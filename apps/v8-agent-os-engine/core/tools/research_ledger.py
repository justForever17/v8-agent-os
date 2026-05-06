from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.v8_agent_os_paths import runtime_private_root


_LOCK = threading.RLock()
_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, list) else []


def _ledger_path() -> Path:
    override = os.getenv("V8_RESEARCH_LEDGER_PATH")
    if override:
        return Path(override).expanduser()
    return runtime_private_root("research") / "research_ledger.json"


def _empty_store() -> dict[str, Any]:
    return {"version": _VERSION, "evidenceBundles": [], "experiencePacks": []}


def _read_store() -> dict[str, Any]:
    path = _ledger_path()
    if not path.exists():
        return _empty_store()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_store()
    if not isinstance(payload, dict):
        return _empty_store()
    payload.setdefault("version", _VERSION)
    payload.setdefault("evidenceBundles", [])
    payload.setdefault("experiencePacks", [])
    if not isinstance(payload["evidenceBundles"], list):
        payload["evidenceBundles"] = []
    if not isinstance(payload["experiencePacks"], list):
        payload["experiencePacks"] = []
    return payload


def _write_store(payload: dict[str, Any]) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _visible(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not str(key).startswith("_")}


def _not_expired(item: dict[str, Any], now: float | None = None) -> bool:
    expires_at = item.get("_expiresAt")
    if not expires_at:
        return True
    try:
        return float(expires_at) >= float(now if now is not None else time.time())
    except Exception:
        return True


def _prune_expired(payload: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    payload["evidenceBundles"] = [item for item in _as_list(payload.get("evidenceBundles")) if isinstance(item, dict) and _not_expired(item, now)]
    payload["experiencePacks"] = [item for item in _as_list(payload.get("experiencePacks")) if isinstance(item, dict)]
    return payload


def _scope_matches(item: dict[str, Any], scope: str) -> bool:
    normalized_scope = _safe_text(scope) or "global"
    return normalized_scope == "global" or _safe_text(item.get("scope")) in {normalized_scope, "global"}


def _question_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[\s,.;:!?()\[\]{}<>/\\|\"'`~，。！？；：、（）【】]+", _safe_text(value).lower())
        if len(token) >= 2
    }


def _source_digest(bundle: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    digest: list[dict[str, Any]] = []
    for item in _as_list(bundle.get("sourceMatrix"))[:limit]:
        if not isinstance(item, dict):
            continue
        digest.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "host": item.get("host"),
                "authorityScore": item.get("authorityScore"),
                "tier": item.get("tier"),
            }
        )
    return digest


def _experience_from_bundle(bundle: dict[str, Any], *, status: str = "draft", title: str = "", tags: list[str] | None = None) -> dict[str, Any]:
    bundle_id = _safe_text(bundle.get("evidenceBundleId")) or f"research_{uuid.uuid4().hex[:12]}"
    source_digest = _source_digest(bundle)
    topic = _safe_text(title) or _safe_text(bundle.get("question"))[:120] or "Research experience"
    pack_id = f"rxp_{uuid.uuid5(uuid.NAMESPACE_URL, bundle_id).hex[:16]}"
    created_at = _utc_now_iso()
    return {
        "experiencePackId": pack_id,
        "status": status,
        "title": topic,
        "query": bundle.get("question"),
        "summary": _safe_text(bundle.get("summary"))[:600],
        "applicability": _safe_text(bundle.get("deliverable") or bundle.get("researchIntent") or "general_research")[:240],
        "confidence": bundle.get("confidence"),
        "authorityScore": bundle.get("authorityScore"),
        "confidenceTimeline": [
            {
                "at": created_at,
                "confidence": bundle.get("confidence"),
                "authorityScore": bundle.get("authorityScore"),
                "evidenceBundleId": bundle_id,
            }
        ],
        "sourceMatrixDigest": source_digest,
        "createdFromBundleId": bundle_id,
        "createdAt": created_at,
        "updatedAt": created_at,
        "lastUsedAt": None,
        "usageCount": 0,
        "tags": list(tags or []),
        "scope": bundle.get("scope") or "global",
    }


def store_evidence_bundle(bundle: dict[str, Any], *, ttl_seconds: int, scope: str) -> dict[str, Any]:
    with _LOCK:
        payload = _prune_expired(_read_store())
        bundle_id = _safe_text(bundle.get("evidenceBundleId")) or f"research_{uuid.uuid4().hex[:12]}"
        created_at = _safe_text(bundle.get("createdAt")) or _utc_now_iso()
        stored = {
            **bundle,
            "evidenceBundleId": bundle_id,
            "scope": scope or "global",
            "createdAt": created_at,
            "retention": "persistent_research_ledger",
            "_expiresAt": time.time() + max(60, int(ttl_seconds or 60)),
        }
        items = [item for item in payload["evidenceBundles"] if _safe_text(item.get("evidenceBundleId")) != bundle_id]
        items.insert(0, stored)
        payload["evidenceBundles"] = items[:500]

        if stored.get("confidence") in {"medium", "high"} and _as_list(stored.get("sourceMatrix")):
            candidate = _experience_from_bundle(stored, status="draft")
            packs = [item for item in payload["experiencePacks"] if _safe_text(item.get("experiencePackId")) != candidate["experiencePackId"]]
            packs.insert(0, candidate)
            payload["experiencePacks"] = packs[:500]

        _write_store(payload)
        return _visible(stored)


