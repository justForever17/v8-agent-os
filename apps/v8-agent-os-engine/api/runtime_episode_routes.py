from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Query

from core.database import db


router = APIRouter(prefix="/runtime-episodes", tags=["runtime-episodes"])


def _safe_limit(value: int | None, *, default: int = 80, maximum: int = 300) -> int:
    try:
        number = int(value or default)
    except Exception:
        number = default
    return max(1, min(number, maximum))


def _summarize(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(Counter(str(item.get(key) or "unknown") for item in items))


@router.get("/overview")
async def runtime_episode_overview(
    limit: int = Query(80, ge=1, le=300),
    active_only: bool = Query(False, alias="activeOnly"),
):
    resolved_limit = _safe_limit(limit)
    episodes = db.list_runtime_episodes(active_only=active_only, limit=resolved_limit)
    queue = db.list_runtime_episode_queue(active_only=active_only, limit=resolved_limit)
    leases = db.list_runtime_episode_leases(active_only=active_only, limit=resolved_limit)
    handoffs: list[dict[str, Any]] = []
    for episode in episodes[:40]:
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            continue
        handoffs.extend(db.list_runtime_episode_handoffs(episode_id)[-3:])
    return {
        "ok": True,
        "summary": {
            "episodeCount": len(episodes),
            "queueCount": len(queue),
            "activeLeaseCount": len([item for item in leases if str(item.get("state") or "") == "active"]),
            "byState": _summarize(episodes, "state"),
            "byKind": _summarize(episodes, "kind"),
            "byTargetKind": _summarize(episodes, "targetKind"),
        },
        "episodes": episodes,
        "queue": queue,
        "leases": leases,
        "handoffs": handoffs[-120:],
    }
