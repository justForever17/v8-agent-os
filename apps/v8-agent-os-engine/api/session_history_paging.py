"""Pagination of the existing materialized navigation snapshot (not a DB index)."""
import base64
import json


class SessionHistoryCursorError(ValueError):
    pass


def page_session_history(payload: dict, *, authority: str, principal: str, query: str = "", cursor: str = "", limit: int = 80) -> dict:
    if not authority or not principal:
        raise SessionHistoryCursorError("session_index_identity_required")
    query = query.strip().casefold()
    version = str(payload.get("generatedAt") or "")
    if not version:
        raise SessionHistoryCursorError("session_index_version_required")
    binding = [authority, principal, query, version]
    after = None
    if cursor:
        try:
            if len(cursor) > 4096:
                raise ValueError()
            decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if not isinstance(decoded, list) or len(decoded) != 6 or decoded[:4] != binding:
                raise ValueError()
            after = (str(decoded[4]), str(decoded[5]))
        except (ValueError, TypeError, UnicodeError) as error:
            raise SessionHistoryCursorError("session_index_changed") from error

    def order(row: dict) -> tuple[str, str]:
        return (str(row.get("historySortAt") or row.get("createdAt") or ""), str(row.get("sessionId") or row.get("id") or ""))

    rows = [row for row in payload.get("sessions", []) if isinstance(row, dict)
            and (not query or query in " ".join(str(row.get(key) or "") for key in ("title", "sessionId", "id", "workspaceDisplayName", "projectName")).casefold())]
    rows.sort(key=order, reverse=True)
    if after is not None:
        rows = [row for row in rows if order(row) < after]
    limit = max(1, min(200, int(limit)))
    selected = rows[:limit]
    next_cursor = None
    if len(rows) > limit and selected:
        next_cursor = base64.urlsafe_b64encode(json.dumps([*binding, *order(selected[-1])], ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
    return {**payload, "sessions": selected, "pageInfo": {"nextCursor": next_cursor, "snapshotVersion": version, "hasMore": bool(next_cursor)}}