def list_evidence_bundles(*, scope: str = "global", limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        payload = _prune_expired(_read_store())
        _write_store(payload)
        items = [_visible(item) for item in payload["evidenceBundles"] if isinstance(item, dict) and _scope_matches(item, scope)]
        return items[: max(1, min(int(limit or 50), 200))]


def get_evidence_bundle(evidence_bundle_id: str) -> dict[str, Any] | None:
    with _LOCK:
        payload = _prune_expired(_read_store())
        target = _safe_text(evidence_bundle_id)
        for item in payload["evidenceBundles"]:
            if _safe_text(item.get("evidenceBundleId")) == target:
                return _visible(item)
    return None


def search_experience_packs(*, query: str, scope: str = "global", tags: list[str] | None = None, min_confidence: str = "", limit: int = 10) -> list[dict[str, Any]]:
    q_tokens = _question_tokens(query)
    tag_set = {str(item).strip().lower() for item in list(tags or []) if str(item).strip()}
    confidence_rank = {"low": 1, "medium": 2, "high": 3}
    min_rank = confidence_rank.get(_safe_text(min_confidence).lower(), 0)
    scored: list[tuple[int, dict[str, Any]]] = []
    with _LOCK:
        payload = _read_store()
        for item in payload["experiencePacks"]:
            if not isinstance(item, dict) or not _scope_matches(item, scope):
                continue
            if _safe_text(item.get("status")).lower() == "archived":
                continue
            if min_rank and confidence_rank.get(_safe_text(item.get("confidence")).lower(), 0) < min_rank:
                continue
            item_tags = {str(tag).strip().lower() for tag in _as_list(item.get("tags")) if str(tag).strip()}
            if tag_set and not tag_set.intersection(item_tags):
                continue
            haystack = " ".join(
                [
                    _safe_text(item.get("title")),
                    _safe_text(item.get("query")),
                    _safe_text(item.get("summary")),
                    _safe_text(item.get("applicability")),
                    " ".join(_safe_text(src.get("host")) for src in _as_list(item.get("sourceMatrixDigest")) if isinstance(src, dict)),
                    " ".join(item_tags),
                ]
            ).lower()
            tokens = _question_tokens(haystack)
            overlap = len(q_tokens.intersection(tokens)) if q_tokens else 0
            score = overlap * 10 + int(float(item.get("authorityScore") or 0) / 10) + int(item.get("usageCount") or 0)
            if q_tokens and overlap == 0:
                continue
            scored.append((score, _visible(item)))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[: max(1, min(int(limit or 10), 50))]]


def search_experience_packs_with_options(
    *,
    query: str,
    scope: str = "global",
    tags: list[str] | None = None,
    min_confidence: str = "",
    limit: int = 10,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    if not include_archived:
        return search_experience_packs(query=query, scope=scope, tags=tags, min_confidence=min_confidence, limit=limit)
    q_tokens = _question_tokens(query)
    tag_set = {str(item).strip().lower() for item in list(tags or []) if str(item).strip()}
    confidence_rank = {"low": 1, "medium": 2, "high": 3}
    min_rank = confidence_rank.get(_safe_text(min_confidence).lower(), 0)
    scored: list[tuple[int, dict[str, Any]]] = []
    with _LOCK:
        payload = _read_store()
        for item in payload["experiencePacks"]:
            if not isinstance(item, dict) or not _scope_matches(item, scope):
                continue
            if min_rank and confidence_rank.get(_safe_text(item.get("confidence")).lower(), 0) < min_rank:
                continue
            item_tags = {str(tag).strip().lower() for tag in _as_list(item.get("tags")) if str(tag).strip()}
            if tag_set and not tag_set.intersection(item_tags):
                continue
            haystack = " ".join(
                [
                    _safe_text(item.get("title")),
                    _safe_text(item.get("query")),
                    _safe_text(item.get("summary")),
                    _safe_text(item.get("applicability")),
                    " ".join(_safe_text(src.get("host")) for src in _as_list(item.get("sourceMatrixDigest")) if isinstance(src, dict)),
                    " ".join(item_tags),
                ]
            ).lower()
            tokens = _question_tokens(haystack)
            overlap = len(q_tokens.intersection(tokens)) if q_tokens else 0
            score = overlap * 10 + int(float(item.get("authorityScore") or 0) / 10) + int(item.get("usageCount") or 0)
            if q_tokens and overlap == 0:
                continue
            scored.append((score, _visible(item)))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[: max(1, min(int(limit or 10), 50))]]


