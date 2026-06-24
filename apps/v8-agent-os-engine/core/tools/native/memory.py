from __future__ import annotations

import json
import re
import sys
from typing import Any, Optional

from langchain_core.tools import tool

from core.database import db

__all__ = [
    "_get_memory_runtime",
    "_memory_broker_clamp_limit",
    "_memory_broker_preview",
    "_memory_broker_score",
    "_memory_broker_compact_recall_item",
    "_memory_broker_response",
    "_memory_broker_catalog",
    "_memory_evidence_pack",
    "_memory_query_tokens",
    "_memory_route_domains",
    "_memory_route_research_experience",
    "_memory_route_workflow",
    "_memory_route_engineering_proof",
    "_memory_route_core",
    "memory_broker",
    "memory_recall",
    "mem_delete",
    "mem_update",
    "mem_summary",
    "memory_map",
    "memory_map_expand",
    "memory_read_day",
]


def _compat_native_attr(name: str, local: Any) -> Any:
    native_module = sys.modules.get("core.native_tools")
    if native_module is None:
        return local
    patched = getattr(native_module, name, local)
    if patched is not local:
        return patched
    return local


def _get_memory_runtime():
    patched = _compat_native_attr("_get_memory_runtime", _get_memory_runtime)
    if patched is not _get_memory_runtime:
        return patched()
    from runtimes.memory.runtime import memory_runtime

    return memory_runtime


