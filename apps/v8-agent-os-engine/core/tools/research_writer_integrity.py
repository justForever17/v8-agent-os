"""Small, non-mutating checks for model-written research text."""

from __future__ import annotations

import re
from typing import Any


def _urls(text: str) -> set[str]:
    return {
        match.rstrip(".,;:!?，。；：！？")
        for match in re.findall(r"https?://[^\s<>\[\]()\"'，。；：！？（）]+", text)
    }


def unbound_section_urls(section: str, task: dict[str, Any]) -> list[str]:
    """Allow read source links and addresses present in the assigned evidence.

    A URL can be the actual fact (a registry, API or download endpoint), not
    redundant citation decoration. Never delete it or adjacent prose. Unknown
    links require writer correction, not silent rewriting of the answer.
    """
    allowed: set[str] = set()
    for claim in task.get("assignedClaims") or []:
        if not isinstance(claim, dict):
            continue
        allowed.update(_urls(str(claim.get("evidenceExcerpt") or "")))
        for source in claim.get("supportingSources") or []:
            if isinstance(source, dict):
                allowed.update(_urls(str(source.get("url") or "")))
    return sorted(_urls(section) - allowed)
