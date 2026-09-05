from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def match_source_catalog(url: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer an explicitly scoped path over a domain-wide catalog hint.

    Catalog hints describe the publication surface, not the truth of its
    contents. A vendor can host both its documentation and user contributions.
    Unscoped entries retain their existing first-match order.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if not host or parsed.scheme.lower() not in {"http", "https"}:
        return None
    best: dict[str, Any] | None = None
    best_specificity = -1
    for entry in entries:
        if not any(
            (candidate := str(value or "").strip().lower())
            and (host == candidate or host.endswith(f".{candidate}"))
            for value in entry.get("hosts") or []
        ):
            continue
        specificity = 0
        if "pathPrefixes" in entry:
            # Invalid/empty scoped entries must not become domain-wide trust.
            prefixes = entry["pathPrefixes"]
            if not isinstance(prefixes, list):
                continue
            specificity = max((
                len(prefix)
                for value in prefixes
                if isinstance(value, str)
                and (prefix := value.rstrip("/"))
                and prefix.startswith("/")
                and (parsed.path == prefix or parsed.path.startswith(prefix + "/"))
            ), default=-1)
        if specificity > best_specificity:
            best, best_specificity = entry, specificity
    return best


def scoped_catalog_projection(source: dict[str, Any], entry: dict[str, Any] | None) -> dict[str, Any]:
    """Requalify old domain-wide metadata without editing the stored bundle."""
    if not entry or not entry.get("pathPrefixes"):
        return source
    role = str(entry.get("authorityTier") or "").lower()
    if role not in {"primary", "secondary"}:
        return source
    return {
        **source,
        "tier": role,
        "authorityTier": role,
        "catalogSourceId": entry.get("id"),
        "catalogCategory": entry.get("category"),
    }