def _memory_broker_clamp_limit(limit: int | None, *, default: int = 5, maximum: int = 12) -> int:
    try:
        value = int(limit or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _memory_broker_preview(value: Any, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _memory_broker_score(item: dict[str, Any]) -> float | None:
    for key in ("final_relevance_score", "relevance_score", "raw_relevance_score", "effectiveConfidence", "effective_confidence", "confidence"):
        value = item.get(key)
        if value in (None, ""):
            continue
        try:
            return round(max(0.0, min(float(value), 1.0)), 3)
        except (TypeError, ValueError):
            continue
    return None


def _memory_broker_compact_recall_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": item.get("id") or item.get("memoryRef") or item.get("memory_ref"),
        "scope": item.get("scope"),
        "category": item.get("category") or item.get("source"),
        "confidence": _memory_broker_score(item),
        "updatedAt": item.get("updated_at") or item.get("updatedAt"),
        "whyMatched": item.get("match_reason") or item.get("source"),
        "text": _memory_broker_preview(item.get("text") or item.get("fact") or item.get("summary") or item.get("content")),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _memory_broker_response(**payload: Any) -> str:
    compact = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
    return json.dumps(compact, ensure_ascii=False)


def _memory_broker_catalog(scope: Optional[str] = None) -> list[dict[str, Any]]:
    requested_scope = str(scope or "current_session").strip() or "current_session"
    return [
        {
            "domain": "memory_core",
            "label": "Memory Core",
            "purpose": "用户偏好、项目历史、长期知识条目与普通 RAG 召回。",
            "bestFor": ["用户之前说过什么", "项目偏好", "跨轮上下文", "知识条目"],
            "scope": requested_scope,
            "autoInjected": True,
            "activeQuery": True,
            "nextAction": "memory_broker(mode='route' 或 mode='recall')",
        },
        {
            "domain": "daily_log",
            "label": "Daily Log",
            "purpose": "按日期查看历史会话/工作摘要，适合恢复某天上下文。",
            "bestFor": ["某天发生了什么", "按日期追溯"],
            "scope": requested_scope,
            "autoInjected": False,
            "activeQuery": True,
            "nextAction": "memory_broker(mode='map'/'read_day')",
        },
        {
            "domain": "knowledge_graph",
            "label": "Knowledge Graph",
            "purpose": "实体关系、项目/人物/概念之间的轻量关系线索。",
            "bestFor": ["实体关系", "谁和什么相关", "概念邻居"],
            "scope": requested_scope,
            "autoInjected": False,
            "activeQuery": True,
            "nextAction": "memory_broker(mode='graph_search'/'graph_neighbors')",
        },
        {
            "domain": "research_experience",
            "label": "Research Experience",
            "purpose": "Research Runtime 产出的可复用经验包与证据结论。",
            "bestFor": ["之前调研过吗", "经验包能不能复用", "来源支撑的结论"],
            "scope": requested_scope,
            "autoInjected": False,
            "activeQuery": True,
            "nextAction": "memory_broker(mode='route', intent='research_experience') 或 research_broker(mode='search_experience')",
        },
        {
            "domain": "workflow_memory",
            "label": "Workflow Memory",
            "purpose": "已验证流程、工程修复路径、成功步骤与反模式提示。",
            "bestFor": ["以前类似 bug 怎么修", "成功步骤", "工作流提示"],
            "scope": requested_scope,
            "autoInjected": True,
            "activeQuery": True,
            "nextAction": "memory_broker(mode='route', intent='workflow')",
        },
        {
            "domain": "engineering_proof",
            "label": "Engineering Proof",
            "purpose": "Engineering Runtime 的 proof / workset / verification 摘要。",
            "bestFor": ["以前验证过什么", "变更证明", "工程续接依据"],
            "scope": requested_scope,
            "autoInjected": False,
            "activeQuery": True,
            "nextAction": "memory_broker(mode='route', intent='engineering_proof')",
        },
        {
            "domain": "uploads_artifacts",
            "label": "Uploads / Artifacts",
            "purpose": "上传文件与产物引用。v1 只提示入口，不做跨账本深查。",
            "bestFor": ["上传文件", "生成产物", "artifact ref"],
            "scope": requested_scope,
            "autoInjected": False,
            "activeQuery": False,
            "unavailableReason": "Use artifact/upload specific broker or current session attachments.",
        },
        {
            "domain": "skill_inventory",
            "label": "Skill Inventory",
            "purpose": "技能清单、workspace skill 与 skill 说明读取入口。",
            "bestFor": ["有哪些 skill", "某个 skill 怎么用"],
            "scope": requested_scope,
            "autoInjected": False,
            "activeQuery": False,
            "nextAction": "Use fetch_skill_instructions / extension skill inventory tools.",
        },
        {
            "domain": "creative_rpa_computer_traces",
            "label": "Runtime Ledgers",
            "purpose": "Creative Media / RPA / Computer Use 等专属账本。v1 只列入口和限制。",
            "bestFor": ["媒体任务记录", "RPA trace", "桌面操作轨迹"],
            "scope": requested_scope,
            "autoInjected": False,
            "activeQuery": False,
            "unavailableReason": "Use the runtime-specific broker or Admin diagnostics for deep inspection.",
        },
    ]


def _memory_evidence_pack(
    *,
    source_domain: str,
    selected: list[dict[str, Any]] | None = None,
    rejected: list[dict[str, Any]] | None = None,
    why_selected: str = "",
    scope: Optional[str] = None,
    freshness: str = "",
    confidence: Any = None,
    source_refs: list[str] | None = None,
    missing_or_stale: list[str] | None = None,
    recommended_next_action: str = "",
) -> dict[str, Any]:
    selected_items = list(selected or [])[:3]
    rejected_items = list(rejected or [])[:3]
    payload = {
        "sourceDomain": source_domain,
        "selectedEvidence": selected_items,
        "rejectedEvidence": rejected_items,
        "whySelected": why_selected,
        "scope": scope,
        "freshness": freshness,
        "confidence": confidence,
        "sourceRefs": list(source_refs or [])[:6],
        "missingOrStaleReasons": list(missing_or_stale or [])[:6],
        "recommendedNextAction": recommended_next_action,
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _memory_query_tokens(value: Any) -> set[str]:
    text = re.sub(r"\s+", " ", str(value or "").lower())
    tokens = {token for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", text) if token}
    return tokens


def _memory_route_domains(query: str, intent: str = "") -> list[str]:
    text = f"{query} {intent}".lower()
    domains: list[str] = []

    def add(domain: str) -> None:
        if domain not in domains:
            domains.append(domain)

    research_terms = ("调研", "研究", "来源", "证据", "经验包", "research", "source", "evidence", "experience")
    workflow_terms = ("以前怎么修", "类似 bug", "工作流", "成功步骤", "workflow", "proof", "验证", "修过", "debug", "报错")
    graph_terms = ("关系", "关联", "实体", "知识图谱", "graph", "relation")
    daily_terms = ("哪天", "某天", "日记", "日志", "daily", "timeline")
    if any(term in text for term in research_terms):
        add("research_experience")
    if any(term in text for term in workflow_terms):
        add("workflow_memory")
        add("engineering_proof")
    if any(term in text for term in graph_terms):
        add("knowledge_graph")
    if any(term in text for term in daily_terms):
        add("daily_log")
    add("memory_core")
    return domains[:3]


def _memory_experience_confidence_rank(value: Any) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(str(value or "").strip().lower(), 0)


def _memory_research_pack_quality_reasons(pack: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    status = str(pack.get("status") or "").strip().lower()
    quality = str(pack.get("qualityStatus") or "").strip().lower()
    invalidation = str(pack.get("invalidationReason") or "").strip()
    answer_pack = pack.get("researchAnswerPack") if isinstance(pack.get("researchAnswerPack"), dict) else {}
    research_result = _memory_broker_preview(answer_pack.get("answer") or pack.get("researchResult"), 1200)
    claim_digest = [item for item in list(pack.get("claimDigest") or []) if isinstance(item, dict) and str(item.get("claim") or "").strip()]
    sources = list(answer_pack.get("sources") or []) or list(pack.get("sourceUrls") or []) or list(pack.get("sourceMatrixDigest") or [])
    if status == "archived":
        reasons.append("archived")
    if quality in {"low_quality_pack", "missing_evidence", "source_unreadable", "refresh_required"}:
        reasons.append(quality)
    answer_score = answer_pack.get("score") if isinstance(answer_pack.get("score"), dict) else {}
    answer_quality = str(answer_score.get("qualityStatus") or "").strip().lower()
    if answer_quality in {"low_quality_pack", "missing_evidence", "source_unreadable", "refresh_required"}:
        reasons.append(answer_quality)
    if invalidation:
        reasons.append(invalidation)
    if not research_result and not claim_digest:
        reasons.append("missing_final_research_result")
    if _memory_experience_confidence_rank(pack.get("confidence")) < 2:
        reasons.append("low_confidence")
    try:
        if pack.get("authorityScore") not in (None, "") and float(pack.get("authorityScore") or 0) < 35:
            reasons.append("low_authority")
    except (TypeError, ValueError):
        pass
    if not sources:
        reasons.append("missing_sources")
    return list(dict.fromkeys([reason for reason in reasons if reason]))


def _memory_compact_research_pack(pack: dict[str, Any], *, rejected_reason: str = "") -> dict[str, Any]:
    answer_pack = pack.get("researchAnswerPack") if isinstance(pack.get("researchAnswerPack"), dict) else {}
    claims = []
    for item in list(pack.get("claimDigest") or [])[:3]:
        if not isinstance(item, dict):
            continue
        claim = _memory_broker_preview(item.get("claim"), 240)
        if claim:
            claims.append(claim)
    source_entries = []
    for source in list(answer_pack.get("sources") or []):
        if isinstance(source, dict):
            source_entries.append(
                {
                    key: value
                    for key, value in {
                        "title": _memory_broker_preview(source.get("title"), 140),
                        "url": source.get("url"),
                        "host": source.get("host"),
                        "authorityScore": source.get("authorityScore"),
                    }.items()
                    if value not in (None, "", [], {})
                }
            )
    source_urls = [str(url).strip() for url in list(pack.get("sourceUrls") or []) if str(url).strip()][:4]
    if not source_entries:
        source_entries = [{"url": url} for url in source_urls]
    answer = _memory_broker_preview(answer_pack.get("answer") or pack.get("researchResult"), 760)
    payload = {
        "id": pack.get("experiencePackId"),
        "title": _memory_broker_preview(pack.get("title"), 180),
        "answer": answer,
        "researchResult": answer,
        "claimDigest": claims,
        "sources": source_entries[:4],
        "score": answer_pack.get("score") or {
            "confidence": pack.get("confidence"),
            "authorityScore": pack.get("authorityScore"),
            "qualityStatus": pack.get("qualityStatus"),
        },
        "rejectedEvidence": list(answer_pack.get("rejectedEvidence") or [])[:4],
        "confidence": pack.get("confidence"),
        "authorityScore": pack.get("authorityScore"),
        "status": pack.get("status"),
        "qualityStatus": pack.get("qualityStatus"),
        "topicFingerprint": pack.get("topicFingerprint"),
        "sourceUrls": source_urls,
        "reason": rejected_reason,
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _memory_route_research_experience(query: str, *, scope: Optional[str], limit: int) -> dict[str, Any]:
    try:
        from core.tools.research_ledger import search_experience_packs_with_options

        packs = search_experience_packs_with_options(query=query, scope=scope or "global", limit=max(limit * 2, 6), include_archived=True)
    except Exception as exc:
        return _memory_evidence_pack(
            source_domain="research_experience",
            scope=scope,
            missing_or_stale=[f"research_experience_unavailable: {str(exc)[:120]}"],
            recommended_next_action="Run fresh research or call research_broker when research experience is unavailable.",
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for pack in packs:
        key = "|".join(
            [
                str(pack.get("topicFingerprint") or pack.get("title") or pack.get("query") or "").strip().lower()[:120],
                str(pack.get("sourcePolicy") or "").strip().lower(),
                str(pack.get("invalidationReason") or pack.get("qualityStatus") or "").strip().lower(),
            ]
        )
        grouped.setdefault(key or str(pack.get("experiencePackId") or len(grouped)), []).append(pack)

    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    missing: list[str] = []
    for group in grouped.values():
        ranked = sorted(
            group,
            key=lambda item: (
                -len(_memory_research_pack_quality_reasons(item)),
                _memory_experience_confidence_rank(item.get("confidence")),
                float(item.get("authorityScore") or 0),
                int(item.get("usageCount") or 0),
            ),
            reverse=True,
        )
        best = ranked[0]
        reasons = _memory_research_pack_quality_reasons(best)
        if reasons:
            rejected.append(_memory_compact_research_pack(best, rejected_reason=", ".join(reasons[:3])))
            missing.extend(reasons[:3])
        else:
            selected.append(_memory_compact_research_pack(best))
        for duplicate in ranked[1:]:
            rejected.append(_memory_compact_research_pack(duplicate, rejected_reason="duplicate_lower_rank"))
        if len(selected) >= limit:
            break

    return _memory_evidence_pack(
        source_domain="research_experience",
        selected=selected[:limit],
        rejected=rejected[:limit],
        why_selected="Selected reusable research packs with final source-backed result and adequate confidence." if selected else "",
        scope=scope,
        freshness="research_ledger",
        confidence=selected[0].get("confidence") if selected else None,
        source_refs=[str(item.get("id")) for item in selected if item.get("id")],
        missing_or_stale=list(dict.fromkeys(missing))[:6],
        recommended_next_action="reuse_selected_experience" if selected else "refresh_required",
    )


def _memory_route_workflow(query: str, *, scope: Optional[str], limit: int) -> dict[str, Any]:
    try:
        from runtimes.memory.workflow_service import workflow_memory_service

        scope_chain = [value for value in [scope, "global"] if value]
        hints = workflow_memory_service.match_hints(query=query, scope_chain=scope_chain, limit=min(limit, 3), engineering_active=True)
    except Exception as exc:
        return _memory_evidence_pack(
            source_domain="workflow_memory",
            scope=scope,
            missing_or_stale=[f"workflow_memory_unavailable: {str(exc)[:120]}"],
            recommended_next_action="Proceed with runtime planning; do not invent prior workflow.",
        )
    selected = []
    for hint in hints:
        selected.append(
            {
                key: value
                for key, value in {
                    "id": hint.get("id"),
                    "taskFamily": hint.get("task_family") or hint.get("taskFamily"),
                    "summary": _memory_broker_preview((hint.get("goldenPathSteps") or [""])[0] if isinstance(hint.get("goldenPathSteps"), list) else hint.get("summary"), 280),
                    "confidence": hint.get("confidence"),
                    "maturityScore": hint.get("maturity_score") or hint.get("maturityScore"),
                    "status": hint.get("status"),
                }.items()
                if value not in (None, "", [], {})
            }
        )
    return _memory_evidence_pack(
        source_domain="workflow_memory",
        selected=selected,
        why_selected="Selected workflow hints matched current task anchors and active scope." if selected else "",
        scope=scope,
        confidence=selected[0].get("confidence") if selected else None,
        source_refs=[f"workflow://{item.get('id')}" for item in selected if item.get("id")],
        missing_or_stale=[] if selected else ["no_workflow_hint_matched"],
        recommended_next_action="use_as_route_hint_not_script" if selected else "continue_with_fresh_runtime_plan",
    )


def _memory_route_engineering_proof(query: str, *, scope: Optional[str], limit: int) -> dict[str, Any]:
    try:
        entries = db.list_engineering_proof_entries(limit=30)
    except Exception as exc:
        return _memory_evidence_pack(
            source_domain="engineering_proof",
            scope=scope,
            missing_or_stale=[f"engineering_proof_unavailable: {str(exc)[:120]}"],
            recommended_next_action="Use Engineering Runtime for fresh proof.",
        )
    q_tokens = _memory_query_tokens(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in list(entries or []):
        if not isinstance(entry, dict):
            continue
        workspace = str(entry.get("workspace_path") or entry.get("workspacePath") or "").strip()
        if scope and scope not in {"global", "current_session"} and workspace and str(scope) not in workspace:
            continue
        haystack = " ".join(
            [
                str(entry.get("summary") or ""),
                str(entry.get("patchIntent") or ""),
                str(entry.get("verificationStatus") or ""),
                " ".join(map(str, entry.get("changedFiles") or [])),
            ]
        )
        overlap = len(q_tokens & _memory_query_tokens(haystack)) if q_tokens else 0
        if q_tokens and overlap <= 0:
            continue
        scored.append((overlap, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    selected = []
    for _, entry in scored[:limit]:
        selected.append(
            {
                key: value
                for key, value in {
                    "id": entry.get("id") or entry.get("entryId"),
                    "summary": _memory_broker_preview(entry.get("summary") or entry.get("patchIntent"), 260),
                    "workspacePath": entry.get("workspace_path") or entry.get("workspacePath"),
                    "verificationStatus": entry.get("verificationStatus"),
                    "updatedAt": entry.get("updated_at") or entry.get("updatedAt") or entry.get("createdAt"),
                }.items()
                if value not in (None, "", [], {})
            }
        )
    return _memory_evidence_pack(
        source_domain="engineering_proof",
        selected=selected,
        why_selected="Selected proof summaries by query overlap and workspace scope." if selected else "",
        scope=scope,
        source_refs=[f"engineering-proof://{item.get('id')}" for item in selected if item.get("id")],
        missing_or_stale=[] if selected else ["no_matching_engineering_proof"],
        recommended_next_action="use_as_verification_context" if selected else "ask Engineering Runtime for fresh proof",
    )


def _memory_route_core(runtime: Any, query: str, *, scope: Optional[str], limit: int) -> dict[str, Any]:
    results = runtime.unified_recall(query=query, limit=limit, scope=scope)
    selected = [_memory_broker_compact_recall_item(item) for item in list(results or [])[:limit] if isinstance(item, dict)]
    return _memory_evidence_pack(
        source_domain="memory_core",
        selected=selected,
        why_selected="Selected by Memory Runtime hybrid recall." if selected else "",
        scope=scope,
        confidence=selected[0].get("confidence") if selected else None,
        source_refs=[str(item.get("id")) for item in selected if item.get("id")],
        missing_or_stale=[] if selected else ["no_matching_memory_core_item"],
        recommended_next_action="use_selected_memory_with_scope_check" if selected else "ask_user_or_collect_fresh_evidence",
    )


@tool
def memory_broker(
    mode: str = "recall",
    query: Optional[str] = None,
    item_id: Optional[str] = None,
    memory_ref: Optional[str] = None,
    memory_ref_or_date: Optional[str] = None,
    entity: Optional[str] = None,
    scope: Optional[str] = None,
    limit: int = 5,
    hops: int = 2,
    anchor_date: Optional[str] = None,
    detail_level: str = "summary",
) -> str:
    """Read-only Memory/RAG broker for supervisor self-service recall.

    Use this before relying on passive memory injection when the user asks about prior work,
    remembered preferences, project history, daily logs, or knowledge graph relations.
    Use mode="catalog" to inspect available memory domains, or mode="route" to get a compact
    evidence pack across Memory Core, Research Experience, Workflow and Engineering proof.
    A no-match result is final for the current query. Continue the current task instead of
    retrying memory with near-duplicate wording in the same user turn.
    This tool does not update or delete memory.
    """
    normalized_mode = str(mode or "recall").strip().lower()
    normalized_detail = str(detail_level or "summary").strip().lower()
    runtime = _get_memory_runtime()
    effective_limit = _memory_broker_clamp_limit(limit)
    try:
        if normalized_mode == "catalog":
            domains = _memory_broker_catalog(scope=scope)
            return _memory_broker_response(
                ok=True,
                kind="memory_broker",
                mode=normalized_mode,
                scope=scope or "current_session",
                summary=f"Memory catalog lists {len(domains)} domain(s). Queryable domains are intentionally compact.",
                domains=domains,
                nextAction="Use memory_broker(mode='route', query='...', intent='...') to retrieve a compact evidence pack.",
            )

        if normalized_mode == "route":
            search_text = str(query or item_id or "").strip()
            if not search_text:
                return _memory_broker_response(
                    ok=False,
                    kind="memory_broker",
                    mode=normalized_mode,
                    failureClass="missing_query",
                    summary="memory_broker route needs a query.",
                    nextAction="Call memory_broker(mode='route', query='...') with the memory question or keywords.",
                )
            routed_domains = _memory_route_domains(search_text, intent=normalized_detail)
            packs: list[dict[str, Any]] = []
            rejected_domains: list[dict[str, Any]] = []
            for domain in routed_domains:
                if domain == "memory_core":
                    packs.append(_memory_route_core(runtime, search_text, scope=scope, limit=min(effective_limit, 3)))
                elif domain == "research_experience":
                    packs.append(_memory_route_research_experience(search_text, scope=scope or "global", limit=min(effective_limit, 3)))
                elif domain == "workflow_memory":
                    packs.append(_memory_route_workflow(search_text, scope=scope, limit=min(effective_limit, 3)))
                elif domain == "engineering_proof":
                    packs.append(_memory_route_engineering_proof(search_text, scope=scope, limit=min(effective_limit, 3)))
                elif domain == "knowledge_graph":
                    rejected_domains.append({"domain": domain, "reason": "Use graph_search/graph_neighbors for exact entity traversal."})
                elif domain == "daily_log":
                    rejected_domains.append({"domain": domain, "reason": "Use map/read_day when a date or memoryRef is available."})
            selected_count = sum(len(pack.get("selectedEvidence") or []) for pack in packs)
            return _memory_broker_response(
                ok=True,
                kind="memory_broker",
                mode=normalized_mode,
                query=search_text,
                scope=scope,
                selectedDomains=[pack.get("sourceDomain") for pack in packs if pack.get("sourceDomain")],
                rejectedDomains=rejected_domains,
                summary=f"Routed memory query to {len(packs)} domain(s); selected {selected_count} evidence item(s).",
                evidencePacks=packs,
                omitted={"rawLedgers": "Use domain-specific broker/detail tool only when the compact evidence is insufficient."},
                nextAction="Use selectedEvidence only when scope/confidence fits; follow recommendedNextAction per domain.",
            )

        if normalized_mode == "recall":
            search_text = str(query or item_id or "").strip()
            if not search_text:
                return _memory_broker_response(
                    ok=False,
                    kind="memory_broker",
                    mode=normalized_mode,
                    failureClass="missing_query",
                    summary="memory_broker recall needs a query.",
                    nextAction="Call memory_broker(mode='recall', query='...') with the memory question or keywords.",
                )
            results = runtime.unified_recall(query=search_text, limit=effective_limit, scope=scope)
            items = [_memory_broker_compact_recall_item(item) for item in results[:effective_limit] if isinstance(item, dict)]
            return _memory_broker_response(
                ok=True,
                kind="memory_broker",
                mode=normalized_mode,
                query=search_text,
                scope=scope,
                summary=f"Found {len(items)} relevant memory item(s)." if items else "No matching prior memory.",
                items=items,
                nextAction="Use get_item/read_day/graph_neighbors if a result needs deeper verification." if items else None,
            )

        if normalized_mode == "get_item":
            search_text = str(item_id or query or "").strip()
            if not search_text:
                return _memory_broker_response(
                    ok=False,
                    kind="memory_broker",
                    mode=normalized_mode,
                    failureClass="missing_item_id",
                    summary="memory_broker get_item needs item_id.",
                    nextAction="Pass an id returned by memory_broker(mode='recall').",
                )
            results = runtime.query_knowledge(query=search_text, scope=scope, limit=effective_limit)
            exact = [item for item in results if str(item.get("id") or "") == search_text]
            selected = exact or results[:effective_limit]
            items = [_memory_broker_compact_recall_item(item) for item in selected if isinstance(item, dict)]
            return _memory_broker_response(
                ok=True,
                kind="memory_broker",
                mode=normalized_mode,
                query=search_text,
                summary=f"Loaded {len(items)} memory item(s)." if items else "No memory item matched.",
                items=items,
                nextAction="Treat no-match as memory insufficient; do not invent prior facts." if not items else "Use the item scope and confidence before acting on it.",
            )

        if normalized_mode == "read_day":
            ref = str(memory_ref_or_date or memory_ref or query or "").strip()
            if not ref:
                return _memory_broker_response(
                    ok=False,
                    kind="memory_broker",
                    mode=normalized_mode,
                    failureClass="missing_memory_ref",
                    summary="memory_broker read_day needs memory_ref_or_date.",
                    nextAction="Pass memory://day/YYYY-MM-DD or YYYY-MM-DD.",
                )
            text = runtime.read_memory_day(memory_ref_or_date=ref)
            preview_limit = 1800 if normalized_detail not in {"full", "detail"} else 4200
            preview = _memory_broker_preview(text, preview_limit)
            return _memory_broker_response(
                ok=True,
                kind="memory_broker",
                mode=normalized_mode,
                memoryRef=ref,
                summary="Loaded day memory preview.",
                preview=preview,
                omittedChars=max(0, len(str(text or "")) - len(preview)),
                nextAction="Use this as historical evidence; ask for exact details if omittedChars is large.",
            )

        if normalized_mode == "map":
            payload = runtime.build_memory_map(anchor_date=anchor_date)
            refs = payload.get("currentRefs") if isinstance(payload, dict) else {}
            raw_items = list(payload.get("items") or []) if isinstance(payload, dict) else []
            items = []
            for item in raw_items[:effective_limit]:
                if not isinstance(item, dict):
                    continue
                items.append(
                    {
                        key: value
                        for key, value in {
                            "memoryRef": item.get("memoryRef"),
                            "kind": item.get("kind"),
                            "label": item.get("label"),
                            "summaryState": item.get("summaryState"),
                            "latestDay": item.get("latestDay"),
                        }.items()
                        if value not in (None, "", [], {})
                    }
                )
            return _memory_broker_response(
                ok=True,
                kind="memory_broker",
                mode=normalized_mode,
                summary="Loaded memory navigation map.",
                currentRefs=refs,
                items=items,
                omittedItems=max(0, len(raw_items) - len(items)),
                nextAction="Use expand_map on a memoryRef or read_day on a day ref for details.",
            )

        if normalized_mode == "expand_map":
            ref = str(memory_ref or query or "").strip()
            if not ref:
                return _memory_broker_response(
                    ok=False,
                    kind="memory_broker",
                    mode=normalized_mode,
                    failureClass="missing_memory_ref",
                    summary="memory_broker expand_map needs memory_ref.",
                    nextAction="Call memory_broker(mode='map') first or pass a memoryRef from passive injection.",
                )
            payload = runtime.expand_memory_map(memory_ref=ref)
            children = list(payload.get("children") or payload.get("items") or []) if isinstance(payload, dict) else []
            items = []
            for item in children[:effective_limit]:
                if not isinstance(item, dict):
                    continue
                items.append(
                    {
                        key: value
                        for key, value in {
                            "memoryRef": item.get("memoryRef"),
                            "kind": item.get("kind"),
                            "label": item.get("label"),
                            "summaryState": item.get("summaryState"),
                            "latestDay": item.get("latestDay"),
                        }.items()
                        if value not in (None, "", [], {})
                    }
                )
            return _memory_broker_response(
                ok=True,
                kind="memory_broker",
                mode=normalized_mode,
                memoryRef=ref,
                summary=f"Expanded memory node with {len(items)} child item(s).",
                items=items,
                omittedItems=max(0, len(children) - len(items)),
                nextAction="Use read_day for exact daily logs or expand_map again for nested nodes.",
            )

        if normalized_mode == "graph_search":
            keyword = str(entity or query or "").strip()
            if not keyword:
                return _memory_broker_response(
                    ok=False,
                    kind="memory_broker",
                    mode=normalized_mode,
                    failureClass="missing_query",
                    summary="memory_broker graph_search needs query or entity.",
                    nextAction="Pass an entity name or fuzzy keyword.",
                )
            results = runtime.search_entities(keyword=keyword, limit=effective_limit)
            items = [
                {
                    key: value
                    for key, value in {
                        "name": item.get("name"),
                        "type": item.get("type"),
                        "confidence": _memory_broker_score(item),
                        "maintainerSource": item.get("maintainerSource"),
                    }.items()
                    if value not in (None, "", [], {})
                }
                for item in results
                if isinstance(item, dict)
            ]
            return _memory_broker_response(
                ok=True,
                kind="memory_broker",
                mode=normalized_mode,
                query=keyword,
                summary=f"Found {len(items)} graph entity candidate(s)." if items else "No graph entity matched.",
                items=items,
                nextAction="Use graph_neighbors with the exact entity name to inspect relations." if items else "Use recall or ask the user when graph has no matching entity.",
            )

        if normalized_mode == "graph_neighbors":
            name = str(entity or query or "").strip()
            if not name:
                return _memory_broker_response(
                    ok=False,
                    kind="memory_broker",
                    mode=normalized_mode,
                    failureClass="missing_entity",
                    summary="memory_broker graph_neighbors needs entity.",
                    nextAction="Pass an exact entity name, or call graph_search first.",
                )
            direct = runtime.query_entity(entity=name)
            hop_count = max(1, min(int(hops or 2), 3))
            multi_hop = runtime.query_multi_hop(entity=name, hops=hop_count) if hop_count > 1 else []
            relations = []
            for item in [*list(direct or []), *list(multi_hop or [])]:
                if not isinstance(item, dict):
                    continue
                relations.append(
                    {
                        key: value
                        for key, value in {
                            "hop": item.get("hop"),
                            "direction": item.get("direction"),
                            "subject": item.get("subject"),
                            "predicate": item.get("predicate"),
                            "object": item.get("object"),
                            "confidence": _memory_broker_score(item),
                            "maintainerSource": item.get("maintainerSource"),
                        }.items()
                        if value not in (None, "", [], {})
                    }
                )
                if len(relations) >= effective_limit:
                    break
            return _memory_broker_response(
                ok=True,
                kind="memory_broker",
                mode=normalized_mode,
                entity=name,
                summary=f"Found {len(relations)} graph relation(s)." if relations else "No graph relations found for entity.",
                relations=relations,
                omittedRelations=max(0, len(list(direct or [])) + len(list(multi_hop or [])) - len(relations)),
                nextAction="Use these relations as hints; verify with recall or project evidence before high-impact action.",
            )

        if normalized_mode == "explain_injection":
            return _memory_broker_response(
                ok=True,
                kind="memory_broker",
                mode=normalized_mode,
                summary="Passive Memory/RAG injection is a compact snapshot, not the complete memory truth.",
                items=[
                    {
                        "type": "rule",
                        "text": "Use memory_broker(recall/get_item/read_day/graph_neighbors) when prior facts affect decisions.",
                    },
                    {
                        "type": "rule",
                        "text": "Workflow behavior hints are injected separately and are not replaced by memory_broker.",
                    },
                    {
                        "type": "rule",
                        "text": "If memory lookup returns no_match or stale context, say so instead of inventing history.",
                    },
                ],
                nextAction="Call recall for facts, map/read_day for timeline, or graph_search/graph_neighbors for relations.",
            )

        return _memory_broker_response(
            ok=False,
            kind="memory_broker",
            mode=normalized_mode,
            failureClass="unsupported_mode",
            summary=f"Unsupported memory_broker mode: {normalized_mode}",
            nextAction="Use catalog, route, recall, get_item, read_day, map, expand_map, graph_search, graph_neighbors, or explain_injection.",
        )
    except Exception as e:
        return _memory_broker_response(
            ok=False,
            kind="memory_broker",
            mode=normalized_mode,
            failureClass="memory_unavailable",
            summary=f"Memory broker failed: {str(e)}",
            nextAction="Do not invent memory; ask the user or proceed with fresh evidence.",
        )


@tool
def memory_recall(query: str, limit: int = 5) -> str:
    """Unified hybrid memory retrieval tool. Call this to search the memory system for facts, code snippets, or user preferences.
    It automatically routes your query through Full-Text Search, Vector Similarity Search, and Knowledge Graph traversal, returning reranked results.

    Arguments:
        query (str): Search query or natural language question (e.g. "What is the project architecture?", "React hooks preference").
        limit (int): Max number of distinct memory fragments to return. Default: 5.
    """
    try:
        results = _get_memory_runtime().unified_recall(query=query, limit=limit)

        if not results:
            return f"No relevant memory found for '{query}'."

        lines = [f"Hybrid recall results for '{query}' (Top {len(results)}):"]
        for r in results:
            s = r.get('scope', 'global')
            c = r.get('category', 'unknown')
            t = r.get('text') or r.get('fact') or ''
            lines.append(f"- [{s}|{c}] {t} (id: {r.get('id', 'N/A')})")

        return "\n".join(lines)
    except Exception as e:
        return f"Error executing memory_recall: {str(e)}"


@tool
def mem_delete(fact_id: str) -> str:
    """Compatibility wrapper for deleting a memory item by ID.

    Prefer `mem_update(..., mode=\"delete\")` in supervisor-facing flows.
    """
    return mem_update(fact_id=fact_id, mode="delete")


@tool
def mem_update(fact_id: str, mode: str = "update", new_content: Optional[str] = None) -> str:
    """Update or delete an existing knowledge item by ID.
    Use mode=\"update\" to replace incorrect content, or mode=\"delete\" to remove a completely false or obsolete item.

    Arguments:
        fact_id (str): The unique ID of the fact to modify (e.g. "fact-a1b2c3d4").
        mode (str): Either "update" or "delete".
        new_content (str, optional): The full corrected text when mode="update".
    """
    normalized_mode = str(mode or "update").strip().lower()
    try:
        if normalized_mode == "delete":
            success = _get_memory_runtime().delete_knowledge(fact_id=fact_id)
            if success:
                return f"✓ Deleted '{fact_id}' from memory."
            return f"Error: Knowledge item '{fact_id}' not found."

        if normalized_mode != "update":
            return "Error: mode must be either 'update' or 'delete'."

        normalized_content = str(new_content or "").strip()
        if not normalized_content:
            return "Error: new_content is required when mode='update'."

        success = _get_memory_runtime().update_knowledge(fact_id=fact_id, new_fact=normalized_content)
        if success:
            return f"✓ Updated '{fact_id}' with new content."
        return f"Error: Knowledge item '{fact_id}' not found."
    except Exception as e:
        action = "deleting" if normalized_mode == "delete" else "updating"
        return f"Error {action} memory: {str(e)}"


@tool
def mem_summary(tier: str, date: Optional[str] = None) -> str:
    """Retrieve historical hierarchical memory summaries (year, month, week, or day level).

    This is extremely useful to restore long-term context beyond the last 2 days.
    Use the high-level summary (e.g. week/month) to find interesting dates, then call again with tier='day' and date='YYYY-MM-DD' to load the exact daily log that details the task.

    Arguments:
        tier (str): The level of summary to retrieve. Must be one of "day", "week", "month", or "year".
        date (str, optional): A target date in "YYYY-MM-DD" format. If omitted, uses the current date as reference to fetch the current week/month/year.
    """
    try:
        return _get_memory_runtime().read_memory_summary(tier=tier, date_str=date)
    except Exception as e:
        return f"Error retrieving memory summary: {str(e)}"


@tool
def memory_map(anchor_date: Optional[str] = None) -> str:
    """Return the brokered memory navigation map. Use this instead of raw filesystem paths."""
    try:
        payload = _get_memory_runtime().build_memory_map(anchor_date=anchor_date)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error building memory map: {str(e)}"


@tool
def memory_map_expand(memory_ref: str) -> str:
    """Expand a brokered memory map node and return its children."""
    try:
        payload = _get_memory_runtime().expand_memory_map(memory_ref=memory_ref)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error expanding memory map: {str(e)}"


@tool
def memory_read_day(memory_ref_or_date: str) -> str:
    """Read a single memory day log by brokered memoryRef or YYYY-MM-DD date."""
    try:
        return _get_memory_runtime().read_memory_day(memory_ref_or_date=memory_ref_or_date)
    except Exception as e:
        return f"Error reading memory day: {str(e)}"
