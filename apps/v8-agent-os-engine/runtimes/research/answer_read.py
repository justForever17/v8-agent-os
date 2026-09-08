"""Lossless, network-free paging of the reviewed answer and its boundaries."""
from __future__ import annotations

import hashlib
from typing import Any

from core.research_verification_bindings import research_evidence_bindings
from core.tools.research_quality import (
    research_answer_is_usable, research_answer_text, research_claims, research_independent_review,
    research_selected_sources,
)


def answer_read_tool(bundle_id: str, *, start: int = 0) -> str:
    return f"research_broker(mode='get_evidence', evidenceBundleId={bundle_id!r}, readAnswer=True, startChar={start})"


def answer_preview_proof(answer: str) -> dict[str, Any]:
    return {"answerChars": len(answer), "answerSha256": hashlib.sha256(answer.encode("utf-8")).hexdigest()}


def saved_answer_document(bundle: dict[str, Any]) -> str:
    """Keep the answer verbatim, followed by complete limitations and citations."""
    answer = research_answer_text(bundle)
    review = research_independent_review(bundle)
    final = bundle.get("finalExperiencePack") if isinstance(bundle.get("finalExperiencePack"), dict) else {}
    limitations = (review.get("limitations") or bundle.get("limitations")
                   or final.get("limitations") or (bundle.get("researchAnswerPack") or {}).get("limitations") or [])
    parts = [answer, "## Limitations\n" + ("\n".join(f"- {item}" for item in limitations) or "None recorded.")]
    bindings = research_evidence_bindings({"claimTable": research_claims(bundle)})
    if bindings:
        # Answer prose can abbreviate a reference; only the original ledger
        # binding identifies the observation inherited by another agent.
        parts.append("## Original read observations\n"
                     "Copy claimId exactly; these identify reads, not semantic approval. "
                     "For independent verification open the relevant saved source bodies with "
                     "research_broker(mode='get_evidence', evidenceBundleId="
                     f"{str(bundle.get('evidenceBundleId') or '')!r}, sourceKey='S#', startChar=0). "
                     "Reading this answer alone is not a source check.\n"
                     "| claimId | Citation | Actual URL |\n|---|---|---|\n"
                     + "\n".join(f"| {row['claimId']} | [{row['citationKey']}] | {row['url']} |" for row in bindings))
    sources = []
    for source in research_selected_sources(bundle):
        key = str(source.get('citationKey') or source.get('sourceId') or '?')
        marker = key if key.startswith('[') and key.endswith(']') else f'[{key}]'
        sources.append(f"- {marker} "
                       f"{source.get('title') or ''}: {source.get('url') or source.get('sourceUrl') or ''}")
    parts.append("## Sources\n" + "\n".join(sources))
    return "\n\n".join(parts)


def saved_answer_page(bundle: dict[str, Any], *, start: int, max_chars: int) -> dict[str, Any]:
    bundle_id = str(bundle.get("evidenceBundleId") or "")
    base = {"kind": "research_answer_page", "evidenceBundleId": bundle_id, "snapshotOnly": True,
            "offsetUnit": "unicode_code_points"}
    if not research_answer_is_usable(bundle):
        return {**base, "ok": False, "error": "research_answer_not_accepted"}
    document = saved_answer_document(bundle)
    start = min(max(0, start), len(document))
    end = min(len(document), start + max(100, min(max_chars, 12000)))
    review = research_independent_review(bundle)
    return {**base, "ok": True, "reviewDecision": "accept",
            "deliveryScope": review.get("deliveryScope") or bundle.get("deliveryScope") or "unknown",
            "asOf": bundle.get("asOf"), "answerChars": len(research_answer_text(bundle)),
            "contentChars": len(document), "contentSha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
            "start": start, "end": end, "nextOffset": end if end < len(document) else None,
            "text": document[start:end], "detailTool": answer_read_tool(bundle_id, start=end)}
