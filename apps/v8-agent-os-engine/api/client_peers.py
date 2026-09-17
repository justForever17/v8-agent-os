"""Phone projection of already trusted Supervisor links (never discovery)."""
from fastapi import HTTPException

from core.client_transport import internal_json


def _public(link, instance_id):
    link_id = str(link.get("linkId") or link.get("id") or "")
    return {"linkId": link_id, "peerId": str(link.get("peerId") or ""), "servingInstanceId": instance_id,
            "sessionId": "network_neighbor_" + link_id, "sessionKind": "local_neighbor",
            "displayName": str(link.get("remoteNickname") or link.get("displayName") or link.get("peerId") or ""),
            "online": link.get("online") is True, **{key: str(link.get(key) or "")
            for key in ("lastSeenAt", "localRole", "remoteRole", "trustStatus", "description")}}


async def client_peers(request, principal, path):
    if principal.role.upper() != "ADMIN":
        raise HTTPException(403, "owner_access_required")
    result = await internal_json(request, principal, "/network-supervisor/neighbors/links")
    peers = [_public(row, principal.issuer) for row in result.get("items", [])
             if row.get("trustStatus") == "trusted" and (row.get("linkId") or row.get("id"))]
    parts = path.split("/")
    if len(parts) == 1 and request.method == "GET":
        query = request.query_params.get("q", "").casefold()[:200]
        try:
            offset = max(0, int(request.query_params.get("cursor", "0")))
            limit = max(1, min(100, int(request.query_params.get("limit", "40"))))
        except ValueError:
            raise HTTPException(400, "invalid_peer_cursor")
        peers = sorted([row for row in peers if query in (row["displayName"] + " " + row["peerId"]).casefold()], key=lambda row: row["linkId"])
        return {"servingInstanceId": principal.issuer, "items": peers[offset:offset+limit],
                "nextCursor": str(offset+limit) if offset+limit < len(peers) else None, "total": len(peers)}
    peer = next((row for row in peers if len(parts) > 1 and row["linkId"] == parts[1]), None)
    if peer is None:
        raise HTTPException(404, "supervisor_link_unavailable")
    target = "/network-supervisor/neighbors/" + peer["linkId"]
    if len(parts) == 2 and request.method in {"PATCH", "DELETE"}:
        body = None
        if request.method == "PATCH":
            from api.client_routes import _payload
            body = await _payload(request)
            if not body or set(body) - {"remoteNickname", "localRole"}:
                raise HTTPException(400, "supervisor_settings_invalid")
            if "remoteNickname" in body:
                name = body["remoteNickname"]
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                    raise HTTPException(400, "supervisor_name_invalid")
                body["remoteNickname"] = name.strip()
            if "localRole" in body and body["localRole"] not in {"primary", "companion"}:
                raise HTTPException(400, "supervisor_role_invalid")
        await internal_json(request, principal, target, method=request.method, payload=body)
        return {"ok": True, "linkId": peer["linkId"], "servingInstanceId": principal.issuer}
    if len(parts) == 3 and parts[2] == "timeline" and request.method == "GET":
        query = {"limit": 50}
        for key in ("cursor", "before"):
            value = request.query_params.get(key, "")
            if value and value.isdigit() and len(value) <= 20:
                query[key] = value
        payload = await internal_json(request, principal, target + "/timeline", query=query)
        return {"peer": peer, "nextCursor": payload.get("nextCursor"), "previousCursor": payload.get("previousCursor"),
                "items": [{**{key: row.get(key) for key in ("seq", "body", "direction", "status", "createdAt", "fromNickname")},
                           "id": row.get("id") or row.get("messageId")} for row in payload.get("items", [])]}
    raise HTTPException(404, "client_route_not_found")
