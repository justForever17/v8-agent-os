from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from core.tools.research_readability import has_readable_table, is_site_chrome_excerpt
from core.tools.research_source_identity import research_source_is_navigation, source_attribution_role


CANONICAL_CLAIM_PLAN_VERSION = "v8.research_claim_plan.v1"

_NORMATIVE_CUE_RE = re.compile(
    r"\b(?:recommended|required|recommends?|requires?|prefer(?:s|red)?|"
    r"best practice|should|must|ought|avoid|do not|never)\b|"
    r"(?:官方.{0,8}(?:推荐|要求)|推荐采用|建议(?:使用|将)?|首选|最佳实践|"
    r"应当|不应|必须|不得)",
    re.IGNORECASE,
)
_NEGATIVE_NORMATIVE_CUE_RE = re.compile(
    r"\b(?:never|avoid|forbid(?:s|den)?|prohibit(?:s|ed)?|must\s+not|"
    r"should\s+not|shall\s+not|may\s+not|do\s+not)\b|"
    r"(?:\u4e0d(?:\u5e94|\u5f97|\u8be5|\u5efa\u8bae|\u63a8\u8350)|\u7981\u6b62|\u907f\u514d)",
    re.IGNORECASE,
)


class CanonicalClaimPlanError(ValueError):
    def __init__(self, code: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _compact_text(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", _safe_text(value)).strip(" `\t\r\n-*")
    if len(text) <= limit:
        return text
    bounded = text[:limit]
    boundary = max(
        bounded.rfind("。"),
        bounded.rfind("！"),
        bounded.rfind("？"),
        bounded.rfind("."),
        bounded.rfind("!"),
        bounded.rfind("?"),
        bounded.rfind(";"),
        bounded.rfind("；"),
    )
    if boundary >= max(40, limit // 2):
        return bounded[: boundary + 1].strip()
    word_boundary = bounded.rfind(" ")
    return bounded[:word_boundary].strip() if word_boundary >= max(40, limit // 2) else bounded.strip()


def _normalized_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _safe_text(value).casefold())


def _material_signature(value: Any) -> str:
    return _normalized_text(value)


def _signature_ngrams(value: str, *, size: int = 4) -> set[str]:
    if len(value) < size:
        return set()
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _materially_duplicates(value: str, accepted: list[tuple[str, set[str]]]) -> bool:
    signature = _material_signature(value)
    grams = _signature_ngrams(signature)
    if not signature:
        return True
    for existing, existing_grams in accepted:
        if signature == existing:
            return True
        if re.findall(r"\d+", signature) != re.findall(r"\d+", existing):
            # Dates, versions, thresholds and conflicting numeric values are
            # material evidence, not interchangeable formatting noise.
            continue
        union = grams | existing_grams
        similarity = len(grams & existing_grams) / len(union) if union else 0.0
        if min(len(signature), len(existing)) >= 20 and similarity >= 0.9:
            return True
    accepted.append((signature, grams))
    return False


def question_requires_structure(question: str) -> bool:
    """Detect an explicit analytical deliverable, not just recommendations."""
    return bool(re.search(
        r"\b(?:best practices?|recommend(?:ation|ed|s|ing)?|should|selection|trade-?offs?|"
        r"compar(?:e|ison|ative)|relationships?|checklists?|interactions?)\b|"
        r"(?:最佳实践|建议|应该|应当|选型|取舍|对比|比较|适用关系|相互关系|之间的关系|检查清单)",
        question,
        re.IGNORECASE,
    ))


def question_requests_primary_sources(question: str) -> bool:
    return bool(re.search(
        r"\b(?:official|primary|first[- ]party|authoritative)\s+(?:sources?|documentation|docs|evidence)\b|"
        r"(?:官方|一手|权威)(?:来源|资料|文档|证据|文本|原文)",
        question,
        re.IGNORECASE,
    ))


def _navigation_excerpt(text: str) -> bool:
    # Flattened news/related-link lists can repeat the query many times but
    # contain no complete assertion. Do not spend a mandatory source slot on
    # such a list merely because its operative paragraphs duplicate a source
    # already selected. Preserve normal prose and ordinary single link titles.
    without_ellipsis = re.sub(r"\.{2,}|…+", "", text)
    return bool(
        len(re.findall(r"\s(?:-|\|)\s", text)) >= 4
        and not re.search(r"。|(?<!\d)\.(?:\s|$)", without_ellipsis)
        and not has_readable_table(text)
    )


def _claim_text(excerpt: str) -> str:
    # The reader already bounds each exact excerpt to 600 characters. A second
    # keyword-based sentence selector discarded conditions and exceptions,
    # often retaining an introduction instead of the operative clause. Keep
    # that evidence unit intact; prose synthesis belongs to the writer.
    text = re.sub(r"```[^\n]*\n?", "", _safe_text(excerpt)).replace("```", "")
    return re.sub(r"\s+", " ", text).strip(" `\t\r\n-*")


def _normative_cue(claim: str) -> str:
    match = _NEGATIVE_NORMATIVE_CUE_RE.search(claim) or _NORMATIVE_CUE_RE.search(claim)
    if match is None:
        return ""
    cue = match.group(0)
    if len(cue) >= 3:
        return cue
    # Some CJK legal cues are only two characters. Bind a short surrounding
    # phrase so the downstream exact-substring gate
    # receives a useful, source-verifiable cue rather than weakening its
    # minimum-length contract.
    start = max(0, match.start() - 4)
    end = min(len(claim), match.end() + 8)
    expanded = claim[start:end].strip(" ,.;:!?()[]{}\"'\u3002\uff0c\uff1b\uff1a\uff01\uff1f")
    return expanded if len(expanded) >= 3 else ""


def _source_role(source: dict[str, Any]) -> str:
    return source_attribution_role(source)


def _support_projection(source: dict[str, Any], facets: list[str], goal: str) -> dict[str, Any]:
    keys = (
        "sourceId",
        "citationKey",
        "title",
        "url",
        "tier",
        "authorityTier",
        "authorityScore",
        "retrievedAt",
        "publishedAt",
        "updatedAt",
        "sourceDate",
        "sourceDateKind",
        "version",
        "temporalEvidence",
        "readEvidence",
        "subjectFocused",
    )
    result = {
        key: source.get(key)
        for key in keys
        if source.get(key) not in (None, "", [], {})
    }
    result["sourceRole"] = _source_role(source)
    if facets:
        result["researchFacetIds"] = facets
        result["researchFacetId"] = facets[0]
    if goal:
        result["researchFacetGoal"] = goal
        result["evidenceQuery"] = goal
    return result


def _claim_row(source: dict[str, Any], candidate: dict[str, Any], source_index: int, candidate_index: int) -> dict[str, Any] | None:
    citation_key = _safe_text(source.get("citationKey")).strip("[]")
    excerpt_key = _safe_text(candidate.get("evidenceExcerptKey"))
    excerpt = _safe_text(candidate.get("text"))
    if not citation_key or not excerpt_key or len(excerpt) > 600 or len(_normalized_text(excerpt)) < 20:
        return None
    if _navigation_excerpt(excerpt):
        return None
    source_text = _safe_text(source.get("text") or source.get("evidenceText"))
    if not source_text or _normalized_text(excerpt) not in _normalized_text(source_text):
        return None
    candidate_facets = list(
        dict.fromkeys(
            _safe_text(value)
            for value in (
                candidate.get("researchFacetId"),
                *list(candidate.get("researchFacetIds") or []),
            )
            if _safe_text(value)
        )
    )
    facets = candidate_facets or list(
        dict.fromkeys(
            _safe_text(value)
            for value in (
                source.get("researchFacetId"),
                *list(source.get("researchFacetIds") or []),
            )
            if _safe_text(value)
        )
    )
    goal = _safe_text(candidate.get("researchFacetGoal") or source.get("researchFacetGoal") or source.get("evidenceQuery"))
    claim = _claim_text(excerpt)
    if len(_normalized_text(claim)) < 20:
        return None
    normative_cue = _normative_cue(claim)
    claim_type = "explicit_normative" if normative_cue else "source_fact"
    digest = hashlib.sha256(
        f"{citation_key}\n{excerpt_key}\n{_normalized_text(claim)}".encode("utf-8", errors="ignore")
    ).hexdigest()
    support = _support_projection(source, facets, goal)
    return {
        "claimId": f"claim_{digest[:16]}",
        "claim": claim,
        "claimType": claim_type,
        **({"normativeCue": normative_cue} if normative_cue else {}),
        "supportingSources": [support],
        "refutingSources": [],
        "confidence": _safe_text(source.get("tier")) or "secondary",
        "evidenceExcerptKey": excerpt_key,
        "evidenceExcerpt": excerpt,
        "evidenceExcerptSha256": hashlib.sha256(excerpt.encode("utf-8", errors="ignore")).hexdigest(),
        "evidenceVerified": True,
        "researchFacetIds": facets,
        **({"researchFacetId": facets[0]} if facets else {}),
        **({"researchFacetGoal": goal} if goal else {}),
        "_sourceIndex": source_index,
        "_candidateIndex": candidate_index,
        "_relevanceScore": int(candidate.get("relevanceScore") or 0),
        "_citationKey": citation_key,
    }


def _public_claim(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _deterministic_outline(claims: list[dict[str, Any]], *, max_sections: int = 4) -> list[dict[str, Any]]:
    if not claims:
        return []
    target_sections = max(2, min(max_sections, len(claims))) if len(claims) > 1 else 1
    groups: list[list[dict[str, Any]]] = [[] for _ in range(target_sections)]
    for index, claim in enumerate(claims):
        groups[index % target_sections].append(claim)
    outline: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        first = group[0]
        goal = _safe_text(first.get("researchFacetGoal"))
        support = next(
            (item for item in list(first.get("supportingSources") or []) if isinstance(item, dict)),
            {},
        )
        title = _compact_text(goal or support.get("title") or f"Evidence section {index}", limit=120)
        outline.append(
            {
                "sectionId": f"section_{index}",
                "title": title,
                "objective": "Explain only the assigned Runtime-verified claims and their evidence boundaries.",
                "claimIds": [str(item["claimId"]) for item in group],
            }
        )
    return outline


def build_canonical_claim_plan(
    *,
    question: str,
    sources: list[dict[str, Any]],
    required_source_keys: list[str],
    required_facet_ids: list[str],
    minimum_source_count: int,
    minimum_claim_count: int,
    target_claim_count: int,
    allow_supported_scope: bool = False,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen_excerpt_keys: set[str] = set()
    seen_claims: list[tuple[str, set[str]]] = []
    for source_index, source in enumerate(sources):
        if not isinstance(source, dict):
            continue
        # Acquisition facet labels are routing provenance, not semantic proof.
        # The reader's explicit off-topic decision must survive canonicalization
        # even when this source would fill an otherwise missing facet.
        if source.get("subjectFocused") is False or research_source_is_navigation(
            source.get("url"), title=source.get("title")
        ):
            continue
        for candidate_index, candidate in enumerate(list(source.get("evidenceCandidates") or [])):
            if not isinstance(candidate, dict):
                continue
            if is_site_chrome_excerpt(_safe_text(candidate.get("text")), question=question):
                continue
            if "relevanceScore" in candidate and int(candidate.get("relevanceScore") or 0) <= 0:
                continue
            row = _claim_row(source, candidate, source_index, candidate_index)
            if row is None:
                continue
            excerpt_key = str(row["evidenceExcerptKey"])
            if excerpt_key in seen_excerpt_keys or _materially_duplicates(
                _safe_text(row.get("claim")),
                seen_claims,
            ):
                continue
            seen_excerpt_keys.add(excerpt_key)
            rows.append(row)

    required_sources = list(
        dict.fromkeys(_safe_text(value).strip("[]") for value in required_source_keys if _safe_text(value))
    )
    required_facets = list(dict.fromkeys(_safe_text(value) for value in required_facet_ids if _safe_text(value)))
    facet_goals: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_goal = _safe_text(
            source.get("researchFacetGoal") or source.get("evidenceQuery")
        )
        for facet_id in [
            *list(source.get("researchFacetIds") or []),
            source.get("researchFacetId"),
        ]:
            normalized_facet_id = _safe_text(facet_id)
            if normalized_facet_id and source_goal:
                facet_goals.setdefault(normalized_facet_id, source_goal)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add_best(candidates: list[dict[str, Any]]) -> bool:
        available = [item for item in candidates if item["claimId"] not in selected_ids]
        if not available:
            return False
        covered_sources = {str(item["_citationKey"]) for item in selected}
        chosen = max(
            available,
            key=lambda item: (
                # A facet reservation must not consume a separate commentary
                # slot when a required source already covers that same facet.
                1 if item["_citationKey"] in required_sources else 0,
                1 if item["_citationKey"] not in covered_sources else 0,
                # The reader already ranked exact operative excerpts (with
                # navigation penalties and clause anchors). Public relevance
                # is only a coarse 0-100 display metric, not that ordering.
                -int(item["_candidateIndex"]),
                int(item["_relevanceScore"]),
                -int(item["_sourceIndex"]),
            ),
        )
        selected.append(chosen)
        selected_ids.add(str(chosen["claimId"]))
        return True

    for facet_id in required_facets:
        if any(facet_id in list(item.get("researchFacetIds") or []) for item in selected):
            continue
        add_best([item for item in rows if facet_id in list(item.get("researchFacetIds") or [])])
    for citation_key in required_sources:
        if any(item["_citationKey"] == citation_key for item in selected):
            continue
        add_best([item for item in rows if item["_citationKey"] == citation_key])

    desired_claim_count = max(int(minimum_claim_count or 0), int(target_claim_count or 0))
    # Deepen the selected evidence before adding new documents merely to fill
    # a claim budget. Facet reservations already retain necessary extra sources.
    ordered_sources = list(dict.fromkeys([
        *required_sources,
        *[str(item["_citationKey"]) for item in selected],
    ]))
    for row in rows:
        if len(ordered_sources) >= max(1, int(minimum_source_count or 0)):
            break
        key = str(row["_citationKey"])
        if key not in ordered_sources:
            ordered_sources.append(key)
    named_titles = [
        _normalized_text(title)
        for title in re.findall(r"《([^《》\n]{4,120})》", question)
    ]
    named_sources = [
        _safe_text(source.get("citationKey")).strip("[]")
        for source in sources
        if any(title in _normalized_text(source.get("title")) for title in named_titles)
        and _safe_text(source.get("citationKey")).strip("[]") in ordered_sources
    ]
    # Source/facet reservations above remain mandatory. Spend the remaining
    # depth on explicitly requested documents before general background; this
    # is relevance, not an upgrade of the document's provenance or authority.
    for source_group in ([named_sources, ordered_sources] if named_sources else [ordered_sources]):
        while len(selected) < desired_claim_count:
            added = False
            for citation_key in source_group:
                if len(selected) >= desired_claim_count:
                    break
                added = add_best([item for item in rows if item["_citationKey"] == citation_key]) or added
            if not added:
                break

    public_claims = [_public_claim(item) for item in selected]
    covered_sources = list(dict.fromkeys(str(item["_citationKey"]) for item in selected))
    covered_facets = list(
        dict.fromkeys(
            facet_id
            for item in selected
            for facet_id in list(item.get("researchFacetIds") or [])
            if _safe_text(facet_id)
        )
    )
    missing_sources = [item for item in required_sources if item not in covered_sources]
    missing_facets = [item for item in required_facets if item not in covered_facets]
    blocked_facets = [
        {
            "facetId": facet_id,
            **({"goal": facet_goals[facet_id]} if facet_goals.get(facet_id) else {}),
        }
        for facet_id in missing_facets
    ]
    diagnostics = {
        "version": CANONICAL_CLAIM_PLAN_VERSION,
        "mode": "runtime_canonical",
        "candidateCount": len(rows),
        "claimCount": len(public_claims),
        "sourceCount": len(covered_sources),
        "requiredSourceCount": max(int(minimum_source_count or 0), len(required_sources)),
        "requiredClaimCount": int(minimum_claim_count or 0),
        "targetClaimCount": desired_claim_count,
        "requiredFacetIds": required_facets,
        "coveredFacetIds": covered_facets,
        "missingSourceKeys": missing_sources,
        "missingFacetIds": missing_facets,
        "blockedFacets": blocked_facets,
    }
    minimum_floor_met = bool(
        len(covered_sources) >= int(minimum_source_count or 0)
        and len(public_claims) >= int(minimum_claim_count or 0)
    )
    coverage_complete = not missing_sources and not missing_facets
    diagnostics["minimumFloorMet"] = minimum_floor_met
    diagnostics["coverageComplete"] = coverage_complete
    diagnostics["supportedScopeLimited"] = bool(
        allow_supported_scope and minimum_floor_met and not coverage_complete
    )
    if not minimum_floor_met or (not allow_supported_scope and not coverage_complete):
        raise CanonicalClaimPlanError("canonical_claim_plan_incomplete", diagnostics)

    outline = _deterministic_outline(public_claims)
    digest = hashlib.sha256(
        json.dumps(public_claims, ensure_ascii=False, sort_keys=True).encode("utf-8", errors="ignore")
    ).hexdigest()
    diagnostics["claimDigest"] = digest
    missing_evidence = [
        "No exact verified claim was retained for planned research facet: "
        f"{item.get('goal') or item['facetId']}."
        for item in blocked_facets
    ]
    missing_evidence.extend(
        f"No exact verified claim was retained from target source: [{source_key}]."
        for source_key in missing_sources
    )
    return {
        "reviewDecision": "accept",
        "reviewReasons": [],
        # This internal plan is not a user-facing verdict. The answer assembler
        # supplies a localized neutral heading if the writer has not provided one.
        "headline": "",
        "claimTable": public_claims,
        "answerOutline": outline,
        "compositeInferences": [],
        "conflictMatrix": [],
        "missingEvidence": missing_evidence,
        "blockedFacets": blocked_facets,
        "blockedSourceKeys": missing_sources,
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "assumptions": [],
        "canonicalClaimPlan": diagnostics,
    }


def structure_material(plan: dict[str, Any]) -> dict[str, Any]:
    claims = [item for item in list(plan.get("claimTable") or []) if isinstance(item, dict)]
    # The projector groups facts and proposes inferences. Removing the page
    # identity here made draft clauses and authored recommendations look like
    # enacted obligations even though the canonical supports retained it.
    citation_index: dict[str, dict[str, Any]] = {}
    for claim in claims:
        for source in list(claim.get("supportingSources") or []):
            if not isinstance(source, dict):
                continue
            key = _safe_text(source.get("citationKey") or source.get("citation")).strip("[]")
            if key and key not in citation_index:
                citation_index[key] = {
                    "citationKey": key,
                    **{
                        field: source[field]
                        for field in (
                            "sourceId", "title", "url", "tier", "sourceRole", "version",
                            "publishedAt", "updatedAt", "sourceDate", "sourceDateKind",
                        )
                        if source.get(field) not in (None, "")
                    },
                }
    return {
        "canonicalClaimPlan": plan.get("canonicalClaimPlan") or {},
        "citationIndex": list(citation_index.values()),
        "claims": [
            {
                "claimId": item.get("claimId"),
                "claim": item.get("claim"),
                "claimType": item.get("claimType"),
                "citationKeys": [
                    _safe_text(source.get("citationKey") or source.get("citation")).strip("[]")
                    for source in list(item.get("supportingSources") or [])
                    if isinstance(source, dict) and _safe_text(source.get("citationKey") or source.get("citation"))
                ],
                "researchFacetIds": list(item.get("researchFacetIds") or []),
            }
            for item in claims
        ],
        "deterministicOutline": plan.get("answerOutline") or [],
    }


def supported_scope_limitation_markdown(
    plan: dict[str, Any],
    *,
    preferred_language: str = "",
) -> str:
    """Render the Runtime-owned boundary for facets without verified claims.

    This text never fills an evidence gap.  It makes the omission explicit so
    a writer/reviewer can deliver the supported scope without silently
    promoting a planned-but-unverified claim.
    """

    blocked_facets = [
        item
        for item in list(plan.get("blockedFacets") or [])
        if isinstance(item, dict) and _safe_text(item.get("facetId"))
    ]
    if not blocked_facets:
        return ""
    chinese = _safe_text(preferred_language).lower().startswith("zh")
    heading = "## 本轮证据限制" if chinese else "## Evidence limitations"
    lines = [heading]
    for item in blocked_facets:
        label = _safe_text(item.get("goal")) or _safe_text(item.get("facetId")).replace("-", " ")
        if chinese:
            lines.append(
                f"- {label}：本轮没有形成可逐字核验且绑定来源的断言，正文未对此作结论；需要继续补查。"
            )
        else:
            lines.append(
                f"- {label}: this run did not retain an exact-excerpt-verified, source-bound claim, "
                "so the answer does not state a conclusion for it; further research is required."
            )
    return "\n".join(lines)


def apply_structure_projection(
    plan: dict[str, Any],
    projection: Any,
) -> tuple[dict[str, Any], list[str]]:
    base = dict(plan)
    claims = [item for item in list(base.get("claimTable") or []) if isinstance(item, dict)]
    claim_ids = {
        _safe_text(item.get("claimId"))
        for item in claims
        if _safe_text(item.get("claimId"))
    }
    issues: list[str] = []
    if not isinstance(projection, dict):
        return base, ["structure_projection_not_object"]

    raw_outline = projection.get("answerOutline")
    accepted_outline: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(raw_outline, list) and 2 <= len(raw_outline) <= 6:
        for index, raw_section in enumerate(raw_outline, start=1):
            if not isinstance(raw_section, dict):
                issues.append(f"structure_section_{index}_invalid")
                break
            section_claim_ids = [
                _safe_text(value)
                for value in list(raw_section.get("claimIds") or [])
                if _safe_text(value)
            ]
            if (
                not section_claim_ids
                or len(section_claim_ids) != len(set(section_claim_ids))
                or any(value not in claim_ids or value in seen for value in section_claim_ids)
            ):
                issues.append(f"structure_section_{index}_claim_binding_invalid")
                break
            seen.update(section_claim_ids)
            accepted_outline.append(
                {
                    "sectionId": _safe_text(raw_section.get("sectionId")) or f"section_{index}",
                    "title": _compact_text(raw_section.get("title") or f"Evidence section {index}", limit=120),
                    "objective": _compact_text(
                        raw_section.get("objective") or "Explain the assigned Runtime-verified claims.",
                        limit=240,
                    ),
                    "claimIds": section_claim_ids,
                }
            )
    else:
        issues.append("structure_outline_invalid")
    if issues or seen != claim_ids:
        if not issues:
            issues.append("structure_outline_incomplete")
        accepted_outline = list(base.get("answerOutline") or [])

    section_claim_sets = [set(item.get("claimIds") or []) for item in accepted_outline]
    accepted_inferences: list[dict[str, Any]] = []
    for index, raw_inference in enumerate(list(projection.get("compositeInferences") or [])[:12], start=1):
        if not isinstance(raw_inference, dict):
            issues.append(f"structure_inference_{index}_invalid")
            continue
        premise_ids = list(
            dict.fromkeys(
                _safe_text(value)
                for value in list(raw_inference.get("premiseClaimIds") or [])
                if _safe_text(value)
            )
        )
        inference = _compact_text(
            raw_inference.get("inference")
            or raw_inference.get("conclusion")
            or raw_inference.get("recommendation"),
            limit=360,
        )
        if (
            len(inference) < 20
            or not premise_ids
            or not set(premise_ids).issubset(claim_ids)
            or not any(set(premise_ids).issubset(section) for section in section_claim_sets)
        ):
            issues.append(f"structure_inference_{index}_binding_invalid")
            continue
        accepted_inferences.append(
            {
                "inferenceId": _safe_text(raw_inference.get("inferenceId")) or f"inference_{index}",
                "inference": inference,
                "premiseClaimIds": premise_ids,
            }
        )

    base["answerOutline"] = accepted_outline
    base["compositeInferences"] = accepted_inferences
    return base, list(dict.fromkeys(issues))
