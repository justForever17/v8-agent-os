from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

from core.database import db
from core.multimodal_payload_adapter import utc_now_iso
from runtimes.computer_use.live_matrix_feedback import visual_acceptance_feedback_snapshot
from runtimes.computer_use.selector_memory import ComputerUseSelectorMemory
from runtimes.rpa.compiler import RPATraceCompiler, rpa_trace_compiler
from runtimes.rpa.promotion_gate import (
    draft_environment_signal_summary,
    draft_promotion_gate_summary,
    draft_timing_signal_summary,
    draft_visual_signal_summary,
)
from runtimes.rpa.store import RPAScriptStore, rpa_script_store
from runtimes.rpa.execution_semantics import normalize_script_assessment_status, outcome_family_for_execution_state
from runtimes.rpa.trust_policy import (
    TEMPLATE_TRUST_POLICY_VERSION,
    draft_template_governance_summary,
    evaluate_template_governance,
)


class RPATemplateService:
    _ALLOWED_STATUSES = {"candidate", "review_required", "approved", "rejected", "frozen"}
    _AUTO_PROMOTE_MIN_RUNS = 3
    _AUTO_PROMOTE_COMPLETED_RATE = 0.9
    _AUTO_PROMOTE_MAX_FALLBACK_HEAVY = 0.15
    _AUTO_PROMOTE_MAX_REVIEW_REQUIRED = 0.15
    _AUTO_FREEZE_FALLBACK_HEAVY = 0.35
    _AUTO_FREEZE_REVIEW_REQUIRED = 0.3
    _AUTO_FREEZE_LOCAL_REPAIR = 0.35
    _STATUS_LABELS = {
        "candidate": "候选模板",
        "review_required": "待人工复核",
        "approved": "已批准",
        "rejected": "已拒绝",
        "frozen": "已冻结",
    }
    _STAGE_LABELS = {
        "candidate": "继续积累样本",
        "shadow_ready": "Shadow Candidate",
        "approval_ready": "可进入审批",
        "approved_live": "主执行模板",
        "approved_at_risk": "已批准但存在退化风险",
        "frozen_hold": "冻结观察",
        "rejected_hold": "拒绝保留",
    }
    _DECISION_LABELS = {
        "approve": "建议批准",
        "freeze": "建议冻结",
        "keep_candidate": "继续保留候选",
        "keep_approved": "维持批准状态",
        "keep_rejected": "维持拒绝状态",
        "review_required": "建议人工复核",
    }
    _ROLLOUT_LABELS = {
        "template_preferred": "优先执行模板",
        "template_preferred_with_fallback": "模板优先，失败后自动回退",
        "candidate_shadow": "Shadow 运行，继续积累样本",
        "computer_use_first": "Computer Use 优先",
    }
    _EXECUTION_PATH_LABELS = {
        "template_preferred": "模板主执行链",
        "template_preferred_with_fallback": "模板主执行链，失败时回退 Computer Use",
        "candidate_shadow": "模板 Shadow 执行链",
        "computer_use_first": "Computer Use 主执行链",
    }
    _RISK_FLAG_LABELS = {
        "compile_issues": "存在编译问题",
        "repair_heavy": "局部修补偏多",
        "fallback_heavy": "历史回退偏高",
        "review_heavy": "人工复核偏高",
        "profile_augmented_heavy": "依赖 profile/视觉合成定位",
        "frozen": "模板冻结",
        "rejected": "模板已拒绝",
        "approved_at_risk": "批准模板存在退化风险",
    }
    _EXECUTION_MODE_LABELS = {
        "reuse_mode": "直接复用既有肌肉记忆",
        "hybrid_mode": "部分复用，局部进入学习模式",
        "learn_mode": "进入 Computer Use 学习模式",
    }
    _ROUTE_ACTION_LABELS = {
        "run_template_reuse": "直接复用模板/RPA 肌肉记忆",
        "reuse_with_variable_binding": "先补变量，再复用模板",
        "run_hybrid_with_computer_use": "使用既有肌肉记忆作骨架，Computer Use 补足缺口",
        "start_computer_use_learning": "启动 Computer Use 学习模式",
    }

    def __init__(
        self,
        *,
        compiler: RPATraceCompiler = rpa_trace_compiler,
        script_store: RPAScriptStore = rpa_script_store,
        selector_memory: ComputerUseSelectorMemory | None = None,
    ) -> None:
        self.compiler = compiler
        self.script_store = script_store
        self.selector_memory = selector_memory or ComputerUseSelectorMemory()

    def _normalize_goal_text(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    def _tokenize_goal(self, value: Any) -> list[str]:
        normalized = self._normalize_goal_text(value)
        if not normalized:
            return []
        tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalized)
        expanded: list[str] = []
        for token in tokens:
            expanded.append(token)
            if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) >= 2:
                expanded.extend(token[index : index + 2] for index in range(0, len(token) - 1))
        return list(dict.fromkeys(item for item in expanded if item))

    def _goal_match_score(self, query_goal: Any, candidate_goal: Any) -> float:
        left = self._normalize_goal_text(query_goal)
        right = self._normalize_goal_text(candidate_goal)
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        direct_bonus = 0.0
        if left in right or right in left:
            direct_bonus = 0.18
        left_tokens = set(self._tokenize_goal(left))
        right_tokens = set(self._tokenize_goal(right))
        token_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens) if (left_tokens or right_tokens) else 0.0
        ratio_score = SequenceMatcher(None, left, right).ratio()
        return round(min(1.0, max(token_score, ratio_score) + direct_bonus), 4)

    def _variable_binding_summary(
        self,
        *,
        variables: list[Dict[str, Any]] | list[Any],
        provided_variables: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        provided = {str(key).strip(): value for key, value in dict(provided_variables or {}).items() if str(key).strip()}
        required_names: list[str] = []
        optional_names: list[str] = []
        for item in list(variables or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if bool(item.get("required", True)):
                required_names.append(name)
            else:
                optional_names.append(name)
        required_unique = list(dict.fromkeys(required_names))
        optional_unique = list(dict.fromkeys(optional_names))
        missing_required = [name for name in required_unique if name not in provided or provided.get(name) in (None, "", [], {})]
        bound_variables = [name for name in required_unique if name not in missing_required]
        extra_variables = [name for name in provided.keys() if name not in set(required_unique + optional_unique)]
        return {
            "requiredVariables": required_unique,
            "optionalVariables": optional_unique,
            "providedVariables": list(provided.keys()),
            "missingVariables": missing_required,
            "boundVariables": bound_variables,
            "extraVariables": extra_variables,
            "requiresVariableBinding": bool(missing_required),
            "variableOnlyReusable": True,
        }

    def _match_quality_weight(self, *, status: str, stage: str, rollout_mode: str, kind: str) -> float:
        if kind == "template":
            if status == "approved":
                return 1.0 if stage == "approved_live" else 0.9
            if stage == "approval_ready":
                return 0.82
            if stage == "shadow_ready":
                return 0.74
            if status == "review_required":
                return 0.48
            if status in {"frozen", "rejected"}:
                return 0.28
            if rollout_mode == "computer_use_first":
                return 0.58
            return 0.64
        if rollout_mode in {"candidate_shadow", "template_preferred_with_fallback"}:
            return 0.72
        return 0.6

    def _route_mode_for_match(
        self,
        *,
        kind: str,
        status: str,
        stage: str,
        rollout_mode: str,
        goal_score: float,
    ) -> str:
        if kind == "template" and status == "approved" and stage == "approved_live" and rollout_mode in {"template_preferred", "template_preferred_with_fallback"} and goal_score >= 0.45:
            return "reuse_mode"
        if stage in {"approval_ready", "shadow_ready", "approved_at_risk"} or rollout_mode in {"candidate_shadow", "computer_use_first", "template_preferred_with_fallback"}:
            return "hybrid_mode"
        if kind == "draft" and goal_score >= 0.42:
            return "hybrid_mode"
        return "learn_mode"

    def _route_action_for_mode(self, *, mode: str, requires_variable_binding: bool) -> str:
        if mode == "reuse_mode":
            return "reuse_with_variable_binding" if requires_variable_binding else "run_template_reuse"
        if mode == "hybrid_mode":
            return "run_hybrid_with_computer_use"
        return "start_computer_use_learning"

    def _augment_promotion_gate_signals(self, signals: Dict[str, Any] | None) -> Dict[str, Any]:
        payload = dict(signals or {})
        if int(payload.get("visualLocatorBackedSteps") or 0) <= 0:
            return payload
        acceptance_snapshot = visual_acceptance_feedback_snapshot(recent_limit=5)
        acceptances = dict(acceptance_snapshot.get("acceptances") or {})
        payload["recentVisualAcceptanceStatus"] = acceptance_snapshot.get("aggregateStatus")
        payload["recentVisualAcceptanceCount"] = int(acceptance_snapshot.get("acceptanceCount") or 0)
        payload["recentVisualAcceptanceReportCount"] = int(acceptance_snapshot.get("reportCount") or 0)
        payload["recentVisualAcceptanceIds"] = list(acceptances.keys())
        payload["recentVisualAcceptanceReadTextCaseCount"] = sum(
            int(dict(item).get("visualLocatorReadTextCaseCount") or 0)
            for item in acceptances.values()
            if isinstance(item, dict)
        )
        payload["recentVisualAcceptanceOcrEnhancedCaseCount"] = sum(
            int(dict(item).get("ocrEnhancedCaseCount") or 0)
            for item in acceptances.values()
            if isinstance(item, dict)
        )
        payload["recentVisualAcceptanceSummaries"] = [
            {
                "acceptanceId": str(key),
                "status": str(dict(item).get("status") or "").strip(),
                "latestReportPath": str(dict(item).get("latestReportPath") or "").strip(),
                "reportCount": int(dict(item).get("reportCount") or 0),
                "passRate": float(dict(item).get("passRate") or 0.0),
                "visualLocatorCaseCount": int(dict(item).get("visualLocatorCaseCount") or 0),
                "ocrEnhancedCaseCount": int(dict(item).get("ocrEnhancedCaseCount") or 0),
            }
            for key, item in acceptances.items()
            if isinstance(item, dict)
        ]
        return payload

    def _match_templates_for_goal(
        self,
        *,
        goal: str,
        app_id: str | None,
        variables: Dict[str, Any] | None,
        limit: int,
    ) -> list[Dict[str, Any]]:
        normalized_app = str(app_id or "").strip().lower()
        matches: list[Dict[str, Any]] = []
        for template in self.list_templates(limit=max(1, limit * 4), app_id=app_id):
            if not isinstance(template, dict):
                continue
            current_app_id = str(template.get("appId") or "").strip().lower()
            goal_score = self._goal_match_score(goal, template.get("goal"))
            if normalized_app and current_app_id and current_app_id != normalized_app:
                goal_score = max(0.0, goal_score - 0.22)
            governance = draft_template_governance_summary(dict(template.get("governance") or {}))
            view = dict(template.get("view") or {})
            status = self._normalize_status(template.get("status"))
            stage = str(governance.get("stage") or "").strip() or "candidate"
            rollout_mode = str(governance.get("rolloutMode") or "").strip() or "computer_use_first"
            promotion_gate_status = str(view.get("promotionGateStatus") or "").strip() or (
                "passed" if status == "approved" and stage == "approved_live" else "blocked"
            )
            promotion_gate_blocked = (
                bool(view.get("promotionGateBlocked"))
                if "promotionGateBlocked" in view
                else promotion_gate_status != "passed"
            )
            promotion_gate_signals = self._augment_promotion_gate_signals(dict(view.get("promotionGateSignals") or {}))
            timing_signal_summary = draft_timing_signal_summary(
                {"signals": promotion_gate_signals},
                metadata=dict(template.get("metadata") or {}),
            )
            environment_signal_summary = draft_environment_signal_summary(
                {"signals": promotion_gate_signals},
                metadata=dict(template.get("metadata") or {}),
            )
            binding = self._variable_binding_summary(
                variables=list(template.get("variables") or []),
                provided_variables=variables,
            )
            quality_weight = self._match_quality_weight(
                status=status,
                stage=stage,
                rollout_mode=rollout_mode,
                kind="template",
            )
            app_bonus = 0.2 if normalized_app and current_app_id == normalized_app else 0.0
            score = round(min(1.0, goal_score * 0.68 + quality_weight * 0.32 + app_bonus), 4)
            if score < 0.28:
                continue
            route_mode = self._route_mode_for_match(
                kind="template",
                status=status,
                stage=stage,
                rollout_mode=rollout_mode,
                goal_score=goal_score,
            )
            route_action = self._route_action_for_mode(
                mode=route_mode,
                requires_variable_binding=bool(binding.get("requiresVariableBinding")),
            )
            matches.append(
                {
                    "kind": "template",
                    "id": template.get("id"),
                    "name": template.get("name"),
                    "appId": template.get("appId"),
                    "goal": template.get("goal"),
                    "status": status,
                    "stage": stage,
                    "rolloutMode": rollout_mode,
                    "confidence": governance.get("confidence"),
                    "goalScore": goal_score,
                    "score": score,
                    "routeMode": route_mode,
                    "routeAction": route_action,
                    "reusable": route_mode == "reuse_mode",
                    "requiresVariableBinding": bool(binding.get("requiresVariableBinding")),
                    "missingVariables": list(binding.get("missingVariables") or []),
                    "providedVariables": list(binding.get("providedVariables") or []),
                    "sourceTraceCount": int((template.get("view") or {}).get("signalSummary", {}).get("sourceTraceCount") or 0),
                    "reasons": list(governance.get("reasons") or [])[:3],
                    "executionPath": rollout_mode,
                    "humanReviewState": view.get("reviewSummary"),
                    "promotionGateStatus": promotion_gate_status,
                    "promotionGateBlocked": promotion_gate_blocked,
                    "promotionGateReasons": list(view.get("promotionGateReasons") or [])[:5],
                    "promotionGateSignals": promotion_gate_signals,
                    "visualSignalSummary": draft_visual_signal_summary(
                        {"signals": promotion_gate_signals},
                        metadata=dict(template.get("metadata") or {}),
                    ),
                    "timingSignalSummary": timing_signal_summary,
                    "environmentSignalSummary": environment_signal_summary,
                }
            )
        return sorted(matches, key=lambda item: (float(item.get("score") or 0.0), float(item.get("confidence") or 0.0)), reverse=True)[:limit]

    def _match_drafts_for_goal(
        self,
        *,
        goal: str,
        app_id: str | None,
        variables: Dict[str, Any] | None,
        limit: int,
    ) -> list[Dict[str, Any]]:
        normalized_app = str(app_id or "").strip().lower()
        matches: list[Dict[str, Any]] = []
        for draft in self.script_store.list_drafts(limit=max(1, limit * 4)):
            if not isinstance(draft, dict):
                continue
            current_app_id = str(draft.get("appId") or "").strip().lower()
            if normalized_app and current_app_id and current_app_id != normalized_app:
                continue
            goal_score = self._goal_match_score(goal, draft.get("goal"))
            metadata = dict(draft.get("metadata") or {})
            governance = draft_template_governance_summary(dict(metadata.get("templateGovernance") or {}))
            status = self._normalize_status(metadata.get("templateStatus") or governance.get("templateStatus") or "candidate")
            stage = str(governance.get("stage") or metadata.get("templateGovernanceStage") or "").strip() or "candidate"
            rollout_mode = str(governance.get("rolloutMode") or metadata.get("templateRolloutMode") or "").strip() or "computer_use_first"
            promotion_gate_status = str(metadata.get("templatePromotionGateStatus") or "").strip() or (
                "passed" if status == "approved" and stage == "approved_live" else "blocked"
            )
            promotion_gate_blocked = (
                bool(metadata.get("templatePromotionGateBlocked"))
                if "templatePromotionGateBlocked" in metadata
                else promotion_gate_status != "passed"
            )
            promotion_gate_signals = self._augment_promotion_gate_signals(dict(metadata.get("templatePromotionGateSignals") or {}))
            timing_signal_summary = draft_timing_signal_summary(
                {"signals": promotion_gate_signals},
                metadata=metadata,
            )
            environment_signal_summary = draft_environment_signal_summary(
                {"signals": promotion_gate_signals},
                metadata=metadata,
            )
            binding = self._variable_binding_summary(
                variables=list(draft.get("variables") or []),
                provided_variables=variables,
            )
            quality_weight = self._match_quality_weight(
                status=status,
                stage=stage,
                rollout_mode=rollout_mode,
                kind="draft",
            )
            score = round(min(1.0, goal_score * 0.72 + quality_weight * 0.28), 4)
            if score < 0.3:
                continue
            route_mode = self._route_mode_for_match(
                kind="draft",
                status=status,
                stage=stage,
                rollout_mode=rollout_mode,
                goal_score=goal_score,
            )
            matches.append(
                {
                    "kind": "draft",
                    "id": draft.get("id"),
                    "name": draft.get("name"),
                    "appId": draft.get("appId"),
                    "goal": draft.get("goal"),
                    "status": status,
                    "stage": stage,
                    "rolloutMode": rollout_mode,
                    "confidence": metadata.get("templateTrustConfidence"),
                    "goalScore": goal_score,
                    "score": score,
                    "routeMode": route_mode,
                    "routeAction": self._route_action_for_mode(
                        mode=route_mode,
                        requires_variable_binding=bool(binding.get("requiresVariableBinding")),
                    ),
                    "reusable": False,
                    "requiresVariableBinding": bool(binding.get("requiresVariableBinding")),
                    "missingVariables": list(binding.get("missingVariables") or []),
                    "providedVariables": list(binding.get("providedVariables") or []),
                    "sourceTraceCount": int(metadata.get("templateSourceTraceCount") or 0),
                    "reasons": list((draft.get("assessment") or {}).get("reasons") or [])[:3],
                    "executionPath": rollout_mode,
                    "promotionGateStatus": promotion_gate_status,
                    "promotionGateBlocked": promotion_gate_blocked,
                    "promotionGateReasons": list(metadata.get("templatePromotionGateReasons") or [])[:5],
                    "promotionGateSignals": promotion_gate_signals,
                    "visualSignalSummary": draft_visual_signal_summary(
                        {"signals": promotion_gate_signals},
                        metadata=metadata,
                    ),
                    "timingSignalSummary": timing_signal_summary,
                    "environmentSignalSummary": environment_signal_summary,
                }
            )
        return sorted(matches, key=lambda item: (float(item.get("score") or 0.0), float(item.get("confidence") or 0.0)), reverse=True)[:limit]

    def _normalize_status(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in self._ALLOWED_STATUSES:
            return normalized
        return "candidate"

    def _build_risk_flags(self, *, governance: Dict[str, Any], status: str) -> list[str]:
        flags: list[str] = []
        signals = dict(governance.get("signals") or {})
        stage = str(governance.get("stage") or "").strip()
        if int(signals.get("compileIssueCount") or 0) > 0:
            flags.append("compile_issues")
        if int(signals.get("localRepairCount") or 0) > 0:
            flags.append("repair_heavy")
        if float(signals.get("historicalFallbackHeavyRate") or 0.0) >= 0.25:
            flags.append("fallback_heavy")
        if float(signals.get("historicalReviewRequiredRate") or 0.0) >= 0.3:
            flags.append("review_heavy")
        if float(signals.get("historicalProfileAugmentedRatio") or 0.0) >= 0.6:
            flags.append("profile_augmented_heavy")
        if status == "frozen":
            flags.append("frozen")
        if status == "rejected":
            flags.append("rejected")
        if stage == "approved_at_risk":
            flags.append("approved_at_risk")
        return list(dict.fromkeys(flags))

    def _build_review_summary(self, *, metadata: Dict[str, Any], governance: Dict[str, Any]) -> Dict[str, Any]:
        review_history = [dict(item) for item in list(metadata.get("reviewHistory") or []) if isinstance(item, dict)]
        counts = {
            "approve": 0,
            "freeze": 0,
            "rollback": 0,
            "review_required": 0,
            "rejected": 0,
        }
        for item in review_history:
            decision = str(item.get("decision") or "").strip().lower()
            if decision in counts:
                counts[decision] += 1
        last_review = review_history[-1] if review_history else {}
        return {
            "total": len(review_history),
            "approveCount": counts["approve"],
            "freezeCount": counts["freeze"],
            "rollbackCount": counts["rollback"],
            "reviewRequiredCount": counts["review_required"],
            "rejectedCount": counts["rejected"],
            "lastDecision": str(last_review.get("decision") or metadata.get("templateStatus") or "").strip() or None,
            "lastDecisionLabel": self._DECISION_LABELS.get(str(last_review.get("decision") or "").strip().lower()),
            "lastReviewer": str(last_review.get("reviewer") or metadata.get("lastReviewer") or "").strip() or None,
            "lastReviewedAt": last_review.get("at") or metadata.get("lastReviewedAt"),
            "approvalRequired": bool(governance.get("approvalRequired")),
        }

    def _build_template_view(self, template: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(template or {})
        metadata = dict(payload.get("metadata") or {})
        governance = draft_template_governance_summary(dict(payload.get("governance") or {}))
        promotion_gate = dict(payload.get("promotionGate") or {})
        status = self._normalize_status(payload.get("status") or metadata.get("templateStatus"))
        stage = str(governance.get("stage") or "").strip()
        rollout_mode = str(governance.get("rolloutMode") or "").strip()
        risk_flags = self._build_risk_flags(governance=governance, status=status)
        review_summary = self._build_review_summary(metadata=metadata, governance=governance)
        confidence = governance.get("confidence")
        confidence_label = "n/a"
        if isinstance(confidence, (int, float)):
            confidence_label = f"{round(float(confidence) * 100)}%"
        promotion_gate_signals = self._augment_promotion_gate_signals(dict(promotion_gate.get("signals") or {}))
        visual_signal_summary = draft_visual_signal_summary(
            {"signals": promotion_gate_signals},
            metadata=metadata,
        )
        timing_signal_summary = draft_timing_signal_summary(
            {"signals": promotion_gate_signals},
            metadata=metadata,
        )
        environment_signal_summary = draft_environment_signal_summary(
            {"signals": promotion_gate_signals},
            metadata=metadata,
        )
        return {
            "statusLabel": self._STATUS_LABELS.get(status, status or "未知状态"),
            "stageLabel": self._STAGE_LABELS.get(stage, stage or "未分级"),
            "recommendedDecisionLabel": self._DECISION_LABELS.get(
                str(governance.get("recommendedDecision") or "").strip().lower(),
                str(governance.get("recommendedDecision") or "").strip() or "未建议",
            ),
            "rolloutModeLabel": self._ROLLOUT_LABELS.get(rollout_mode, rollout_mode or "未指定"),
            "executionPath": rollout_mode,
            "executionPathLabel": self._EXECUTION_PATH_LABELS.get(rollout_mode, rollout_mode or "未指定"),
            "confidenceLabel": confidence_label,
            "riskFlags": risk_flags,
            "riskFlagLabels": [self._RISK_FLAG_LABELS.get(item, item) for item in risk_flags],
            "reviewSummary": review_summary,
            "signalSummary": {
                "sourceTraceCount": int(governance.get("signals", {}).get("sourceTraceCount") or 0),
                "localRepairCount": int(governance.get("signals", {}).get("localRepairCount") or 0),
                "compileIssueCount": int(governance.get("signals", {}).get("compileIssueCount") or 0),
                "historicalRuns": int(governance.get("signals", {}).get("historicalRuns") or 0),
                "historicalCompletedRate": governance.get("signals", {}).get("historicalCompletedRate"),
                "historicalFallbackHeavyRate": governance.get("signals", {}).get("historicalFallbackHeavyRate"),
                "historicalReviewRequiredRate": governance.get("signals", {}).get("historicalReviewRequiredRate"),
            },
            "promotionGateStatus": promotion_gate.get("status"),
            "promotionGateBlocked": bool(promotion_gate.get("blockedPromotion")),
            "promotionGateReasons": list(promotion_gate.get("reasons") or [])[:5],
            "promotionGateSignals": promotion_gate_signals,
            "visualSignalSummary": visual_signal_summary,
            "timingSignalSummary": timing_signal_summary,
            "environmentSignalSummary": environment_signal_summary,
        }

    def _build_history_view(self, item: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(item or {})
        view = dict(payload.get("view") or {})
        return {
            "revision": int(payload.get("revision") or 0),
            "statusLabel": self._STATUS_LABELS.get(self._normalize_status(payload.get("status")), str(payload.get("status") or "未知状态")),
            "reason": payload.get("reason"),
            "actor": payload.get("actor"),
            "at": payload.get("at"),
            "visualSignalSummary": dict(view.get("visualSignalSummary") or {}),
            "timingSignalSummary": dict(view.get("timingSignalSummary") or {}),
            "environmentSignalSummary": dict(view.get("environmentSignalSummary") or {}),
        }

    def _ensure_template(self, template_id: str) -> Dict[str, Any]:
        payload = self.script_store.get_template(template_id)
        if not isinstance(payload, dict):
            raise ValueError(f"未找到 template: {template_id}")
        return self._decorate_template(payload)

    def _decorate_template(self, template: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(template or {})
        metadata = dict(payload.get("metadata") or {})
        status = self._normalize_status(metadata.get("templateStatus") or payload.get("status"))
        metadata["templateStatus"] = status
        script_id = str(((payload.get("source") or {}).get("draftId")) or "").strip()
        fingerprint = str(metadata.get("fingerprint") or "").strip()
        calibration = self.script_store.get_script_calibration(
            script_id=script_id,
            fingerprint=fingerprint or None,
        ) if script_id else {}
        governance = evaluate_template_governance(
            template_payload={**payload, "metadata": metadata, "status": status},
            calibration=calibration,
        )
        payload["governance"] = governance
        metadata["templateTrustPolicyVersion"] = TEMPLATE_TRUST_POLICY_VERSION
        payload["metadata"] = metadata
        payload["status"] = status
        payload["view"] = self._build_template_view(payload)
        return payload

    def _build_governance_event(
        self,
        template: Dict[str, Any],
        *,
        event_type: str,
        actor: str,
        execution_state: str | None = None,
        feedback: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = dict(template or {})
        metadata = dict(payload.get("metadata") or {})
        governance = draft_template_governance_summary(dict(payload.get("governance") or {}))
        source = dict(payload.get("source") or {})
        return {
            "eventType": str(event_type or "template_update").strip() or "template_update",
            "actor": str(actor or "system").strip() or "system",
            "templateId": payload.get("id"),
            "draftId": source.get("draftId"),
            "status": payload.get("status"),
            "stage": governance.get("stage"),
            "rolloutMode": governance.get("rolloutMode"),
            "executionPath": governance.get("rolloutMode"),
            "recommendedDecision": governance.get("recommendedDecision"),
            "confidence": governance.get("confidence"),
            "executionState": str(execution_state or "").strip() or None,
            "outcomeFamily": outcome_family_for_execution_state(execution_state),
            "decisionScope": metadata.get("decisionScope") or governance.get("signals", {}).get("decisionScope"),
            "decisionReasonGroup": metadata.get("decisionReasonGroup") or governance.get("signals", {}).get("decisionReasonGroup"),
            "decisionSignals": dict(metadata.get("decisionSignals") or governance.get("signals", {}).get("decisionSignals") or {}),
            "targetStrategyKeys": list(metadata.get("targetStrategyKeys") or []),
            "attachmentCapabilities": list(metadata.get("attachmentCapabilities") or []),
            "reason": " / ".join([str(item) for item in list(governance.get("reasons") or [])[:2]]),
            "updatedAt": utc_now_iso(),
            "feedback": dict(feedback or {}),
        }

    def _append_governance_event(self, metadata: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
        next_metadata = dict(metadata or {})
        history = [dict(item) for item in list(next_metadata.get("governanceEvents") or []) if isinstance(item, dict)]
        history.append(dict(event))
        next_metadata["governanceEvents"] = history[-20:]
        next_metadata["lastTemplateGovernanceEvent"] = dict(event)
        return next_metadata

    def _remember_governance_event(self, template: Dict[str, Any], event: Dict[str, Any]) -> None:
        try:
            app_id = str(template.get("appId") or "").strip() or "desktop"
            governance = dict(template.get("governance") or {})
            stage = str(governance.get("stage") or "").strip().lower()
            weight = 52
            if stage == "approved_live":
                weight = 68
            elif stage in {"approved_at_risk", "frozen_hold", "rejected_hold"}:
                weight = 74
            self.selector_memory.remember_governance_event(
                app_id=app_id,
                event=event,
                source="rpa_template_governance",
                reason=event.get("reason"),
                weight=weight,
            )
        except Exception:
            pass

    def _append_unique_metadata_item(
        self,
        metadata: Dict[str, Any],
        *,
        field: str,
        item: Dict[str, Any],
        limit: int = 20,
    ) -> None:
        current = [dict(existing) for existing in list(metadata.get(field) or []) if isinstance(existing, dict)]
        signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
        deduped = [existing for existing in current if json.dumps(existing, ensure_ascii=False, sort_keys=True) != signature]
        deduped.insert(0, dict(item))
        metadata[field] = deduped[: max(1, int(limit))]

    def _consume_execution_feedback(
        self,
        *,
        template: Dict[str, Any],
        metadata: Dict[str, Any],
        feedback: Dict[str, Any] | None,
    ) -> None:
        feedback_payload = dict(feedback or {})
        app_id = str(template.get("appId") or "desktop").strip() or "desktop"
        selector_candidate = dict(feedback_payload.get("selectorMemoryCandidate") or {})
        if selector_candidate:
            selector = dict(selector_candidate.get("selector") or {})
            if selector:
                try:
                    self.selector_memory.remember(
                        app_id=str(selector_candidate.get("appId") or app_id).strip() or app_id,
                        selector=selector,
                        source="rpa_feedback_selector",
                        reason=str(selector_candidate.get("reason") or "rpa_feedback_selector").strip() or "rpa_feedback_selector",
                        weight=36,
                    )
                except Exception:
                    pass
                self._append_unique_metadata_item(
                    metadata,
                    field="selectorMemoryCandidates",
                    item=selector_candidate,
                )
        app_profile_recommendation = dict(feedback_payload.get("appProfileRecommendation") or {})
        if app_profile_recommendation:
            self._append_unique_metadata_item(
                metadata,
                field="appProfileRecommendations",
                item=app_profile_recommendation,
            )
        playbook_recommendation = dict(feedback_payload.get("playbookRecommendation") or {})
        if playbook_recommendation:
            self._append_unique_metadata_item(
                metadata,
                field="playbookRecommendations",
                item=playbook_recommendation,
            )
        preflight_hints = [dict(item) for item in list(feedback_payload.get("preflightHints") or []) if isinstance(item, dict)]
        if preflight_hints:
            for hint in preflight_hints:
                self._append_unique_metadata_item(
                    metadata,
                    field="preflightHints",
                    item=hint,
                    limit=40,
                )
        if selector_candidate or app_profile_recommendation or playbook_recommendation or preflight_hints:
            metadata["lastFeedbackSuggestions"] = {
                "selectorMemoryCandidate": selector_candidate or None,
                "appProfileRecommendation": app_profile_recommendation or None,
                "playbookRecommendation": playbook_recommendation or None,
                "preflightHints": preflight_hints,
            }

    def _auto_transition_decision(
        self,
        template: Dict[str, Any],
        *,
        execution_state: str | None = None,
    ) -> Dict[str, Any] | None:
        payload = dict(template or {})
        governance = dict(payload.get("governance") or {})
        status = self._normalize_status(payload.get("status"))
        stage = str(governance.get("stage") or "").strip()
        signals = dict(governance.get("signals") or {})
        historical_runs = int(signals.get("historicalRuns") or 0)
        completed_rate = float(signals.get("historicalCompletedRate") or 0.0)
        fallback_heavy_rate = float(signals.get("historicalFallbackHeavyRate") or 0.0)
        review_required_rate = float(signals.get("historicalReviewRequiredRate") or 0.0)
        local_repair_rate = float(signals.get("historicalLocalRepairRate") or 0.0)

        if (
            status in {"candidate", "review_required"}
            and stage == "approval_ready"
            and historical_runs >= self._AUTO_PROMOTE_MIN_RUNS
            and completed_rate >= self._AUTO_PROMOTE_COMPLETED_RATE
            and fallback_heavy_rate <= self._AUTO_PROMOTE_MAX_FALLBACK_HEAVY
            and review_required_rate <= self._AUTO_PROMOTE_MAX_REVIEW_REQUIRED
        ):
            return {
                "decision": "approved",
                "reason": "auto_promote_approval_ready",
                "notes": f"模板满足自动提级阈值：runs={historical_runs}, completedRate={completed_rate}",
            }
        if status == "approved" and (
            stage == "approved_at_risk"
            or execution_state in {"failed", "fallback_failed", "compile_blocked"}
            or fallback_heavy_rate >= self._AUTO_FREEZE_FALLBACK_HEAVY
            or review_required_rate >= self._AUTO_FREEZE_REVIEW_REQUIRED
            or local_repair_rate >= self._AUTO_FREEZE_LOCAL_REPAIR
        ):
            return {
                "decision": "frozen",
                "reason": "auto_freeze_risk_detected",
                "notes": f"模板退化信号触发自动冻结：state={execution_state}, fallbackHeavy={fallback_heavy_rate}, reviewRequired={review_required_rate}, localRepair={local_repair_rate}",
            }
        if status == "candidate" and execution_state in {"fallback_failed", "compile_blocked"}:
            return {
                "decision": "review_required",
                "reason": "auto_review_required_after_failure",
                "notes": f"模板在 {execution_state} 后自动进入待复核。",
            }
        return None

    def _sync_linked_draft_metadata(self, template: Dict[str, Any]) -> None:
        source = dict(template.get("source") or {})
        draft_id = str(source.get("draftId") or "").strip()
        if not draft_id:
            return
        draft = self.script_store.get_draft(draft_id)
        if not isinstance(draft, dict):
            return
        metadata = dict(draft.get("metadata") or {})
        governance = draft_template_governance_summary(dict(template.get("governance") or {}))
        metadata["templateGovernance"] = governance
        metadata["templateGovernanceStage"] = governance.get("stage")
        metadata["templateRecommendedDecision"] = governance.get("recommendedDecision")
        metadata["templateTrustConfidence"] = governance.get("confidence")
        metadata["templatePreferExecution"] = bool(governance.get("preferTemplateExecution"))
        metadata["templateRolloutMode"] = governance.get("rolloutMode")
        metadata["templateTrustPolicyVersion"] = governance.get("version")
        metadata["templateSourceTraceCount"] = dict(template.get("metadata") or {}).get("sourceTraceCount")
        metadata["templateCandidateRevision"] = dict(template.get("metadata") or {}).get("revision")
        metadata["templateStatus"] = template.get("status")
        promotion_gate = draft_promotion_gate_summary(dict(template.get("promotionGate") or {}))
        metadata["templatePromotionGate"] = promotion_gate
        metadata["templatePromotionGateStatus"] = promotion_gate.get("status")
        metadata["templatePromotionGateBlocked"] = bool(promotion_gate.get("blockedPromotion"))
        metadata["templatePromotionGateReasons"] = list(promotion_gate.get("reasons") or [])[:5]
        metadata["templatePromotionGateSignals"] = dict(promotion_gate.get("signals") or {})
        metadata["templatePromotionEligible"] = bool(promotion_gate.get("eligible"))
        metadata["templatePromotionGateVersion"] = promotion_gate.get("version")
        metadata["templateGovernanceEvents"] = [dict(item) for item in list(dict(template.get("metadata") or {}).get("governanceEvents") or []) if isinstance(item, dict)][-20:]
        metadata["lastTemplateGovernanceEvent"] = dict(dict(template.get("metadata") or {}).get("lastTemplateGovernanceEvent") or {})
        metadata["templateGovernanceStats"] = dict(dict(template.get("metadata") or {}).get("governanceStats") or {})
        metadata["templateLastExecutionState"] = dict(template.get("metadata") or {}).get("lastExecutionState")
        metadata["templateLastOutcomeFamily"] = dict(template.get("metadata") or {}).get("lastOutcomeFamily")
        metadata["templateLastExecutedAt"] = dict(template.get("metadata") or {}).get("lastExecutedAt")
        metadata["selectorMemoryCandidates"] = [dict(item) for item in list(dict(template.get("metadata") or {}).get("selectorMemoryCandidates") or []) if isinstance(item, dict)][-20:]
        metadata["appProfileRecommendations"] = [dict(item) for item in list(dict(template.get("metadata") or {}).get("appProfileRecommendations") or []) if isinstance(item, dict)][-20:]
        metadata["playbookRecommendations"] = [dict(item) for item in list(dict(template.get("metadata") or {}).get("playbookRecommendations") or []) if isinstance(item, dict)][-20:]
        metadata["preflightHints"] = [dict(item) for item in list(dict(template.get("metadata") or {}).get("preflightHints") or []) if isinstance(item, dict)][-40:]
        metadata["lastFeedbackSuggestions"] = dict(dict(template.get("metadata") or {}).get("lastFeedbackSuggestions") or {})
        metadata["decisionScope"] = dict(template.get("metadata") or {}).get("decisionScope") or governance.get("signals", {}).get("decisionScope")
        metadata["decisionReasonGroup"] = dict(template.get("metadata") or {}).get("decisionReasonGroup") or governance.get("signals", {}).get("decisionReasonGroup")
        metadata["decisionSignals"] = dict(dict(template.get("metadata") or {}).get("decisionSignals") or governance.get("signals", {}).get("decisionSignals") or {})
        metadata["templateVisualSignalSummary"] = draft_visual_signal_summary(
            dict(template.get("promotionGate") or {}),
            metadata=dict(template.get("metadata") or {}),
        )
        metadata["templateTimingSignalSummary"] = draft_timing_signal_summary(
            dict(template.get("promotionGate") or {}),
            metadata=dict(template.get("metadata") or {}),
        )
        metadata["templateEnvironmentSignalSummary"] = draft_environment_signal_summary(
            dict(template.get("promotionGate") or {}),
            metadata=dict(template.get("metadata") or {}),
        )
        if str(template.get("id") or "").strip():
            source["templateId"] = template.get("id")
        if dict(template.get("metadata") or {}).get("fingerprint"):
            source["templateFingerprint"] = dict(template.get("metadata") or {}).get("fingerprint")
        if template.get("updatedAt"):
            source["templateUpdatedAt"] = template.get("updatedAt")
        source["templateStatus"] = governance.get("templateStatus")
        source["templateStage"] = governance.get("stage")
        draft["metadata"] = metadata
        draft["source"] = source
        self.script_store.save_draft(draft)

    def _save_template(
        self,
        template: Dict[str, Any],
        *,
        reason: str,
        actor: str,
        execution_state: str | None = None,
        feedback: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        decorated = self._decorate_template(template)
        event = self._build_governance_event(
            decorated,
            event_type=reason,
            actor=actor,
            execution_state=execution_state,
            feedback=feedback,
        )
        metadata = self._append_governance_event(dict(decorated.get("metadata") or {}), event)
        decorated["metadata"] = metadata
        payload = self.script_store.save_template(
            decorated,
            history_reason=reason,
            history_actor=actor,
            write_history=True,
        )
        decorated_payload = self._decorate_template(payload)
        self._sync_linked_draft_metadata(decorated_payload)
        self._remember_governance_event(decorated_payload, event)
        return decorated_payload

    def list_templates(
        self,
        *,
        limit: int = 100,
        app_id: str | None = None,
        status: str | None = None,
    ) -> list[Dict[str, Any]]:
        normalized_status = self._normalize_status(status) if status not in (None, "") else None
        items: list[Dict[str, Any]] = []
        for payload in self.script_store.list_templates(limit=max(1, int(limit) * 3), app_id=app_id):
            decorated = self._decorate_template(payload)
            if normalized_status and decorated.get("status") != normalized_status:
                continue
            items.append(decorated)
            if len(items) >= max(1, int(limit)):
                break
        return items

    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        payload = self.script_store.get_template(template_id)
        if not isinstance(payload, dict):
            return None
        return self._decorate_template(payload)

    def list_template_history(self, template_id: str, *, limit: int = 50) -> list[Dict[str, Any]]:
        history = self.script_store.list_template_history(template_id, limit=limit)
        normalized_items: list[Dict[str, Any]] = []
        for item in history:
            payload = dict(item or {})
            template = payload.get("template")
            if isinstance(template, dict):
                payload["template"] = self._decorate_template(template)
                payload["view"] = dict(payload["template"].get("view") or {})
            payload["historyView"] = self._build_history_view(payload)
            normalized_items.append(payload)
        return normalized_items

    def summarize_templates(self, templates: list[Dict[str, Any]]) -> Dict[str, Any]:
        items = [dict(item) for item in list(templates or []) if isinstance(item, dict)]
        by_status: Dict[str, int] = {}
        by_stage: Dict[str, int] = {}
        max_confidence = 0.0
        at_risk = 0
        template_preferred = 0
        computer_use_first = 0
        review_required = 0
        visual_locator_backed = 0
        visual_judge_backed = 0
        wait_sensitive = 0
        loading_sensitive = 0
        environment_aware = 0
        dialog_aware = 0
        focus_aware = 0
        notification_aware = 0
        sound_aware = 0
        notification_sensing_requested_count = 0
        notification_sensing_available_count = 0
        sound_sensing_requested_count = 0
        sound_sensing_available_count = 0
        notification_observed = 0
        sound_observed = 0
        max_notification_candidate_count = 0
        max_sound_active_session_count = 0
        notification_signal_providers: list[str] = []
        sound_signal_providers: list[str] = []
        notification_sensing_modes: list[str] = []
        sound_sensing_modes: list[str] = []

        def _extend_unique(target: list[str], values: list[Any]) -> None:
            for item in values:
                normalized = str(item or "").strip()
                if normalized and normalized not in target:
                    target.append(normalized)

        for item in items:
            status = self._normalize_status(item.get("status"))
            stage = str(((item.get("governance") or {}).get("stage")) or "").strip() or "candidate"
            rollout_mode = str(((item.get("governance") or {}).get("rolloutMode")) or "").strip()
            confidence = float(((item.get("governance") or {}).get("confidence")) or 0.0)
            view = dict(item.get("view") or {})
            visual_signal_summary = dict(view.get("visualSignalSummary") or {})
            timing_signal_summary = dict(view.get("timingSignalSummary") or {})
            environment_signal_summary = dict(view.get("environmentSignalSummary") or {})
            by_status[status] = by_status.get(status, 0) + 1
            by_stage[stage] = by_stage.get(stage, 0) + 1
            if stage == "approved_at_risk":
                at_risk += 1
            if rollout_mode in {"template_preferred", "template_preferred_with_fallback"}:
                template_preferred += 1
            if rollout_mode == "computer_use_first":
                computer_use_first += 1
            if bool(((item.get("governance") or {}).get("approvalRequired"))):
                review_required += 1
            if bool(visual_signal_summary.get("visualLocatorBacked")):
                visual_locator_backed += 1
            if bool(visual_signal_summary.get("visualJudgeBacked")):
                visual_judge_backed += 1
            if bool(timing_signal_summary.get("waitSensitive")):
                wait_sensitive += 1
            if int(timing_signal_summary.get("loadingSensitiveSteps") or 0) > 0:
                loading_sensitive += 1
            if bool(environment_signal_summary.get("desktopEnvironmentAware")):
                environment_aware += 1
            if int(environment_signal_summary.get("dialogAwareSteps") or 0) > 0:
                dialog_aware += 1
            if int(environment_signal_summary.get("focusAwareSteps") or 0) > 0:
                focus_aware += 1
            if bool(environment_signal_summary.get("notificationSensingRequested")) or bool(
                environment_signal_summary.get("notificationSensingAvailable")
            ):
                notification_aware += 1
            if bool(environment_signal_summary.get("soundSensingRequested")) or bool(
                environment_signal_summary.get("soundSensingAvailable")
            ):
                sound_aware += 1
            if bool(environment_signal_summary.get("notificationSensingRequested")):
                notification_sensing_requested_count += 1
            if bool(environment_signal_summary.get("notificationSensingAvailable")):
                notification_sensing_available_count += 1
            if bool(environment_signal_summary.get("soundSensingRequested")):
                sound_sensing_requested_count += 1
            if bool(environment_signal_summary.get("soundSensingAvailable")):
                sound_sensing_available_count += 1
            if int(environment_signal_summary.get("notificationObservedSteps") or 0) > 0:
                notification_observed += 1
            if int(environment_signal_summary.get("soundObservedSteps") or 0) > 0:
                sound_observed += 1
            max_notification_candidate_count = max(
                max_notification_candidate_count,
                int(environment_signal_summary.get("maxNotificationCandidateCount") or 0),
            )
            max_sound_active_session_count = max(
                max_sound_active_session_count,
                int(environment_signal_summary.get("maxSoundActiveSessionCount") or 0),
            )
            _extend_unique(
                notification_signal_providers,
                list(environment_signal_summary.get("notificationSignalProviders") or []),
            )
            _extend_unique(
                sound_signal_providers,
                list(environment_signal_summary.get("soundSignalProviders") or []),
            )
            _extend_unique(
                notification_sensing_modes,
                [
                    environment_signal_summary.get("notificationSensingMode"),
                    *(list(environment_signal_summary.get("notificationSensingModes") or [])),
                ],
            )
            _extend_unique(
                sound_sensing_modes,
                [
                    environment_signal_summary.get("soundSensingMode"),
                    *(list(environment_signal_summary.get("soundSensingModes") or [])),
                ],
            )
            max_confidence = max(max_confidence, confidence)
        return {
            "total": len(items),
            "byStatus": by_status,
            "byStage": by_stage,
            "templatePreferredCount": template_preferred,
            "computerUseFirstCount": computer_use_first,
            "atRiskCount": at_risk,
            "reviewRequiredCount": review_required,
            "visualLocatorBackedCount": visual_locator_backed,
            "visualJudgeBackedCount": visual_judge_backed,
            "waitSensitiveCount": wait_sensitive,
            "loadingSensitiveCount": loading_sensitive,
            "environmentAwareCount": environment_aware,
            "dialogAwareCount": dialog_aware,
            "focusAwareCount": focus_aware,
            "notificationAwareCount": notification_aware,
            "soundAwareCount": sound_aware,
            "notificationSensingRequested": notification_sensing_requested_count > 0,
            "notificationSensingAvailable": notification_sensing_available_count > 0,
            "soundSensingRequested": sound_sensing_requested_count > 0,
            "soundSensingAvailable": sound_sensing_available_count > 0,
            "notificationSensingRequestedCount": notification_sensing_requested_count,
            "notificationSensingAvailableCount": notification_sensing_available_count,
            "soundSensingRequestedCount": sound_sensing_requested_count,
            "soundSensingAvailableCount": sound_sensing_available_count,
            "notificationObserved": notification_observed > 0,
            "soundObserved": sound_observed > 0,
            "notificationObservedCount": notification_observed,
            "soundObservedCount": sound_observed,
            "notificationSignalProviders": notification_signal_providers,
            "soundSignalProviders": sound_signal_providers,
            "notificationSensingModes": notification_sensing_modes,
            "soundSensingModes": sound_sensing_modes,
            "maxNotificationCandidateCount": max_notification_candidate_count,
            "maxSoundActiveSessionCount": max_sound_active_session_count,
            "maxConfidence": round(max_confidence, 3),
        }

    def _summarize_match_signals(self, matches: list[Dict[str, Any]]) -> Dict[str, Any]:
        items = [dict(item) for item in list(matches or []) if isinstance(item, dict)]
        visual_locator_backed = 0
        visual_judge_backed = 0
        wait_sensitive = 0
        loading_sensitive = 0
        environment_aware = 0
        dialog_aware = 0
        focus_aware = 0
        notification_aware = 0
        sound_aware = 0
        notification_sensing_requested_count = 0
        notification_sensing_available_count = 0
        sound_sensing_requested_count = 0
        sound_sensing_available_count = 0
        notification_observed = 0
        sound_observed = 0
        visual_semantic_roles: list[str] = []
        visual_locator_providers: list[str] = []
        transition_states: list[str] = []
        page_identities: list[str] = []
        notification_signal_providers: list[str] = []
        sound_signal_providers: list[str] = []
        notification_sensing_modes: list[str] = []
        sound_sensing_modes: list[str] = []
        max_notification_candidate_count = 0
        max_sound_active_session_count = 0

        def _extend_unique(target: list[str], values: list[Any]) -> None:
            for item in values:
                normalized = str(item or "").strip()
                if normalized and normalized not in target:
                    target.append(normalized)

        for item in items:
            visual_signal_summary = dict(item.get("visualSignalSummary") or {})
            timing_signal_summary = dict(item.get("timingSignalSummary") or {})
            environment_signal_summary = dict(item.get("environmentSignalSummary") or {})
            if bool(visual_signal_summary.get("visualLocatorBacked")):
                visual_locator_backed += 1
            if bool(visual_signal_summary.get("visualJudgeBacked")):
                visual_judge_backed += 1
            if bool(timing_signal_summary.get("waitSensitive")):
                wait_sensitive += 1
            if int(timing_signal_summary.get("loadingSensitiveSteps") or 0) > 0:
                loading_sensitive += 1
            if bool(environment_signal_summary.get("desktopEnvironmentAware")):
                environment_aware += 1
            if int(environment_signal_summary.get("dialogAwareSteps") or 0) > 0:
                dialog_aware += 1
            if int(environment_signal_summary.get("focusAwareSteps") or 0) > 0:
                focus_aware += 1
            if bool(environment_signal_summary.get("notificationSensingRequested")) or bool(
                environment_signal_summary.get("notificationSensingAvailable")
            ):
                notification_aware += 1
            if bool(environment_signal_summary.get("soundSensingRequested")) or bool(
                environment_signal_summary.get("soundSensingAvailable")
            ):
                sound_aware += 1
            if bool(environment_signal_summary.get("notificationSensingRequested")):
                notification_sensing_requested_count += 1
            if bool(environment_signal_summary.get("notificationSensingAvailable")):
                notification_sensing_available_count += 1
            if bool(environment_signal_summary.get("soundSensingRequested")):
                sound_sensing_requested_count += 1
            if bool(environment_signal_summary.get("soundSensingAvailable")):
                sound_sensing_available_count += 1
            if int(environment_signal_summary.get("notificationObservedSteps") or 0) > 0:
                notification_observed += 1
            if int(environment_signal_summary.get("soundObservedSteps") or 0) > 0:
                sound_observed += 1
            for role in list(visual_signal_summary.get("visualSemanticRoles") or []):
                _extend_unique(visual_semantic_roles, [role])
            for provider in list(visual_signal_summary.get("visualLocatorProviders") or []):
                _extend_unique(visual_locator_providers, [provider])
            for transition_state in list(timing_signal_summary.get("transitionStates") or []):
                _extend_unique(transition_states, [transition_state])
            for page_identity in list(environment_signal_summary.get("pageIdentities") or []):
                _extend_unique(page_identities, [page_identity])
            _extend_unique(
                notification_signal_providers,
                list(environment_signal_summary.get("notificationSignalProviders") or []),
            )
            _extend_unique(
                sound_signal_providers,
                list(environment_signal_summary.get("soundSignalProviders") or []),
            )
            _extend_unique(
                notification_sensing_modes,
                [
                    environment_signal_summary.get("notificationSensingMode"),
                    *(list(environment_signal_summary.get("notificationSensingModes") or [])),
                ],
            )
            _extend_unique(
                sound_sensing_modes,
                [
                    environment_signal_summary.get("soundSensingMode"),
                    *(list(environment_signal_summary.get("soundSensingModes") or [])),
                ],
            )
            max_notification_candidate_count = max(
                max_notification_candidate_count,
                int(environment_signal_summary.get("maxNotificationCandidateCount") or 0),
            )
            max_sound_active_session_count = max(
                max_sound_active_session_count,
                int(environment_signal_summary.get("maxSoundActiveSessionCount") or 0),
            )
        return {
            "total": len(items),
            "visualLocatorBackedCount": visual_locator_backed,
            "visualJudgeBackedCount": visual_judge_backed,
            "waitSensitiveCount": wait_sensitive,
            "loadingSensitiveCount": loading_sensitive,
            "environmentAwareCount": environment_aware,
            "dialogAwareCount": dialog_aware,
            "focusAwareCount": focus_aware,
            "notificationAwareCount": notification_aware,
            "soundAwareCount": sound_aware,
            "notificationSensingRequested": notification_sensing_requested_count > 0,
            "notificationSensingAvailable": notification_sensing_available_count > 0,
            "soundSensingRequested": sound_sensing_requested_count > 0,
            "soundSensingAvailable": sound_sensing_available_count > 0,
            "notificationSensingRequestedCount": notification_sensing_requested_count,
            "notificationSensingAvailableCount": notification_sensing_available_count,
            "soundSensingRequestedCount": sound_sensing_requested_count,
            "soundSensingAvailableCount": sound_sensing_available_count,
            "notificationObserved": notification_observed > 0,
            "soundObserved": sound_observed > 0,
            "notificationObservedCount": notification_observed,
            "soundObservedCount": sound_observed,
            "notificationSignalProviders": notification_signal_providers,
            "soundSignalProviders": sound_signal_providers,
            "notificationSensingModes": notification_sensing_modes,
            "soundSensingModes": sound_sensing_modes,
            "maxNotificationCandidateCount": max_notification_candidate_count,
            "maxSoundActiveSessionCount": max_sound_active_session_count,
            "visualSemanticRoles": visual_semantic_roles,
            "visualLocatorProviders": visual_locator_providers,
            "transitionStates": transition_states,
            "pageIdentities": page_identities,
        }

    def _resolve_effective_session_id(
        self,
        *,
        session_id: str | None,
        run_id: str | None,
    ) -> str | None:
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            return normalized_session_id
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return None
        run_record = db.get_run_record(normalized_run_id) or {}
        resolved_session_id = str(run_record.get("session_id") or "").strip()
        return resolved_session_id or None

    def _trace_app_id(self, trace: Dict[str, Any]) -> str | None:
        metadata = dict(trace.get("metadata") or {})
        candidates = [metadata.get("appId"), metadata.get("profileId")]
        for step in list(trace.get("steps") or []):
            if not isinstance(step, dict):
                continue
            candidates.extend(
                [
                    step.get("appId"),
                    step.get("app_id"),
                    dict(step.get("metadata") or {}).get("appId"),
                    dict(step.get("target") or {}).get("appId"),
                ]
            )
        for candidate in candidates:
            normalized = str(candidate or "").strip().lower()
            if normalized:
                return normalized
        return None

    def _should_materialize_from_recent_trace(
        self,
        *,
        template_matches: list[Dict[str, Any]],
        draft_matches: list[Dict[str, Any]],
    ) -> bool:
        combined = sorted(
            [*template_matches, *draft_matches],
            key=lambda item: (float(item.get("score") or 0.0), float(item.get("confidence") or 0.0)),
            reverse=True,
        )
        if not combined:
            return True
        top_match = dict(combined[0] or {})
        top_score = float(top_match.get("score") or 0.0)
        top_mode = str(top_match.get("routeMode") or "").strip() or "learn_mode"
        if top_mode == "learn_mode":
            return True
        if self._match_requires_rematerialization(top_match):
            return True
        return top_score < 0.42

    def _payload_has_suspicious_runtime_placeholders(self, payload: Dict[str, Any]) -> bool:
        for variable in list(payload.get("variables") or []):
            if not isinstance(variable, dict):
                continue
            if isinstance(variable.get("exampleValue"), bool):
                return True
        for step in list(payload.get("steps") or []):
            if not isinstance(step, dict):
                continue
            params = dict(step.get("params") or {})
            for key, value in params.items():
                normalized_key = str(key or "").strip().lower()
                text = str(value or "").strip()
                if normalized_key in self.compiler._NON_VARIABLE_PARAM_KEYS and text.startswith("{{") and text.endswith("}}"):
                    return True
        return False

    def _match_requires_rematerialization(self, match: Dict[str, Any]) -> bool:
        payload: Dict[str, Any] | None = None
        kind = str(match.get("kind") or "").strip().lower()
        match_id = str(match.get("id") or "").strip()
        if kind == "draft" and match_id:
            payload = self.script_store.get_draft(match_id)
        elif kind == "template" and match_id:
            payload = self.get_template(match_id)
            source = dict((payload or {}).get("source") or {})
            linked_draft_id = str(source.get("draftId") or "").strip()
            if linked_draft_id:
                linked_draft = self.script_store.get_draft(linked_draft_id)
                if isinstance(linked_draft, dict) and self._payload_has_suspicious_runtime_placeholders(linked_draft):
                    return True
        if not isinstance(payload, dict):
            return False
        return self._payload_has_suspicious_runtime_placeholders(payload)

    def _materialize_recent_session_trace(
        self,
        *,
        goal: str,
        app_id: str | None,
        session_id: str | None,
        run_id: str | None,
    ) -> Dict[str, Any] | None:
        effective_session_id = self._resolve_effective_session_id(session_id=session_id, run_id=run_id)
        normalized_goal = str(goal or "").strip()
        normalized_app_id = str(app_id or "").strip().lower()
        if not effective_session_id or not normalized_goal or not normalized_app_id:
            return None

        recent_traces = self.compiler.trace_store.list_traces(limit=20, session_id=effective_session_id)
        for trace_summary in list(recent_traces or []):
            candidate_run_id = str(trace_summary.get("runId") or "").strip()
            if not candidate_run_id:
                continue
            run_record = db.get_run_record(candidate_run_id) or {}
            if str(run_record.get("status") or "").strip().lower() != "completed":
                continue
            trace = self.compiler.trace_store.get_trace(candidate_run_id)
            if not isinstance(trace, dict):
                continue
            if self._trace_app_id(trace) != normalized_app_id:
                continue

            script_payload = self.compiler.compile_trace(trace).as_dict()
            script_payload["goal"] = normalized_goal
            script_payload["id"] = self.compiler._script_id(app_id=normalized_app_id, goal=normalized_goal)
            script_payload["name"] = self.compiler._script_name(app_id=normalized_app_id, goal=normalized_goal)
            metadata = dict(script_payload.get("metadata") or {})
            metadata["autoMaterializedFromRunId"] = candidate_run_id
            metadata["autoMaterializedFromSessionId"] = effective_session_id
            metadata["autoMaterializedAt"] = utc_now_iso()
            metadata["autoMaterializedStrategy"] = "recent_completed_session_trace"
            script_payload["metadata"] = metadata
            saved_draft = self.script_store.save_draft(script_payload)
            candidate_payload = self.sync_candidate_for_script(saved_draft, save=True)
            return {
                "draftId": saved_draft.get("id"),
                "templateId": ((candidate_payload.get("source") or {}).get("templateId")) or candidate_payload.get("id"),
                "runId": candidate_run_id,
                "sessionId": effective_session_id,
                "goal": normalized_goal,
                "appId": normalized_app_id,
            }
        return None

    def recommend_execution_route(
        self,
        *,
        goal: str,
        app_id: str | None = None,
        variables: Dict[str, Any] | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        limit: int = 5,
        allow_materialization: bool = False,
    ) -> Dict[str, Any]:
        raw_goal = str(goal or "").strip()
        normalized_goal = self._normalize_goal_text(goal)
        template_matches = self._match_templates_for_goal(
            goal=normalized_goal,
            app_id=app_id,
            variables=variables,
            limit=max(1, limit),
        )
        draft_matches = self._match_drafts_for_goal(
            goal=normalized_goal,
            app_id=app_id,
            variables=variables,
            limit=max(1, limit),
        )
        auto_materialized_payload: Dict[str, Any] | None = None
        if allow_materialization and self._should_materialize_from_recent_trace(
            template_matches=template_matches,
            draft_matches=draft_matches,
        ):
            auto_materialized_payload = self._materialize_recent_session_trace(
                goal=raw_goal,
                app_id=app_id,
                session_id=session_id,
                run_id=run_id,
            )
            if auto_materialized_payload:
                template_matches = self._match_templates_for_goal(
                    goal=normalized_goal,
                    app_id=app_id,
                    variables=variables,
                    limit=max(1, limit),
                )
                draft_matches = self._match_drafts_for_goal(
                    goal=normalized_goal,
                    app_id=app_id,
                    variables=variables,
                    limit=max(1, limit),
                )
        combined = sorted(
            [*template_matches, *draft_matches],
            key=lambda item: (float(item.get("score") or 0.0), float(item.get("confidence") or 0.0)),
            reverse=True,
        )[: max(1, limit)]
        top_match = dict(combined[0]) if combined else {}
        if auto_materialized_payload:
            auto_draft_id = str(auto_materialized_payload.get("draftId") or "").strip()
            preferred_auto_draft = next(
                (dict(item) for item in draft_matches if str(item.get("id") or "").strip() == auto_draft_id),
                None,
            )
            if preferred_auto_draft:
                top_match = preferred_auto_draft
        recommended_mode = str(top_match.get("routeMode") or "").strip() or "learn_mode"
        recommended_action = str(top_match.get("routeAction") or "").strip() or "start_computer_use_learning"
        if not combined:
            recommended_mode = "learn_mode"
            recommended_action = "start_computer_use_learning"
        stage = str(top_match.get("stage") or "").strip() or None
        rollout_mode = str(top_match.get("rolloutMode") or "").strip() or None
        match_signal_summary = self._summarize_match_signals(combined)
        return {
            "goal": goal,
            "appId": str(app_id or "").strip() or None,
            "lookupMode": "auto_materialized_session_trace" if auto_materialized_payload else "read_only",
            "recommendedMode": recommended_mode,
            "recommendedModeLabel": self._EXECUTION_MODE_LABELS.get(recommended_mode, recommended_mode),
            "recommendedAction": recommended_action,
            "recommendedActionLabel": self._ROUTE_ACTION_LABELS.get(recommended_action, recommended_action),
            "recommendedMatch": top_match or None,
            "recommendedTemplateId": top_match.get("id") if top_match.get("kind") == "template" else None,
            "recommendedDraftId": top_match.get("id") if top_match.get("kind") == "draft" else None,
            "stage": stage,
            "rolloutMode": rollout_mode,
            "requiresVariableBinding": bool(top_match.get("requiresVariableBinding")),
            "missingVariables": list(top_match.get("missingVariables") or []),
            "providedVariables": list(top_match.get("providedVariables") or []),
            "templateMatches": template_matches,
            "draftMatches": draft_matches,
            "matches": combined,
            "summary": {
                "templateCount": len(template_matches),
                "draftCount": len(draft_matches),
                "bestScore": float(top_match.get("score") or 0.0) if top_match else 0.0,
                "bestConfidence": top_match.get("confidence"),
                "hasReusableMemory": bool(recommended_mode in {"reuse_mode", "hybrid_mode"}),
                "requiresLearning": bool(recommended_mode == "learn_mode"),
                "promotionGateStatus": top_match.get("promotionGateStatus") if top_match else None,
                "promotionGateBlocked": bool(top_match.get("promotionGateBlocked")) if top_match else False,
                "promotionGateReasons": list(top_match.get("promotionGateReasons") or [])[:5] if top_match else [],
                "promotionGateSignals": dict(top_match.get("promotionGateSignals") or {}) if top_match else {},
                "visualSignalSummary": dict(top_match.get("visualSignalSummary") or {}) if top_match else {},
                "timingSignalSummary": dict(top_match.get("timingSignalSummary") or {}) if top_match else {},
                "environmentSignalSummary": dict(top_match.get("environmentSignalSummary") or {}) if top_match else {},
                "matchSignalSummary": match_signal_summary,
                "autoMaterialized": bool(auto_materialized_payload),
                "autoMaterializedDraftId": (auto_materialized_payload or {}).get("draftId"),
                "autoMaterializedTemplateId": (auto_materialized_payload or {}).get("templateId"),
                "autoMaterializedFromRunId": (auto_materialized_payload or {}).get("runId"),
            },
            "manualControls": {
                "humanCanSyncCandidate": True,
                "humanCanApprove": True,
                "humanCanFreeze": True,
                "humanCanRollback": True,
                "humanCanReject": True,
                "notes": "人工始终保留对肌肉记忆的增减、审批、冻结、回滚和复核权。",
            },
            "autoMaterialized": auto_materialized_payload,
        }

    def sync_candidate_for_script(self, script_payload: Dict[str, Any], *, save: bool = True) -> Dict[str, Any]:
        payload = self.compiler.sync_template_for_script(script_payload, save=save)
        if not save:
            return dict(payload)
        template_id = str((payload.get("source") or {}).get("templateId") or "").strip()
        if template_id:
            template = self.get_template(template_id)
            if isinstance(template, dict):
                self._sync_linked_draft_metadata(template)
        return dict(payload)

    def review_template(
        self,
        template_id: str,
        *,
        decision: str,
        reviewer: str = "system",
        notes: str | None = None,
        metadata_patch: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = self._ensure_template(template_id)
        metadata = dict(payload.get("metadata") or {})
        now = utc_now_iso()
        normalized_decision = self._normalize_status(decision)
        current_revision = int(metadata.get("revision") or 0)
        review_history = [dict(item) for item in list(metadata.get("reviewHistory") or []) if isinstance(item, dict)]
        review_history.append(
            {
                "decision": normalized_decision,
                "reviewer": str(reviewer or "system"),
                "notes": str(notes or "").strip() or None,
                "at": now,
                "fromStatus": str(metadata.get("templateStatus") or payload.get("status") or "candidate"),
                "fromRevision": current_revision or None,
            }
        )
        metadata["reviewHistory"] = review_history[-20:]
        metadata["templateStatus"] = normalized_decision
        metadata["lastReviewedAt"] = now
        metadata["lastReviewer"] = str(reviewer or "system")
        metadata["revision"] = max(1, current_revision + 1)
        if notes not in (None, ""):
            metadata["lastReviewNotes"] = str(notes)
        if isinstance(metadata_patch, dict) and metadata_patch:
            metadata.update(dict(metadata_patch))
        if normalized_decision == "approved":
            metadata["approvedAt"] = now
            metadata["approvedBy"] = str(reviewer or "system")
        elif normalized_decision == "rejected":
            metadata["rejectedAt"] = now
            metadata["rejectedBy"] = str(reviewer or "system")
        elif normalized_decision == "frozen":
            metadata["frozenAt"] = now
            metadata["frozenBy"] = str(reviewer or "system")
        payload["metadata"] = metadata
        payload["status"] = normalized_decision
        return self._save_template(
            payload,
            reason=f"template_{normalized_decision}",
            actor=str(reviewer or "system"),
        )

    def approve_template(
        self,
        template_id: str,
        *,
        reviewer: str = "system",
        notes: str | None = None,
    ) -> Dict[str, Any]:
        return self.review_template(template_id, decision="approved", reviewer=reviewer, notes=notes)

    def freeze_template(
        self,
        template_id: str,
        *,
        reviewer: str = "system",
        notes: str | None = None,
    ) -> Dict[str, Any]:
        return self.review_template(template_id, decision="frozen", reviewer=reviewer, notes=notes)

    def rollback_template(
        self,
        template_id: str,
        *,
        revision: int | None = None,
        history_path: str | None = None,
        reviewer: str = "system",
        notes: str | None = None,
    ) -> Dict[str, Any]:
        current = self._ensure_template(template_id)
        history = self.list_template_history(template_id, limit=200)
        if not history:
            raise ValueError(f"template '{template_id}' 不存在可回滚历史。")
        selected: Dict[str, Any] | None = None
        if history_path:
            normalized_history_path = str(history_path).strip().lower()
            for item in history:
                if str(item.get("path") or "").strip().lower() == normalized_history_path:
                    selected = item
                    break
        elif revision not in (None, ""):
            target_revision = int(revision)
            for item in history:
                if int(item.get("revision") or 0) == target_revision:
                    selected = item
                    break
        else:
            selected = history[0]
        if not isinstance(selected, dict) or not isinstance(selected.get("template"), dict):
            raise ValueError(f"template '{template_id}' 未找到匹配的历史快照。")

        restored = dict(selected.get("template") or {})
        restored["id"] = template_id
        restored_metadata = dict(restored.get("metadata") or {})
        current_metadata = dict(current.get("metadata") or {})
        now = utc_now_iso()
        previous_revision = int(current_metadata.get("revision") or 0)
        restored_revision = int(restored_metadata.get("revision") or 0)
        restored_metadata["templateStatus"] = self._normalize_status(restored_metadata.get("templateStatus") or restored.get("status"))
        restored_metadata["rollback"] = {
            "fromRevision": previous_revision or None,
            "toRevision": restored_revision or None,
            "at": now,
            "reviewer": str(reviewer or "system"),
            "notes": str(notes or "").strip() or None,
            "historyPath": selected.get("path"),
        }
        restored_metadata["revision"] = max(previous_revision, restored_revision) + 1
        restored_metadata["lastReviewedAt"] = now
        restored_metadata["lastReviewer"] = str(reviewer or "system")
        merged_review_history = [
            dict(item)
            for item in list(current_metadata.get("reviewHistory") or [])
            if isinstance(item, dict)
        ]
        for item in list(restored_metadata.get("reviewHistory") or []):
            if not isinstance(item, dict):
                continue
            if item not in merged_review_history:
                merged_review_history.append(dict(item))
        rollback_entry = {
            "decision": "rollback",
            "reviewer": str(reviewer or "system"),
            "notes": str(notes or "").strip() or None,
            "at": now,
            "fromStatus": str(current_metadata.get("templateStatus") or current.get("status") or "candidate"),
            "toRevision": restored_revision or None,
        }
        merged_review_history.append(rollback_entry)
        restored_metadata["reviewHistory"] = merged_review_history[-20:]
        restored["metadata"] = restored_metadata
        restored["status"] = restored_metadata.get("templateStatus")
        return self._save_template(
            restored,
            reason="template_rollback",
            actor=str(reviewer or "system"),
        )

    def register_template_execution_feedback(
        self,
        template_id: str,
        *,
        execution_state: str,
        outcome_family: str | None = None,
        feedback: Dict[str, Any] | None = None,
        actor: str = "rpa_runtime",
    ) -> Dict[str, Any]:
        template = self._ensure_template(template_id)
        metadata = dict(template.get("metadata") or {})
        stats = dict(metadata.get("governanceStats") or {})
        normalized_outcome_family = outcome_family or outcome_family_for_execution_state(execution_state)
        stats["runs"] = int(stats.get("runs") or 0) + 1
        stats["lastExecutionState"] = str(execution_state or "").strip() or None
        stats["lastOutcomeFamily"] = normalized_outcome_family
        stats["lastExecutedAt"] = utc_now_iso()
        if normalized_outcome_family == "completed":
            stats["completed"] = int(stats.get("completed") or 0) + 1
            if str(execution_state or "").strip() == "completed_via_computer_use_primary":
                stats["computerUsePrimaryCompleted"] = int(stats.get("computerUsePrimaryCompleted") or 0) + 1
        elif normalized_outcome_family == "completed_with_fallback":
            stats["fallbackCompleted"] = int(stats.get("fallbackCompleted") or 0) + 1
        elif normalized_outcome_family == "review_required":
            stats["reviewRequired"] = int(stats.get("reviewRequired") or 0) + 1
        elif normalized_outcome_family == "blocked":
            stats["compileBlocked"] = int(stats.get("compileBlocked") or 0) + 1
        elif normalized_outcome_family == "failed":
            if str(execution_state or "").strip() == "fallback_failed":
                stats["fallbackFailed"] = int(stats.get("fallbackFailed") or 0) + 1
            stats["failed"] = int(stats.get("failed") or 0) + 1
        if bool(dict(feedback or {}).get("localRepairApplied")):
            stats["localRepairApplied"] = int(stats.get("localRepairApplied") or 0) + 1
        metadata["governanceStats"] = stats
        metadata["lastExecutionState"] = stats.get("lastExecutionState")
        metadata["lastOutcomeFamily"] = stats.get("lastOutcomeFamily")
        metadata["lastExecutedAt"] = stats.get("lastExecutedAt")
        self._consume_execution_feedback(
            template=template,
            metadata=metadata,
            feedback=feedback,
        )
        template["metadata"] = metadata
        saved = self._save_template(
            template,
            reason="template_execution_feedback",
            actor=str(actor or "rpa_runtime"),
            execution_state=execution_state,
            feedback=feedback,
        )
        auto_transition = self._auto_transition_decision(saved, execution_state=execution_state)
        if auto_transition is None:
            return {"template": saved, "autoTransition": None}
        transitioned = self.review_template(
            template_id,
            decision=str(auto_transition.get("decision") or "review_required"),
            reviewer="template_policy",
            notes=str(auto_transition.get("notes") or auto_transition.get("reason") or ""),
        )
        return {
            "template": transitioned,
            "autoTransition": auto_transition,
        }


rpa_template_service = RPATemplateService()
