"""Exact provenance checks, not an answer-quality or legal-truth evaluator.

Only an explicitly labelled verification table asserts machine-checkable IDs.
Ordinary prose and unparsed formats remain unassessed, never implicitly verified.
"""

import re
from typing import Any


def research_evidence_bindings(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    if isinstance(payload.get("claimTable"), list):
        for claim in payload["claimTable"]:
            if not isinstance(claim, dict):
                continue
            for source in claim.get("supportingSources") or []:
                if isinstance(source, dict):
                    rows.append({"claimId": str(claim.get("claimId") or "").strip(),
                                 "citationKey": str(source.get("citationKey") or "").strip("[]"),
                                 "url": str(source.get("url") or source.get("sourceUrl") or "").strip()})
    else:
        rows = [dict(row) for row in payload.get("evidenceBindings") or [] if isinstance(row, dict)]
    unique: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        values = tuple(str(row.get(key) or "").strip() for key in ("claimId", "citationKey", "url"))
        if all(values):
            unique[values] = dict(zip(("claimId", "citationKey", "url"), values))
    return list(unique.values())


def audit_research_verification_bindings(text: str, handoffs: list[dict[str, Any]]) -> dict[str, Any]:
    expected: dict[str, dict[str, set[str]]] = {}
    complete = True
    for handoff in handoffs:
        rows = research_evidence_bindings(handoff)
        if not rows:
            continue
        complete = complete and handoff.get("evidenceBindingsComplete", True) is True
        for row in rows:
            expected.setdefault(row["claimId"], {}).setdefault(row["citationKey"], set()).add(row["url"])
    result: dict[str, Any] = {"checkedRows": 0, "matchedClaimIds": [], "mismatches": []}
    if not expected:
        return result
    id_column = None
    matched: set[str] = set()
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            id_column = None
            continue
        cells = [cell.strip().strip("`* ") for cell in line.strip().strip("|").split("|")]
        header = next((index for index, cell in enumerate(cells)
                       if cell.lower() in {"claimid", "readobservationid"}), None)
        if header is not None:
            id_column = header
            continue
        if id_column is None or len(cells) <= id_column or re.fullmatch(r"[-: ]+", cells[id_column]):
            continue
        claim_ids = [value.strip("` ") for value in re.split(r"[,，、;；\s]+", cells[id_column]) if value.strip("` ")]
        keys = set(re.findall(r"\[(S\d+)\]", line))
        urls = {url.rstrip(".,;。；，") for url in re.findall(r"https?://[^\s<>\[\]|`（）()]+", line)}
        if not claim_ids or not keys or not urls:
            continue  # No proof row asserted in the supported format.
        result["checkedRows"] += 1
        unknown = [value for value in claim_ids if value not in expected]
        if unknown:
            if complete:
                result["mismatches"].extend(f"unknown_claim_id:{value}" for value in unknown)
            continue  # A truncated index is not authority to reject omitted IDs.
        allowed = {key: set().union(*(expected[cid].get(key, set()) for cid in claim_ids)) for key in keys}
        if any(not value for value in allowed.values()) or urls != set().union(*allowed.values()):
            result["mismatches"].append(f"citation_url_binding_mismatch:{','.join(claim_ids)}")
        else:
            matched.update(claim_ids)
    result["matchedClaimIds"] = sorted(matched)
    result["mismatches"] = sorted(set(result["mismatches"]))
    return result
