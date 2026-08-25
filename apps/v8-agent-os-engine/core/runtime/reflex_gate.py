from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
import re
from typing import Any

from core.database import db
from core.realtime_protocol import build_runtime_event


logger = logging.getLogger(__name__)


RuntimeReflexMode = str
RuntimeGateStatus = str
RuntimeGateRiskLevel = str

_CODE_INTENT_RE = re.compile(
    r"(代码|编程|修复|测试|重构|bug|pytest|build|typecheck|typescript|python|react|repo|diff|commit|code|refactor|debug)",
    re.IGNORECASE,
)
_VOICE_INTENT_RE = re.compile(r"(语音|朗读|播报|tts|voice|speak|发语音)", re.IGNORECASE)
_ROUTE_INTENT_RE = re.compile(r"(skill|工具|插件|mcp|公众号|微信|文档|视频|图片|财报|会议纪要|代码审查)", re.IGNORECASE)
_EXPLICIT_EXTENSION_DEPENDENCY_RE = re.compile(r"(?:\bskill\b|插件|\bplugin\b|\bmcp\b)", re.IGNORECASE)
_READ_ONLY_EXECUTION_RE = re.compile(
    r"(只读(?:审查|检查|分析|任务)?|仅规划|仅方案)"
    r"|(?:不要|不需要|禁止|无需|不真实).{0,8}(?:写入|写|修改|改动|变更|落盘)"
    r".{0,8}(?:任何|当前|这个|本)?(?:项目|源码|文件|工作区|workspace|repo)"
    r"|(?:不写|不落盘|不修改|不改动)(?:任何|当前|这个|本)?(?:项目|源码|文件|工作区|workspace|repo)"
    r"|without\s+(?:actually\s+)?(?:writing|modifying|changing)|read[- ]only",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RuntimeReflexDecision:
    mode: RuntimeReflexMode = "none"
    confidence: float = 0.0
    matchedReflexes: list[str] = field(default_factory=list)
    promptPatch: str = ""
    evidenceRefs: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RuntimeGateDecision:
    status: RuntimeGateStatus = "clean"
    riskLevel: RuntimeGateRiskLevel = "low"
    reasons: list[str] = field(default_factory=list)
    recommendedAction: str = ""
    promptPatch: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceFeedbackPacket:
    sessionId: str | None = None
    runId: str | None = None
    scope: str = "global"
    sourceRuntime: str = "chat"
    graphSummary: dict[str, Any] = field(default_factory=dict)
    workflowHint: dict[str, Any] = field(default_factory=dict)
    proofWorkset: dict[str, Any] = field(default_factory=dict)
    reflex: dict[str, Any] = field(default_factory=dict)
    gate: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _trim(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _safe_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _candidate_summary(route_bundle: Any) -> dict[str, Any]:
    summary = getattr(route_bundle, "candidate_summary", None)
    return summary if isinstance(summary, dict) else {}


def _selected_skill_names(route_bundle: Any) -> list[str]:
    return [str(item).strip() for item in _safe_list(getattr(route_bundle, "selected_skill_names", [])) if str(item).strip()]


def _selected_mcp_tools(route_bundle: Any) -> list[str]:
    return [str(item).strip() for item in _safe_list(getattr(route_bundle, "exposed_mcp_tool_names", [])) if str(item).strip()]


def _find_nested_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value.get(key)
        for nested in value.values():
            found = _find_nested_value(nested, key)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_nested_value(item, key)
            if found is not None:
                return found
    return None


def _truthy_nested(value: Any, key: str) -> bool:
    found = _find_nested_value(value, key)
    if isinstance(found, str):
        return found.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(found)


def _engineering_active(state: dict[str, Any], user_query: str) -> bool:
    engineering = state.get("engineering_context") if isinstance(state.get("engineering_context"), dict) else {}
    trigger = engineering.get("triggerDecision") if isinstance(engineering.get("triggerDecision"), dict) else {}
    if "active" in trigger:
        return bool(trigger.get("active"))
    mode = str(state.get("engineeringMode") or state.get("engineering_mode") or "").strip().lower()
    if mode == "force":
        return True
    return bool(_CODE_INTENT_RE.search(str(user_query or "")))


def _read_only_execution_intent(user_query: str, state: dict[str, Any]) -> bool:
    query = str(user_query or "")
    explicit_state = state.get("execution_intent") if isinstance(state.get("execution_intent"), dict) else {}
    if explicit_state.get("readOnly") is True or explicit_state.get("read_only") is True:
        return True
    # Scope boundaries such as "do not modify other projects" authorize work
    # inside the bound workspace; they must not be reclassified as a global
    # read-only instruction.  Only an unambiguous read-only phrase (or a typed
    # execution intent above) may suppress writes.
    return bool(_READ_ONLY_EXECUTION_RE.search(query))


def _scope_conflict_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    conflict = state.get("scopeConflict") or state.get("scope_conflict")
    if isinstance(conflict, dict) and conflict:
        return conflict
    if conflict:
        return {"conflict": conflict}
    return None


class RuntimeReflexService:
    """Low-token, no-side-effect prompt bias for already-known runtime evidence."""

    def evaluate(
        self,
        *,
        user_query: str,
        scope: str,
        scope_chain: list[str] | None,
        session_id: str | None,
        route_bundle: Any,
        state: dict[str, Any],
        memory_diagnostics: dict[str, Any] | None = None,
    ) -> RuntimeReflexDecision:
        patch_lines: list[str] = []
        matched: list[str] = []
        evidence_refs: list[dict[str, Any]] = []
        confidence = 0.0
        query = str(user_query or "")

        if _VOICE_INTENT_RE.search(query):
            matched.append("voice_output_discipline")
            confidence = max(confidence, 0.72)
            patch_lines.append("语音/朗读输出走干净文本纪律：避免特殊符号、代码块和不可播报格式，必要时按当前语音标签规范包裹正文。")

        if _engineering_active(state, query):
            confidence = max(confidence, 0.68)
            if _read_only_execution_intent(query, state):
                matched.append("engineering_read_only_contract")
                patch_lines.append("这是显式只读工程任务：保持 readSet 纪律，writeSet=[]，只产出 typed handoff，不要写入或修改工作区。")
            else:
                matched.append("engineering_read_before_write")
                patch_lines.append("工程任务先读关键文件与既有诊断，再写；保持 readSet/writeSet 纪律，验证是否执行仍由 supervisor 决策。")

        selected_skills = _selected_skill_names(route_bundle)
        if 0 < len(selected_skills) <= 3:
            matched.append("scoped_extension_route_bias")
            confidence = max(confidence, 0.64)
            patch_lines.append(f"Extensions 已预筛：优先围绕 {', '.join(selected_skills[:3])} 解释或行动，不要全量枚举技能。")
            evidence_refs.append({"type": "extensions_route", "selectedSkills": selected_skills[:3]})

        workflow_hint = self._best_workflow_hint(user_query=query, scope_chain=scope_chain, session_id=session_id, state=state)
        if workflow_hint:
            matched.append("verified_workflow_bias")
            confidence = max(confidence, 0.66)
            patch_lines.append("行为链记忆已命中：只把它当低权重 checklist/bias，不替代 Supervisor 判断、证据读取或用户确认。")
            evidence_refs.append({"type": "workflow_hint", **workflow_hint})

        diagnostics = dict(memory_diagnostics or {})
        if diagnostics.get("consistencyNoteInjected") or diagnostics.get("consistencyConflicts"):
            matched.append("memory_latest_fact_bias")
            confidence = max(confidence, 0.74)
            patch_lines.append("记忆一致性审计发现旧摘要冲突时，以 canonical 最新偏好/事实为准。")
            evidence_refs.append({"type": "memory_consistency", "conflicts": diagnostics.get("consistencyConflicts") or []})

        if not patch_lines:
            return RuntimeReflexDecision(
                mode="none",
                confidence=0.0,
                diagnostics={
                    "scope": scope,
                    "scopeChain": list(scope_chain or []),
                    "selectedSkillCount": len(selected_skills),
                },
            )

        prompt_patch = _trim("\n".join(f"- {line}" for line in patch_lines), 400)
        return RuntimeReflexDecision(
            mode="bias",
            confidence=round(min(confidence, 0.95), 3),
            matchedReflexes=matched,
            promptPatch=prompt_patch,
            evidenceRefs=evidence_refs[:5],
            diagnostics={
                "scope": scope,
                "scopeChain": list(scope_chain or []),
                "selectedSkillCount": len(selected_skills),
                "engineeringActive": _engineering_active(state, query),
            },
        )

    def _best_workflow_hint(
        self,
        *,
        user_query: str,
        scope_chain: list[str] | None,
        session_id: str | None,
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            from runtimes.memory.workflow_service import workflow_memory_service

            hints = workflow_memory_service.match_hints(
                query=user_query,
                scope_chain=scope_chain,
                session_id=session_id,
                run_id=state.get("run_id") or state.get("runId"),
                limit=1,
                engineering_active=_engineering_active(state, user_query),
            )
        except Exception as exc:
            logger.debug("Runtime reflex workflow hint lookup skipped: %s", exc)
            return None
        if not hints:
            return None
        first = hints[0] if isinstance(hints[0], dict) else {}
        return {
            "candidateId": str(first.get("candidateId") or first.get("id") or ""),
            "workflowClass": str(first.get("workflowClass") or ""),
            "score": first.get("score"),
        }


class RuntimePreflightGate:
    """Small universal gate that aggregates existing runtime confidence signals."""

    def evaluate(
        self,
        *,
        user_query: str,
        scope: str,
        scope_chain: list[str] | None,
        session_id: str | None,
        route_bundle: Any,
        state: dict[str, Any],
        memory_diagnostics: dict[str, Any] | None = None,
    ) -> RuntimeGateDecision:
        summary = _candidate_summary(route_bundle)
        selected_skills = _selected_skill_names(route_bundle)
        selected_mcp = _selected_mcp_tools(route_bundle)
        reasons: list[str] = []
        diagnostics: dict[str, Any] = {
            "scope": scope,
            "scopeChain": list(scope_chain or []),
            "sessionId": session_id,
            "selectedSkillCount": len(selected_skills),
            "selectedMcpToolCount": len(selected_mcp),
            "inventoryReadyState": _find_nested_value(summary, "inventoryReadyState"),
            "dirtyVisibleRoots": _find_nested_value(summary, "dirtyVisibleRoots") or [],
        }
        blocked = False
        clarify = False
        read_only_execution = _read_only_execution_intent(user_query, state)
        diagnostics["readOnlyExecutionIntent"] = read_only_execution
        extension_inventory_required = bool(
            _EXPLICIT_EXTENSION_DEPENDENCY_RE.search(str(user_query or ""))
            or _truthy_nested(state, "requiresExtensionInventory")
            or _truthy_nested(state, "requires_extension_inventory")
        )
        diagnostics["extensionInventoryRequired"] = extension_inventory_required

        if _truthy_nested(summary, "inventoryBarrierTimedOut"):
            diagnostics["inventoryBarrierTimedOut"] = True
            if extension_inventory_required:
                reasons.append("inventory_barrier_timed_out")
                blocked = True

        ready_state = str(diagnostics.get("inventoryReadyState") or "").strip().lower()
        if (
            extension_inventory_required
            and ready_state
            and ready_state not in {"ready", "fresh", "hot_ready", "ready_snapshot"}
        ):
            reasons.append("inventory_not_ready")

        stage1_entries = _safe_list(summary.get("skillStage1Entries")) or _safe_list(summary.get("stage1Entries"))
        final_entries = _safe_list(summary.get("skillEntries")) or _safe_list(summary.get("finalEntries"))
        if extension_inventory_required and len(stage1_entries) >= 8 and len(selected_skills) >= 3:
            reasons.append("route_candidate_spread")
        if not selected_skills and not selected_mcp and _ROUTE_INTENT_RE.search(str(user_query or "")):
            reasons.append("route_no_candidate_for_tool_like_query")
            clarify = True
        if final_entries:
            diagnostics["finalCandidateCount"] = len(final_entries)
        if stage1_entries:
            diagnostics["stage1CandidateCount"] = len(stage1_entries)

        scope_conflict = _scope_conflict_from_state(state)
        if scope_conflict:
            reasons.append("session_scope_conflict")
            diagnostics["scopeConflict"] = scope_conflict
            blocked = True

        engineering = state.get("engineering_context") if isinstance(state.get("engineering_context"), dict) else {}
        engineering_risk = _find_nested_value(engineering, "worksetSoftGateDecision") or _find_nested_value(engineering, "worksetDispatchDecision")
        if isinstance(engineering_risk, dict):
            diagnostics["engineeringWorksetDecision"] = engineering_risk
            risk = str(engineering_risk.get("risk") or engineering_risk.get("status") or "").strip()
            if risk in {"outside_write_set", "missing_write_set", "unknown_write_set"} or engineering_risk.get("warning"):
                if read_only_execution:
                    reasons.append("engineering_workset_risk_not_applicable_to_read_only")
                else:
                    reasons.append("engineering_workset_risk")
                    clarify = True

        memory = dict(memory_diagnostics or {})
        if memory.get("consistencyNoteInjected") or memory.get("consistencyConflicts"):
            reasons.append("memory_consistency_conflict")
            diagnostics["memoryConsistencyConflicts"] = memory.get("consistencyConflicts") or []

        transport = str(state.get("transport") or state.get("runtime_transport") or "").strip()
        if transport == "network_supervisor_openai" and not state.get("workspace_id") and not state.get("workspace_path") and not state.get("project_id"):
            diagnostics["networkWorkspaceLess"] = True
            diagnostics["networkWorkspaceRulesPolicy"] = "workspace_rules_disabled_memory_allowed"

        unique_reasons = list(dict.fromkeys(reasons))
        if blocked:
            status = "blocked"
            risk_level = "critical" if "session_scope_conflict" in unique_reasons else "high"
            action = "repair_or_restart_scope_before_side_effects"
        elif clarify or "route_no_candidate_for_tool_like_query" in unique_reasons:
            status = "clarify"
            risk_level = "medium"
            action = "ask_user_or_read_evidence_before_side_effects"
        elif unique_reasons:
            status = "watch"
            risk_level = "low"
            action = "continue_with_runtime_risks_visible"
        else:
            status = "clean"
            risk_level = "low"
            action = "continue"

        prompt_patch = ""
        if status != "clean":
            if status == "blocked":
                prompt_patch = "Runtime gate blocked this handoff: repair scope/freshness/contract before side effects. Reasons: "
            elif status == "clarify":
                prompt_patch = "Runtime gate asks for clarification or evidence before side effects. Reasons: "
            else:
                prompt_patch = "Runtime gate watchlist: keep these risks visible. Reasons: "
            prompt_patch = _trim(prompt_patch + ", ".join(unique_reasons[:5]), 520)

        return RuntimeGateDecision(
            status=status,
            riskLevel=risk_level,
            reasons=unique_reasons,
            recommendedAction=action,
            promptPatch=prompt_patch,
            diagnostics=diagnostics,
        )


class RuntimeEvidenceFeedbackService:
    """Records reflex/gate/graph/workflow evidence without creating a new memory system."""

    def record(
        self,
        *,
        session_id: str | None,
        run_id: str | None,
        scope: str,
        reflex_decision: RuntimeReflexDecision,
        gate_decision: RuntimeGateDecision,
        memory_diagnostics: dict[str, Any] | None = None,
        route_bundle: Any = None,
        state: dict[str, Any] | None = None,
    ) -> EvidenceFeedbackPacket:
        memory = dict(memory_diagnostics or {})
        packet = EvidenceFeedbackPacket(
            sessionId=session_id,
            runId=run_id,
            scope=scope,
            sourceRuntime=str((state or {}).get("transport") or "chat"),
            graphSummary={
                "injected": bool(memory.get("graphSummaryInjected")),
                "relationCount": memory.get("graphSummaryRelationCount"),
                "seedEntities": memory.get("graphSummarySeedEntities") or [],
                "trimmed": bool(memory.get("graphSummaryTrimmed")),
            },
            workflowHint={
                "selectedSkills": _selected_skill_names(route_bundle) if route_bundle is not None else [],
            },
            proofWorkset={
                "engineeringWorksetDecision": _find_nested_value((state or {}).get("engineering_context"), "worksetDispatchDecision")
                or _find_nested_value((state or {}).get("engineering_context"), "worksetSoftGateDecision"),
            },
            reflex=reflex_decision.as_dict(),
            gate=gate_decision.as_dict(),
            diagnostics={
                "memoryConsistencyConflict": bool(memory.get("consistencyNoteInjected") or memory.get("consistencyConflicts")),
            },
        )
        if reflex_decision.mode != "none":
            self._emit(session_id=session_id, run_id=run_id, topic="runtime.reflex.decision", payload=reflex_decision.as_dict())
        if gate_decision.status != "clean":
            self._emit(session_id=session_id, run_id=run_id, topic="runtime.gate.decision", payload=gate_decision.as_dict())
        if self._has_feedback_signal(packet):
            self._emit(session_id=session_id, run_id=run_id, topic="memory.evidence.feedback", payload=packet.as_dict())
        return packet

    def _has_feedback_signal(self, packet: EvidenceFeedbackPacket) -> bool:
        graph = packet.graphSummary or {}
        proof = packet.proofWorkset or {}
        return bool(
            graph.get("injected")
            or packet.workflowHint.get("selectedSkills")
            or proof.get("engineeringWorksetDecision")
            or packet.reflex.get("mode") not in {None, "", "none"}
            or packet.gate.get("status") not in {None, "", "clean"}
            or packet.diagnostics.get("memoryConsistencyConflict")
        )

    def _emit(self, *, session_id: str | None, run_id: str | None, topic: str, payload: dict[str, Any]) -> None:
        if not session_id:
            return
        try:
            seq = db.get_next_runtime_seq(session_id)
            db.add_runtime_event(
                build_runtime_event(
                    topic=topic,
                    payload=payload,
                    session_id=session_id,
                    conversation_id=session_id,
                    run_id=run_id,
                    seq=seq,
                    source={
                        "plane": "engine",
                        "component": "runtime_reflex_gate",
                        "node": "supervisor",
                        "agent_id": "supervisor",
                    },
                )
            )
        except Exception as exc:
            logger.debug("Failed to emit %s runtime event: %s", topic, exc)


def render_reflex_prompt_addition(decision: RuntimeReflexDecision) -> str:
    if decision.mode == "none" or not decision.promptPatch:
        return ""
    return (
        "\n[RUNTIME REFLEX]\n"
        f"mode={decision.mode}; confidence={decision.confidence}\n"
        f"{decision.promptPatch}\n"
        "[/RUNTIME REFLEX]\n"
    )


def render_gate_prompt_addition(decision: RuntimeGateDecision) -> str:
    if decision.status == "clean" or not decision.promptPatch:
        return ""
    return (
        "\n[RUNTIME GATE]\n"
        f"status={decision.status}; risk={decision.riskLevel}; recommendedAction={decision.recommendedAction}\n"
        f"{decision.promptPatch}\n"
        "[/RUNTIME GATE]\n"
    )


runtime_reflex_service = RuntimeReflexService()
runtime_preflight_gate = RuntimePreflightGate()
runtime_evidence_feedback_service = RuntimeEvidenceFeedbackService()