def get_experience_pack(experience_pack_id: str, *, include_archived: bool = False) -> dict[str, Any] | None:
    with _LOCK:
        payload = _read_store()
        target = _safe_text(experience_pack_id)
        changed = False
        for item in payload["experiencePacks"]:
            if _safe_text(item.get("experiencePackId")) == target:
                if _safe_text(item.get("status")).lower() == "archived" and not include_archived:
                    return None
                item["usageCount"] = int(item.get("usageCount") or 0) + 1
                item["lastUsedAt"] = _utc_now_iso()
                changed = True
                if changed:
                    _write_store(payload)
                return _visible(item)
    return None


def promote_experience_pack(evidence_bundle_id: str, *, title: str = "", tags: list[str] | None = None) -> dict[str, Any] | None:
    with _LOCK:
        payload = _prune_expired(_read_store())
        bundle = None
        target_bundle_id = _safe_text(evidence_bundle_id)
        for item in payload["evidenceBundles"]:
            if _safe_text(item.get("evidenceBundleId")) == target_bundle_id:
                bundle = item
                break
        if not bundle:
            return None
        candidate = _experience_from_bundle(bundle, status="active", title=title, tags=tags)
        packs = [item for item in payload["experiencePacks"] if _safe_text(item.get("experiencePackId")) != candidate["experiencePackId"]]
        packs.insert(0, candidate)
        payload["experiencePacks"] = packs[:500]
        _write_store(payload)
        return _visible(candidate)


def archive_experience_pack(experience_pack_id: str, *, initiated_by: str = "admin", reason: str = "") -> dict[str, Any] | None:
    with _LOCK:
        payload = _read_store()
        target = _safe_text(experience_pack_id)
        now = _utc_now_iso()
        for item in payload["experiencePacks"]:
            if _safe_text(item.get("experiencePackId")) == target:
                item["status"] = "archived"
                item["archivedAt"] = now
                item["archivedBy"] = _safe_text(initiated_by) or "admin"
                item["archiveReason"] = _safe_text(reason)
                item["updatedAt"] = now
                _write_store(payload)
                return _visible(item)
    return None


def restore_experience_pack(experience_pack_id: str, *, initiated_by: str = "admin") -> dict[str, Any] | None:
    with _LOCK:
        payload = _read_store()
        target = _safe_text(experience_pack_id)
        now = _utc_now_iso()
        for item in payload["experiencePacks"]:
            if _safe_text(item.get("experiencePackId")) == target:
                item["status"] = "active" if _safe_text(item.get("status")).lower() == "archived" else (_safe_text(item.get("status")) or "active")
                item["restoredAt"] = now
                item["restoredBy"] = _safe_text(initiated_by) or "admin"
                item["updatedAt"] = now
                _write_store(payload)
                return _visible(item)
    return None


def delete_experience_pack(experience_pack_id: str, *, confirm: bool = False) -> bool:
    if not confirm:
        return False
    with _LOCK:
        payload = _read_store()
        target = _safe_text(experience_pack_id)
        before = len(payload["experiencePacks"])
        payload["experiencePacks"] = [
            item for item in payload["experiencePacks"]
            if not (isinstance(item, dict) and _safe_text(item.get("experiencePackId")) == target)
        ]
        changed = len(payload["experiencePacks"]) != before
        if changed:
            _write_store(payload)
        return changed


def list_experience_packs(*, scope: str = "global", limit: int = 50, include_archived: bool = False) -> list[dict[str, Any]]:
    with _LOCK:
        payload = _read_store()
        items = [
            _visible(item)
            for item in payload["experiencePacks"]
            if isinstance(item, dict)
            and _scope_matches(item, scope)
            and (include_archived or _safe_text(item.get("status")).lower() != "archived")
        ]
        return items[: max(1, min(int(limit or 50), 200))]


def research_ledger_summary(*, scope: str = "global", include_archived: bool = False) -> dict[str, Any]:
    bundles = list_evidence_bundles(scope=scope, limit=200)
    packs = list_experience_packs(scope=scope, limit=200, include_archived=include_archived)
    timeline = [
        {
            "at": item.get("createdAt"),
            "question": item.get("question"),
            "confidence": item.get("confidence"),
            "authorityScore": item.get("authorityScore"),
            "evidenceBundleId": item.get("evidenceBundleId"),
        }
        for item in bundles[:30]
    ]
    return {
        "ok": True,
        "scope": scope,
        "counts": {"evidenceBundles": len(bundles), "experiencePacks": len(packs)},
        "evidenceBundles": bundles[:30],
        "experiencePacks": packs[:30],
        "confidenceTimeline": timeline,
    }
