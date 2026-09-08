"""Discovery visibility for the researcher; search snippets are never read evidence."""

from __future__ import annotations

from typing import Any


def acquisition_feedback(shards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose candidates skipped by eager fetching so the agent can choose exact URLs."""
    feedback = []
    for shard in shards:
        reads = {item.get("url"): item for item in shard.get("fetchedTopSources") or []}
        candidates = []
        for item in shard.get("results") or []:
            read = reads.get(item.get("url"))
            candidates.append({
                "url": item.get("url"), "title": item.get("title"),
                "snippet": str(item.get("snippet") or "")[:600],
                "readStatus": ("fetched" if read.get("ok") and not read.get("missingContentReason")
                               else "fetch_failed") if read else "not_fetched",
                "readReason": (read.get("missingContentReason") or read.get("failureClass"))
                              if read else item.get("readSelectionReason", "eager_read_budget_or_ranking"),
            })
        feedback.append({
            "query": shard.get("query"), "provider": shard.get("provider"),
            "elapsedMs": shard.get("elapsedMs"), "errors": shard.get("errors") or [],
            "siteDomains": shard.get("siteDomains") or [], "candidates": candidates,
            "instruction": "Discovery only, not evidence. Select useful URLs for direct fetching, then read their source keys. A skipped candidate is not an unreachable page.",
        })
    return feedback
