from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from core.agent_browser_profile import (
    configured_agent_browser_profile_dir,
    discover_system_agent_browser,
    normalize_agent_browser_kind,
)
from core.multimodal_payload_adapter import utc_now_iso
from runtimes.computer_use.app_profiles import ComputerUseAppProfiles
from runtimes.computer_use.trace_store import trace_store
from runtimes.rpa.promotion_gate import (
    draft_environment_signal_summary,
    draft_promotion_gate_summary,
    draft_timing_signal_summary,
    draft_visual_signal_summary,
    evaluate_promotion_gate,
)
from runtimes.rpa.execution_semantics import normalize_script_assessment_status
from runtimes.rpa.store import RPAScriptStore, rpa_script_store
from runtimes.rpa.trust_policy import (
    TEMPLATE_TRUST_POLICY_VERSION,
    draft_template_governance_summary,
    evaluate_template_governance,
)
from runtimes.rpa.types import (
    RPARobotLibrary,
    RPAScript,
    RPAScriptAssessment,
    RPAScriptRobotOptions,
    RPAScriptStep,
    RPATemplateCandidate,
    RPATemplateProfileBinding,
    RPAScriptVariable,
    RPAStepAssessment,
    RPAStepRobotSemantic,
    RPAStepApproval,
)

TRUST_MODEL_VERSION = "rpa-trust-v1"
STEP_REVIEW_THRESHOLD = 0.72
STEP_EXCLUDE_THRESHOLD = 0.45
SCRIPT_TRUSTED_THRESHOLD = 0.84
SCRIPT_REVIEW_THRESHOLD = 0.66
SCRIPT_FALLBACK_HEAVY_THRESHOLD = 0.56
SCRIPT_BLOCKED_ACCEPTED_RATIO_MIN = 0.4
SCRIPT_BLOCKED_EXCLUDED_RATIO_MAX = 0.5
SCRIPT_PROFILE_AUGMENTED_PENALTY_RATIO = 0.5
SCRIPT_PROFILE_AUGMENTED_REVIEW_RATIO = 0.75
CALIBRATION_MIN_RUNS = 3


class RPATraceCompiler:
    _NON_VARIABLE_PARAM_KEYS = {
        "clear_first",
        "double",
        "continue_on_error",
        "max_steps",
        "retry_limit",
        "wait_timeout_ms",
        "timeout_ms",
        "poll_ms",
        "post_action_settle_timeout_ms",
        "post_action_settle_poll_ms",
        "post_action_stable_rounds",
        "abort_on_major_deviation",
        "point",
        "point_rect",
        "point_candidates",
        "spatial_anchor",
        "image_anchor",
        "coordinate_source",
        "visual_locator",
        "visual_locator_confidence",
        "visual_locator_role_hint",
        "visual_locator_timeout_ms",
        "visual_locator_multiple",
        "visual_locator_read_text",
        "post_action_visual_locator",
        "post_action_visual_locator_confidence",
        "post_action_visual_locator_timeout_ms",
        "post_action_visual_locator_multiple",
        "post_action_visual_locator_read_text",
        "prefer_sendinput_click",
        "prefer_fast_path",
        "strict_window_title_match",
        "observe_notifications",
        "observe_sound",
        "window_typing",
        "query_mode",
        "preferred_result_region",
        "preferred_result_index",
        "required_exact_match",
        "forbidden_result_tokens",
        "search_selector_key",
        "result_selector_key",
        "require_visual_guard",
        "action_name",
        "toolbar_action_name",
        "app_id",
        "window_title",
        "window_handle",
        "process_name",
        "class_name",
    }

    def __init__(
        self,
        *,
        trace_store_instance=trace_store,
        script_store: RPAScriptStore = rpa_script_store,
    ) -> None:
        self.trace_store = trace_store_instance
        self.script_store = script_store
        self.app_profiles = ComputerUseAppProfiles()

    def _template_governance(
        self,
        *,
        template_payload: Dict[str, Any],
        script_payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        script = dict(script_payload or {})
        script_id = str(
            script.get("id")
            or ((template_payload.get("source") or {}).get("draftId"))
            or ""
        ).strip()
        fingerprint = str(
            (template_payload.get("metadata") or {}).get("fingerprint")
            or (script.get("metadata") or {}).get("fingerprint")
            or ""
        ).strip()
        calibration = self.script_store.get_script_calibration(
            script_id=script_id,
            fingerprint=fingerprint or None,
        ) if script_id else {}
        return evaluate_template_governance(
            template_payload=template_payload,
            calibration=calibration,
        )

    def _slug(self, value: str, fallback: str = "workflow") -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower())
        normalized = normalized.strip("_")
        return normalized or fallback

    def _script_name(self, *, app_id: str, goal: str) -> str:
        compact_goal = str(goal or "").strip() or "computer_use_flow"
        if len(compact_goal) > 48:
            compact_goal = compact_goal[:48].rstrip()
        return f"{app_id}:{compact_goal}"

    def _script_id(self, *, app_id: str, goal: str) -> str:
        return f"rpa.{self._slug(app_id, 'desktop')}.{self._slug(goal, 'workflow')}"

    def _template_name(self, *, app_id: str, goal: str) -> str:
        compact_goal = str(goal or "").strip() or "computer_use_template"
        if len(compact_goal) > 48:
            compact_goal = compact_goal[:48].rstrip()
        return f"{app_id}:template:{compact_goal}"

    def _template_id(self, *, app_id: str, goal: str, fingerprint: str) -> str:
        suffix = str(fingerprint or "").split(".")[-1] or self._slug(goal, "workflow")
        return f"tpl.{self._slug(app_id, 'desktop')}.{suffix}"

    def _script_fingerprint(self, *, app_id: str, steps: List[Dict[str, Any]]) -> str:
        parts: List[str] = [self._slug(app_id, "desktop")]
        for step in steps:
            if not isinstance(step, dict):
                continue
            compiled_use = self._step_use(step)
            params = dict(step.get("params") or {})
            target = dict(step.get("target") or {})
            selector = dict(target.get("selector") or {})
            action_name = str(params.get("action_name") or params.get("toolbar_action_name") or "").strip().lower()
            selector_key = str(params.get("selector_key") or selector.get("selectorKey") or "").strip().lower()
            control_type = str(selector.get("controlType") or "").strip().lower()
            parts.append(f"{compiled_use}:{action_name}:{selector_key}:{control_type}")
        digest = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:12]
        return f"fp.{self._slug(app_id, 'desktop')}.{digest}"

    def _template_profile_binding(self, app_id: str) -> Optional[RPATemplateProfileBinding]:
        profile = self.app_profiles.get(app_id)
        if profile is None:
            return None
        return RPATemplateProfileBinding(
            app_id=profile.app_id,
            display_name=profile.display_name,
            process_names=list(profile.process_names),
            scenario_tags=list(profile.scenario_tags),
            title_patterns=list(profile.title_patterns),
            class_names=list(profile.class_names),
            transient_selectors=list(profile.transient_selectors),
            window_probe_selector_keys=list(profile.window_probe_selector_keys),
            high_risk_actions=list(profile.high_risk_actions),
        )

    def _robot_options(
        self,
        *,
        app_id: str,
        goal: str,
        trace: Dict[str, Any],
        steps: List[RPAScriptStep],
        script_assessment: Optional[RPAScriptAssessment] = None,
    ) -> RPAScriptRobotOptions:
        tags = [
            "v8chat",
            "rpa-draft",
            f"app:{self._slug(app_id, 'desktop')}",
        ]
        libraries = [
            RPARobotLibrary(
                name="runtimes.rpa.robot_keywords.V8ChatRPAKeywords",
                required=True,
                purpose="v8chat ComputerUse / RPA bridge keywords",
            )
        ]
        if app_id in {"browser_checkout", "browser", "chrome", "edge"}:
            libraries.append(
                RPARobotLibrary(
                    name="RPA.Browser.Selenium",
                    required=False,
                    purpose="browser-native keywords for future exporter upgrades",
                )
            )
        if any(step.approval is not None for step in steps):
            tags.append("risk:high")
        if trace.get("source") == "manual":
            tags.append("source:manual")
        review_required_count = sum(1 for step in steps if step.assessment and step.assessment.review_required)
        if review_required_count:
            tags.append("review:required")
        accepted_count = sum(1 for step in steps if step.assessment and step.assessment.status == "accepted")
        if script_assessment and script_assessment.status:
            tags.append(f"trust:{script_assessment.status}")
        return RPAScriptRobotOptions(
            tags=tags,
            libraries=libraries,
            metadata={
                "App Id": app_id,
                "Goal": goal,
                "Source Trace Run": trace.get("runId"),
                "Source Trace Session": trace.get("sessionId"),
                "Generated By": "v8chat RPATraceCompiler",
                "Accepted Steps": accepted_count,
                "Review Required Steps": review_required_count,
                "Trust Status": script_assessment.status if script_assessment else None,
            },
        )

    def _variable_type(self, example_value: Any) -> str:
        if isinstance(example_value, bool):
            return "boolean"
        if isinstance(example_value, (int, float)) and not isinstance(example_value, bool):
            return "number"
        if isinstance(example_value, list):
            return "array"
        if isinstance(example_value, dict):
            return "object"
        return "string"

    def _approval_for_step(self, step: Dict[str, Any], assessment: RPAStepAssessment) -> Optional[RPAStepApproval]:
        risk = dict(step.get("risk") or {})
        details = dict(risk.get("details") or {})
        high_risk = bool(risk.get("highRiskAction"))
        requires_pre = bool(risk.get("requiresPreGuard"))
        requires_post = bool(risk.get("requiresPostGuard"))
        if not (high_risk or requires_pre or requires_post or assessment.review_required):
            return None
        reason_parts: List[str] = []
        if high_risk:
            reason_parts.append("高风险动作")
        if requires_pre:
            reason_parts.append("执行前视觉确认")
        if requires_post:
            reason_parts.append("执行后视觉确认")
        if assessment.review_required and not high_risk:
            reason_parts.extend(item for item in assessment.reasons[:2] if item)
        mode = "high_risk_visual_guard" if high_risk else ("compile_review_required" if assessment.review_required else "visual_guard")
        return RPAStepApproval(
            mode=mode,
            reason=" / ".join(dict.fromkeys(reason_parts)) or str(details.get("visualExpectation") or "需要视觉保底确认"),
            required=True,
        )

    def _robot_quote(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if re.search(r"\s", text):
            return f"\"{text}\""
        return text

    def _robot_control_type(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return text if text.endswith("Control") else f"{text}Control"

    def _windows_selector_locator(self, selector: Dict[str, Any]) -> str:
        if not isinstance(selector, dict):
            return ""
        parts: List[str] = []
        selector_key = str(selector.get("selectorKey") or "").strip()
        name = str(selector.get("name") or "").strip()
        automation_id = str(selector.get("automationId") or "").strip()
        control_type = self._robot_control_type(selector.get("controlType"))
        class_name = str(selector.get("className") or "").strip()
        handle = selector.get("handle")

        if selector_key:
            parts.append(f"selector:{selector_key}")
        if name:
            parts.append(f"name:{self._robot_quote(name)}")
        if automation_id:
            parts.append(f"id:{self._robot_quote(automation_id)}")
        if control_type:
            parts.append(f"type:{self._robot_quote(control_type)}")
        if class_name:
            parts.append(f"class:{self._robot_quote(class_name)}")
        if handle not in (None, ""):
            parts.append(f"handle:{handle}")
        return " and ".join(part for part in parts if part)

    def _windows_window_locator(self, window: Dict[str, Any]) -> str:
        if not isinstance(window, dict):
            return ""
        parts: List[str] = []
        title = str(window.get("title") or "").strip()
        class_name = str(window.get("className") or "").strip()
        process_name = str(window.get("processName") or "").strip()
        window_handle = window.get("windowHandle")
        if process_name:
            parts.append(f"executable:{self._robot_quote(process_name)}")
        if title:
            parts.append(f"subname:{self._robot_quote(title)}")
        parts.append("type:WindowControl")
        if class_name:
            parts.append(f"class:{self._robot_quote(class_name)}")
        if window_handle not in (None, ""):
            parts.append(f"handle:{window_handle}")
        return " and ".join(part for part in parts if part)

    def _combined_windows_locator(self, step: Dict[str, Any]) -> str:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        window = dict(target.get("window") or {})
        selector_locator = self._windows_selector_locator(selector)
        window_locator = self._windows_window_locator(window)
        if window_locator and selector_locator:
            if selector_locator.startswith("selector:"):
                return selector_locator
            return f"{window_locator} > {selector_locator}"
        return selector_locator or window_locator

    def _browser_selector_locator(self, step: Dict[str, Any]) -> str:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        metadata = dict(selector.get("metadata") or {}) if isinstance(selector.get("metadata"), dict) else {}
        for key in ("browserLocator", "locator", "css", "xpath"):
            value = metadata.get(key)
            if value not in (None, ""):
                if key == "css":
                    return f"css:{value}"
                if key == "xpath":
                    return f"xpath:{value}"
                return str(value).strip()
        selector_key = str(selector.get("selectorKey") or "").strip()
        if re.match(r"^(css|xpath|id|name|alias|class|link|partial link):", selector_key, re.IGNORECASE):
            return selector_key
        automation_id = str(selector.get("automationId") or "").strip()
        if automation_id:
            return f"id:{automation_id}"
        name = str(selector.get("name") or "").strip()
        if name:
            escaped = name.replace('"', '\\"')
            return f'xpath://*[contains(normalize-space(.), "{escaped}")]'
        return ""

    def _profile_augmented_step(self, app_id: str, step: Dict[str, Any], *, action_name: str | None = None) -> Dict[str, Any]:
        if not app_id:
            return dict(step)
        profile = self.app_profiles.get(app_id)
        if profile is None:
            return dict(step)
        target = dict(step.get("target") or {})
        next_target = dict(target)
        window = dict(target.get("window") or {})
        if not window:
            if profile.title_patterns:
                window["title"] = profile.title_patterns[0]
            if profile.class_names:
                window["className"] = profile.class_names[0]
            if profile.process_names:
                window["processName"] = profile.process_names[0]
            if window:
                window["profileAugmented"] = True
                next_target["window"] = window
        selector = dict(target.get("selector") or {})
        if selector:
            next_step = dict(step)
            next_step["target"] = next_target
            return next_step
        candidates = self.app_profiles.action_selector_keys_for(app_id, action_name)
        if not candidates:
            next_step = dict(step)
            next_step["target"] = next_target
            return next_step
        for selector_key in candidates:
            profile_selector = profile.selector_for(selector_key)
            if not profile_selector:
                profile_selector = profile.toolbar_selector_for(selector_key)
            if not profile_selector:
                continue
            profile_selector.setdefault("selectorKey", selector_key)
            metadata = dict(profile_selector.get("metadata") or {})
            metadata["profileAugmented"] = True
            profile_selector["metadata"] = metadata
            next_target["selector"] = profile_selector
            next_step = dict(step)
            next_step["target"] = next_target
            return next_step
        next_step = dict(step)
        next_step["target"] = next_target
        return next_step

    def _is_password_like(self, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        if selector_key and any(token in selector_key for token in ("password", "passwd", "pwd", "密码")):
            return True
        name = str(selector.get("name") or "").strip().lower()
        return bool(name and any(token in name for token in ("password", "passwd", "pwd", "密码")))

    def _selector_metadata(self, step: Dict[str, Any]) -> Dict[str, Any]:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        return dict(selector.get("metadata") or {}) if isinstance(selector.get("metadata"), dict) else {}

    def _browser_select_value(self, params: Dict[str, Any]) -> str:
        for key in ("value", "selected_value", "option_value", "selected", "selection", "text"):
            value = params.get(key)
            if isinstance(value, list) and value:
                return str(value[0]).strip()
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def _is_browser_select_like(self, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        metadata = self._selector_metadata(step)
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        control_type = str(selector.get("controlType") or metadata.get("controlType") or "").strip().lower()
        name = str(selector.get("name") or "").strip().lower()
        role = str(metadata.get("role") or metadata.get("ariaRole") or "").strip().lower()
        tag_name = str(metadata.get("browserTagName") or metadata.get("tagName") or "").strip().lower()
        if control_type in {"combobox", "list", "listitem"}:
            return True
        if role in {"combobox", "listbox"}:
            return True
        if tag_name == "select":
            return True
        return any(
            token in value
            for value in (selector_key, name)
            for token in ("select", "dropdown", "combobox", "listbox", "country", "province", "option", "选项", "地区", "国家")
            if value
        )

    def _is_browser_checkbox_like(self, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        metadata = self._selector_metadata(step)
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        control_type = str(selector.get("controlType") or metadata.get("controlType") or "").strip().lower()
        name = str(selector.get("name") or "").strip().lower()
        role = str(metadata.get("role") or metadata.get("ariaRole") or "").strip().lower()
        tag_name = str(metadata.get("browserTagName") or metadata.get("tagName") or "").strip().lower()
        input_type = str(metadata.get("inputType") or "").strip().lower()
        if control_type in {"checkbox", "checkbutton"}:
            return True
        if role == "checkbox":
            return True
        if tag_name == "input" and input_type == "checkbox":
            return True
        tokens = ("checkbox", "agree", "consent", "terms", "accept", "remember", "subscribe", "勾选", "同意", "协议", "条款")
        return any(token in value for value in (selector_key, name) for token in tokens if value)

    def _is_browser_file_input_like(self, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        metadata = self._selector_metadata(step)
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        name = str(selector.get("name") or "").strip().lower()
        tag_name = str(metadata.get("browserTagName") or metadata.get("tagName") or "").strip().lower()
        input_type = str(metadata.get("inputType") or "").strip().lower()
        if tag_name == "input" and input_type == "file":
            return True
        tokens = ("file", "upload", "附件", "上传", "choose_file")
        return any(token in value for value in (selector_key, name) for token in tokens if value)

    def _is_browser_button_like(self, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        metadata = self._selector_metadata(step)
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        control_type = str(selector.get("controlType") or metadata.get("controlType") or "").strip().lower()
        name = str(selector.get("name") or "").strip().lower()
        role = str(metadata.get("role") or metadata.get("ariaRole") or "").strip().lower()
        tag_name = str(metadata.get("browserTagName") or metadata.get("tagName") or "").strip().lower()
        if control_type in {"button", "hyperlink"}:
            return True
        if role in {"button", "link"}:
            return True
        if tag_name in {"button", "a"}:
            return True
        tokens = ("button", "btn", "play", "submit", "confirm", "pay", "send", "播放", "提交", "确认", "支付", "发送")
        return any(token in value for value in (selector_key, name) for token in tokens if value)

    def _browser_url_for_step(self, step: Dict[str, Any]) -> str:
        params = dict(step.get("params") or {})
        for key in ("url", "target_url", "page_url", "href"):
            value = params.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return "about:blank"

    def _browser_open_arguments(self, *, app_id: str, step: Dict[str, Any]) -> List[str]:
        params = dict(step.get("params") or {})
        browser_hint = params.get("browserKind") or params.get("browser_kind") or app_id
        browser_kind = normalize_agent_browser_kind(str(browser_hint or "auto"))
        if browser_kind == "auto":
            browser_kind = str(discover_system_agent_browser().get("browserKind") or "chrome")
        browser_selection = "Edge" if browser_kind == "edge" else "Chrome"
        profile_dir = configured_agent_browser_profile_dir(browser_kind)
        return [
            self._browser_url_for_step(step),
            "use_profile=True",
            f"profile_path={profile_dir}",
            f"browser_selection={browser_selection}",
            "maximized=True",
        ]

    def _normalize_score(self, value: float) -> float:
        return round(max(0.0, min(1.0, value)), 3)

    def _effective_step_thresholds(self, calibration: Dict[str, Any]) -> Dict[str, float]:
        review_threshold = STEP_REVIEW_THRESHOLD
        exclude_threshold = STEP_EXCLUDE_THRESHOLD
        runs = int(calibration.get("runs") or 0)
        if runs < CALIBRATION_MIN_RUNS:
            return {"review": review_threshold, "exclude": exclude_threshold}
        success_rate = calibration.get("successRate")
        fallback_heavy_rate = calibration.get("fallbackHeavyRate")
        fallback_failure_rate = calibration.get("fallbackFailureRate")
        failure_rate = calibration.get("failureRate")
        compile_blocked_rate = calibration.get("compileBlockedRate")
        native_success_rate = calibration.get("nativeSuccessRate")
        if success_rate is not None and success_rate >= 0.9:
            review_threshold -= 0.04
            exclude_threshold -= 0.02
        elif success_rate is not None and success_rate <= 0.5:
            review_threshold += 0.04
            exclude_threshold += 0.03
        if fallback_heavy_rate is not None and fallback_heavy_rate >= 0.4:
            review_threshold += 0.04
            exclude_threshold += 0.02
        if fallback_failure_rate is not None and fallback_failure_rate >= 0.18:
            review_threshold += 0.03
            exclude_threshold += 0.03
        if failure_rate is not None and failure_rate >= 0.25:
            review_threshold += 0.02
            exclude_threshold += 0.03
        if compile_blocked_rate is not None and compile_blocked_rate >= 0.34:
            review_threshold += 0.03
            exclude_threshold += 0.03
        if native_success_rate is not None and native_success_rate >= 0.9:
            review_threshold -= 0.01
        elif native_success_rate is not None and native_success_rate <= 0.45:
            review_threshold += 0.02
        return {
            "review": self._normalize_score(review_threshold),
            "exclude": self._normalize_score(exclude_threshold),
        }

    def _effective_script_thresholds(self, calibration: Dict[str, Any]) -> Dict[str, float]:
        trusted_threshold = SCRIPT_TRUSTED_THRESHOLD
        review_threshold = SCRIPT_REVIEW_THRESHOLD
        fallback_heavy_threshold = SCRIPT_FALLBACK_HEAVY_THRESHOLD
        accepted_ratio_min = SCRIPT_BLOCKED_ACCEPTED_RATIO_MIN
        excluded_ratio_max = SCRIPT_BLOCKED_EXCLUDED_RATIO_MAX
        runs = int(calibration.get("runs") or 0)
        if runs < CALIBRATION_MIN_RUNS:
            return {
                "trusted": trusted_threshold,
                "review": review_threshold,
                "fallbackHeavy": fallback_heavy_threshold,
                "blockedAcceptedRatioMin": accepted_ratio_min,
                "blockedExcludedRatioMax": excluded_ratio_max,
            }
        completed_rate = calibration.get("completedRate")
        fallback_heavy_rate = calibration.get("fallbackHeavyRate")
        fallback_failure_rate = calibration.get("fallbackFailureRate")
        failure_rate = calibration.get("failureRate")
        compile_blocked_rate = calibration.get("compileBlockedRate")
        review_required_rate = calibration.get("reviewRequiredRate")
        profile_augmented_rate = calibration.get("profileAugmentedRatio")
        native_success_rate = calibration.get("nativeSuccessRate")
        local_repair_rate = calibration.get("localRepairRate")
        source = str(calibration.get("source") or "").strip().lower()
        if completed_rate is not None and completed_rate >= 0.9:
            trusted_threshold -= 0.03
            review_threshold -= 0.02
        elif completed_rate is not None and completed_rate <= 0.55:
            trusted_threshold += 0.04
            review_threshold += 0.03
        if fallback_heavy_rate is not None and fallback_heavy_rate >= 0.4:
            trusted_threshold += 0.03
            review_threshold += 0.02
            fallback_heavy_threshold += 0.02
        if fallback_failure_rate is not None and fallback_failure_rate >= 0.15:
            trusted_threshold += 0.03
            review_threshold += 0.02
            fallback_heavy_threshold += 0.03
        if failure_rate is not None and failure_rate >= 0.2:
            trusted_threshold += 0.02
            review_threshold += 0.02
        if compile_blocked_rate is not None and compile_blocked_rate >= 0.25:
            accepted_ratio_min += 0.08
            excluded_ratio_max -= 0.05
        if review_required_rate is not None and review_required_rate >= 0.5:
            trusted_threshold += 0.02
        if profile_augmented_rate is not None and profile_augmented_rate >= 0.6:
            trusted_threshold += 0.02
            review_threshold += 0.01
            accepted_ratio_min += 0.04
        elif profile_augmented_rate is not None and profile_augmented_rate <= 0.2:
            trusted_threshold -= 0.01
        if native_success_rate is not None and native_success_rate >= 0.88:
            trusted_threshold -= 0.02
            review_threshold -= 0.01
        elif native_success_rate is not None and native_success_rate <= 0.5:
            trusted_threshold += 0.02
            review_threshold += 0.01
        if local_repair_rate is not None and local_repair_rate >= 0.4:
            trusted_threshold += 0.03
            review_threshold += 0.02
            fallback_heavy_threshold += 0.02
        elif local_repair_rate is not None and local_repair_rate <= 0.15 and runs >= CALIBRATION_MIN_RUNS:
            trusted_threshold -= 0.01
        if source == "fingerprint":
            trusted_threshold += 0.01
            review_threshold += 0.01
        elif source == "script" and runs >= 5 and completed_rate is not None and completed_rate >= 0.9:
            trusted_threshold -= 0.01
        return {
            "trusted": self._normalize_score(trusted_threshold),
            "review": self._normalize_score(review_threshold),
            "fallbackHeavy": self._normalize_score(fallback_heavy_threshold),
            "blockedAcceptedRatioMin": self._normalize_score(accepted_ratio_min),
            "blockedExcludedRatioMax": self._normalize_score(excluded_ratio_max),
        }

    def _normalize_trace_signals(self, step: Dict[str, Any]) -> Dict[str, Any]:
        signals = dict(step.get("signals") or {}) if isinstance(step.get("signals"), dict) else {}
        metadata = dict(step.get("metadata") or {}) if isinstance(step.get("metadata"), dict) else {}
        verification = dict(step.get("verification") or {})
        recovery = dict(step.get("recovery") or {})
        scene = dict(step.get("scene") or {})
        execution = dict(signals.get("execution") or metadata.get("executionRoute") or {})
        binding = dict(signals.get("binding") or {})
        preflight = dict(signals.get("preflight") or {})
        verification_signal = dict(signals.get("verification") or {})
        recovery_signal = dict(signals.get("recovery") or {})
        failure_category = str(signals.get("failureCategory") or "").strip().lower() or "unknown"
        return {
            "binding": {
                "requestedAppId": binding.get("requestedAppId") or metadata.get("requestedAppId"),
                "resolvedAppId": binding.get("resolvedAppId") or metadata.get("resolvedAppId") or step.get("appId"),
                "bindingMode": binding.get("bindingMode") or metadata.get("bindingMode") or "none",
                "bindingConfidence": binding.get("bindingConfidence")
                if binding.get("bindingConfidence") is not None
                else metadata.get("bindingConfidence"),
                "bindingEvidence": dict(binding.get("bindingEvidence") or metadata.get("bindingEvidence") or {}),
            },
            "preflight": {
                "focusConfirmed": bool(
                    preflight.get("focusConfirmed")
                    or metadata.get("focusConfirmed")
                    or dict(metadata.get("environmentProbe") or {}).get("focusKnown")
                ),
                "windowBound": bool(preflight.get("windowBound") or dict(step.get("target") or {}).get("window")),
                "sceneBound": bool(preflight.get("sceneBound") or scene.get("pageIdentity")),
                "blockerDetected": bool(
                    preflight.get("blockerDetected")
                    or str(scene.get("blockerState") or "").strip().lower() not in {"", "none", "ready"}
                ),
                "riskDowngraded": bool(
                    preflight.get("riskDowngraded")
                    or metadata.get("visualGuardSkipped")
                    or metadata.get("blockedReason")
                ),
            },
            "verification": {
                "passed": verification_signal.get("passed")
                if verification_signal.get("passed") is not None
                else verification.get("passed"),
                "status": verification_signal.get("status") or verification.get("status"),
                "level": verification_signal.get("level") or verification.get("level"),
                "reason": verification_signal.get("reason") or verification.get("reason"),
                "blockedReason": verification_signal.get("blockedReason") or metadata.get("blockedReason"),
            },
            "recovery": {
                "semanticPathTried": bool(recovery_signal.get("semanticPathTried", True)),
                "controlledFallbackTried": bool(
                    recovery_signal.get("controlledFallbackTried")
                    or (recovery.get("performed") and str(recovery.get("strategy") or "").strip().lower() in {"retry", "direct"})
                ),
                "visualFallbackTried": bool(
                    recovery_signal.get("visualFallbackTried")
                    or (recovery.get("performed") and str(recovery.get("strategy") or "").strip().lower() == "visual")
                ),
                "strictVerificationApplied": bool(
                    recovery_signal.get("strictVerificationApplied")
                    or str(verification.get("level") or "").strip().lower() in {"verified", "review_required", "failed"}
                ),
                "finalRecoveryStage": recovery_signal.get("finalRecoveryStage")
                or ("visual_fallback" if str(recovery.get("strategy") or "").strip().lower() == "visual" else "semantic_path"),
                "fallbackOrder": list(recovery_signal.get("fallbackOrder") or recovery.get("fallbackOrder") or []),
            },
            "execution": {
                "route": str(execution.get("route") or "").strip().lower() or None,
                "nativeCommand": bool(execution.get("nativeCommand")),
                "structuredAccessibility": bool(execution.get("structuredAccessibility")),
                "visualLocator": bool(execution.get("visualLocator")),
                "coordinateFallback": bool(execution.get("coordinateFallback")),
                "humanApprovalRequired": bool(execution.get("humanApprovalRequired")),
            },
            "failureCategory": failure_category,
        }

    def _step_decision_explainer(
        self,
        *,
        assessment_status: str,
        trace_signals: Dict[str, Any],
        score: float,
        calibration: Dict[str, Any],
    ) -> Dict[str, Any]:
        binding = dict(trace_signals.get("binding") or {})
        preflight = dict(trace_signals.get("preflight") or {})
        recovery = dict(trace_signals.get("recovery") or {})
        execution = dict(trace_signals.get("execution") or {})
        failure_category = str(trace_signals.get("failureCategory") or "unknown")
        calibration_runs = int(calibration.get("runs") or 0)
        decision_reason_group = "compile_rule"
        if failure_category == "binding" or (
            str(binding.get("bindingMode") or "").strip().lower() in {"heuristic", "none"}
            and float(binding.get("bindingConfidence") or 0.0) < 0.6
        ):
            decision_reason_group = "binding_risk"
        elif bool(preflight.get("blockerDetected")) or not bool(preflight.get("windowBound")):
            decision_reason_group = "preflight_risk"
        elif bool(recovery.get("visualFallbackTried")) or str(trace_signals.get("failureCategory") or "") == "visual_fallback":
            decision_reason_group = "recovery_heavy"
        elif calibration_runs >= CALIBRATION_MIN_RUNS and score < STEP_REVIEW_THRESHOLD:
            decision_reason_group = "calibration_drag"
        return {
            "decisionScope": "step",
            "decisionReasonGroup": decision_reason_group,
            "decisionSignals": {
                "binding": binding,
                "preflight": preflight,
                "recovery": recovery,
                "execution": execution,
                "failureCategory": failure_category,
                "assessmentStatus": assessment_status,
            },
        }

    def _script_decision_explainer(
        self,
        *,
        assessment_status: str,
        accepted_ratio: float,
        excluded_ratio: float,
        recovery_ratio: float,
        profile_augmented_ratio: float,
        calibration: Dict[str, Any],
        binding_summary: Dict[str, Any],
        preflight_summary: Dict[str, Any],
        recovery_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        decision_reason_group = "compile_rule"
        if float(binding_summary.get("lowConfidenceRatio") or 0.0) >= 0.5:
            decision_reason_group = "binding_risk"
        elif int(preflight_summary.get("blockerDetectedSteps") or 0) > 0:
            decision_reason_group = "preflight_risk"
        elif recovery_ratio >= 0.5 or int(recovery_summary.get("visualFallbackSteps") or 0) > 0:
            decision_reason_group = "recovery_heavy"
        elif int(calibration.get("runs") or 0) >= CALIBRATION_MIN_RUNS and (
            (calibration.get("fallbackHeavyRate") is not None and float(calibration.get("fallbackHeavyRate")) >= 0.4)
            or (calibration.get("localRepairRate") is not None and float(calibration.get("localRepairRate")) >= 0.35)
        ):
            decision_reason_group = "calibration_drag"
        return {
            "decisionScope": "script",
            "decisionReasonGroup": decision_reason_group,
            "decisionSignals": {
                "acceptedRatio": round(accepted_ratio, 3),
                "excludedRatio": round(excluded_ratio, 3),
                "recoveryHeavyRatio": round(recovery_ratio, 3),
                "profileAugmentedRatio": round(profile_augmented_ratio, 3),
                "bindingSummary": dict(binding_summary or {}),
                "preflightSummary": dict(preflight_summary or {}),
                "recoverySummary": dict(recovery_summary or {}),
                "assessmentStatus": assessment_status,
            },
        }

    def _aggregate_trace_summaries(self, steps: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        total = max(1, len(steps))
        binding_low_confidence = 0
        blocker_detected_steps = 0
        focus_confirmed_steps = 0
        window_bound_steps = 0
        scene_bound_steps = 0
        risk_downgraded_steps = 0
        visual_fallback_steps = 0
        controlled_fallback_steps = 0
        strict_verification_steps = 0
        execution_route_counts: Dict[str, int] = {}
        coordinate_fallback_steps = 0
        human_approval_steps = 0
        structured_accessibility_steps = 0
        native_command_steps = 0
        visual_locator_steps = 0
        failure_category_counts: Dict[str, int] = {}
        for step in steps:
            trace_signals = self._normalize_trace_signals(step)
            binding = dict(trace_signals.get("binding") or {})
            preflight = dict(trace_signals.get("preflight") or {})
            recovery = dict(trace_signals.get("recovery") or {})
            execution = dict(trace_signals.get("execution") or {})
            if float(binding.get("bindingConfidence") or 0.0) < 0.6:
                binding_low_confidence += 1
            if bool(preflight.get("blockerDetected")):
                blocker_detected_steps += 1
            if bool(preflight.get("focusConfirmed")):
                focus_confirmed_steps += 1
            if bool(preflight.get("windowBound")):
                window_bound_steps += 1
            if bool(preflight.get("sceneBound")):
                scene_bound_steps += 1
            if bool(preflight.get("riskDowngraded")):
                risk_downgraded_steps += 1
            if bool(recovery.get("visualFallbackTried")):
                visual_fallback_steps += 1
            if bool(recovery.get("controlledFallbackTried")):
                controlled_fallback_steps += 1
            if bool(recovery.get("strictVerificationApplied")):
                strict_verification_steps += 1
            route = str(execution.get("route") or "").strip().lower()
            if route:
                execution_route_counts[route] = int(execution_route_counts.get(route) or 0) + 1
            if bool(execution.get("coordinateFallback")) or route == "coordinate_fallback":
                coordinate_fallback_steps += 1
            if bool(execution.get("humanApprovalRequired")) or route == "human_approval":
                human_approval_steps += 1
            if bool(execution.get("structuredAccessibility")) or route == "structured_accessibility":
                structured_accessibility_steps += 1
            if bool(execution.get("nativeCommand")) or route == "native_command":
                native_command_steps += 1
            if bool(execution.get("visualLocator")) or route == "visual_locator":
                visual_locator_steps += 1
            failure_category = str(trace_signals.get("failureCategory") or "unknown")
            failure_category_counts[failure_category] = int(failure_category_counts.get(failure_category) or 0) + 1
        return {
            "bindingSummary": {
                "lowConfidenceSteps": binding_low_confidence,
                "lowConfidenceRatio": round(binding_low_confidence / total, 3),
            },
            "preflightSummary": {
                "focusConfirmedSteps": focus_confirmed_steps,
                "windowBoundSteps": window_bound_steps,
                "sceneBoundSteps": scene_bound_steps,
                "blockerDetectedSteps": blocker_detected_steps,
                "riskDowngradedSteps": risk_downgraded_steps,
            },
            "recoverySummary": {
                "controlledFallbackSteps": controlled_fallback_steps,
                "visualFallbackSteps": visual_fallback_steps,
                "strictVerificationSteps": strict_verification_steps,
                "failureCategoryCounts": failure_category_counts,
            },
            "executionSummary": {
                "routesUsed": sorted(execution_route_counts.keys()),
                "routeCounts": execution_route_counts,
                "coordinateFallbackSteps": coordinate_fallback_steps,
                "humanApprovalSteps": human_approval_steps,
                "structuredAccessibilitySteps": structured_accessibility_steps,
                "nativeCommandSteps": native_command_steps,
                "visualLocatorSteps": visual_locator_steps,
            },
        }

    def _assessment_for_step(
        self,
        step: Dict[str, Any],
        *,
        app_id: str,
        compiled_use: str,
        robot_semantic: Optional[RPAStepRobotSemantic],
    ) -> RPAStepAssessment:
        verification = dict(step.get("verification") or {})
        recovery = dict(step.get("recovery") or {})
        risk = dict(step.get("risk") or {})
        metadata = dict(step.get("metadata") or {}) if isinstance(step.get("metadata"), dict) else {}
        trace_phase = str(step.get("phase") or metadata.get("tracePhase") or "action").strip().lower() or "action"
        trace_signals = self._normalize_trace_signals(step)
        execution = dict(trace_signals.get("execution") or metadata.get("executionRoute") or {})
        execution_route = str(execution.get("route") or "").strip().lower()
        status = str(metadata.get("status") or "").strip().lower()
        reasons: List[str] = []
        score = 0.52
        trust_model = {
            "version": TRUST_MODEL_VERSION,
            "stepReviewThreshold": STEP_REVIEW_THRESHOLD,
            "stepExcludeThreshold": STEP_EXCLUDE_THRESHOLD,
            "calibrationMinRuns": CALIBRATION_MIN_RUNS,
        }
        calibration = self.script_store.get_action_calibration(app_id=app_id, use=compiled_use)
        effective_thresholds = self._effective_step_thresholds(calibration)
        trust_model["effectiveStepReviewThreshold"] = effective_thresholds["review"]
        trust_model["effectiveStepExcludeThreshold"] = effective_thresholds["exclude"]
        signals: Dict[str, Any] = {
            "compiledUse": compiled_use,
            "tracePhase": trace_phase,
            "traceSignals": trace_signals,
            "verificationPassed": verification.get("passed"),
            "verificationStatus": str(verification.get("status") or "").strip().lower() or None,
            "verificationLevel": str(verification.get("level") or "").strip().lower() or None,
            "recoveryPerformed": bool(recovery.get("performed")),
            "transient": bool(recovery.get("transient")),
            "highRisk": bool(risk.get("highRiskAction")),
            "requiresPreGuard": bool(risk.get("requiresPreGuard")),
            "requiresPostGuard": bool(risk.get("requiresPostGuard")),
            "nativeSemanticReady": bool(robot_semantic and robot_semantic.library and robot_semantic.keyword),
            "historicalRuns": calibration.get("runs"),
            "historicalSuccessRate": calibration.get("successRate"),
            "historicalFallbackRate": calibration.get("fallbackRate"),
            "historicalFailureRate": calibration.get("failureRate"),
            "historicalFallbackFailureRate": calibration.get("fallbackFailureRate"),
            "historicalFallbackHeavyRate": calibration.get("fallbackHeavyRate"),
            "historicalReviewRequiredRate": calibration.get("reviewRequiredRate"),
            "historicalCompileBlockedRate": calibration.get("compileBlockedRate"),
            "historicalNativeSuccessRate": calibration.get("nativeSuccessRate"),
        }
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        window = dict(target.get("window") or {})
        selector_metadata = dict(selector.get("metadata") or {}) if isinstance(selector.get("metadata"), dict) else {}
        signals["selectorProfileAugmented"] = bool(selector_metadata.get("profileAugmented"))
        signals["windowProfileAugmented"] = bool(window.get("profileAugmented"))
        signals["profileAugmented"] = bool(signals["selectorProfileAugmented"] or signals["windowProfileAugmented"])
        signals["bindingMode"] = trace_signals["binding"].get("bindingMode")
        signals["bindingConfidence"] = trace_signals["binding"].get("bindingConfidence")
        signals["preflightBlockerDetected"] = bool(trace_signals["preflight"].get("blockerDetected"))
        signals["preflightFocusConfirmed"] = bool(trace_signals["preflight"].get("focusConfirmed"))
        signals["preflightWindowBound"] = bool(trace_signals["preflight"].get("windowBound"))
        signals["recoveryVisualFallbackTried"] = bool(trace_signals["recovery"].get("visualFallbackTried"))
        signals["failureCategory"] = trace_signals.get("failureCategory")

        if compiled_use in {"open_app", "focus_window", "find_and_type", "scroll_list", "click_toolbar_action", "wait_for_element", "double_click"}:
            score += 0.08
        if compiled_use in {"open_app", "focus_window", "find_and_type", "click_toolbar_action", "double_click"}:
            score += 0.03
        if signals["nativeSemanticReady"]:
            score += 0.04
        else:
            reasons.append("尚未匹配到稳定的原生 .robot 语义")

        verification_passed = verification.get("passed")
        verification_status = str(verification.get("status") or "").strip().lower()
        verification_level = str(verification.get("level") or "").strip().lower()
        if verification_level == "verified":
            score += 0.2
        elif verification_level == "executed_only":
            score -= 0.12
            reasons.append("动作只确认执行过，缺少稳定业务结果证据")
        elif verification_level == "soft_verified":
            if verification_status == "focus_verified" and compiled_use in {"focus_window", "open_app"}:
                score += 0.12
                reasons.append("窗口焦点已确认，按窗口级软验证保守接受")
            elif verification_status == "soft_verified_editable_target":
                score += 0.14
                reasons.append("结构化验证为原生输入控件的保守确认")
            else:
                score += 0.06
                reasons.append("结构化验证仅达到软确认")
        elif verification_level == "review_required":
            score -= 0.14
            reasons.append(f"结构化验证需要人工复核: {verification_status or 'review_required'}")
        elif verification_level == "failed" or verification_passed is False:
            score -= 0.28
            reasons.append(f"结构化验证未通过: {verification_status or 'unknown'}")
        elif verification_passed is True:
            score += 0.16
        if execution_route == "coordinate_fallback":
            score -= 0.1
            reasons.append("执行路径依赖坐标兜底，跨端复现与提级应更保守")
        elif execution_route == "human_approval":
            score -= 0.08
            reasons.append("执行路径依赖人工审批，不能视为稳定自动化主链")
        elif execution_route == "structured_accessibility":
            score += 0.03
        elif execution_route == "native_command":
            score += 0.04

        if status in {"completed", "success"}:
            score += 0.05
        if status in {"blocked", "failed", "error"}:
            score -= 0.45
            reasons.append(f"原始步骤状态为 {status}")
        if verification_status in {"visual_guard_unconfirmed", "high_risk_visual_confirmation_required", "high_risk_pre_action_confirmation_required"}:
            score -= 0.22
            reasons.append("视觉保底未明确确认")

        if bool(recovery.get("performed")):
            score -= 0.08
            reasons.append("依赖恢复链后才成功")
        if bool(recovery.get("transient")):
            score -= 0.05
            reasons.append("目标控件存在 transient 风险")

        if bool(risk.get("highRiskAction")):
            score -= 0.04
            reasons.append("高风险动作")
        if bool(risk.get("requiresPreGuard")):
            score -= 0.03
            reasons.append("依赖执行前视觉确认")
        if bool(risk.get("requiresPostGuard")):
            score -= 0.02
            reasons.append("依赖执行后视觉确认")
        if signals["profileAugmented"]:
            score -= 0.03
            reasons.append("部分定位来自 app profile 合成")

        historical_runs = int(calibration.get("runs") or 0)
        historical_success = calibration.get("successRate")
        historical_fallback_heavy = calibration.get("fallbackHeavyRate")
        historical_review_required = calibration.get("reviewRequiredRate")
        historical_compile_blocked = calibration.get("compileBlockedRate")
        historical_native_success = calibration.get("nativeSuccessRate")
        if historical_runs >= CALIBRATION_MIN_RUNS:
            if historical_success is not None:
                if historical_success >= 0.9:
                    score += 0.09
                elif historical_success >= 0.8:
                    score += 0.05
                elif historical_success <= 0.35:
                    score -= 0.12
                    reasons.append("历史运行成功率偏低")
                elif historical_success <= 0.55:
                    score -= 0.06
                    reasons.append("历史运行成功率不足")
            if historical_fallback_heavy is not None:
                if historical_fallback_heavy >= 0.5:
                    score -= 0.09
                    reasons.append("历史回退依赖较重")
                elif historical_fallback_heavy >= 0.3:
                    score -= 0.05
                    reasons.append("历史回退率偏高")
            if historical_review_required is not None and historical_review_required >= 0.5:
                score -= 0.05
                reasons.append("历史上经常需要人工复核")
            if historical_compile_blocked is not None and historical_compile_blocked >= 0.34:
                score -= 0.08
                reasons.append("历史上多次触发编译阻断")
            if signals["nativeSemanticReady"] and historical_native_success is not None:
                if historical_native_success >= 0.85:
                    score += 0.04
                elif historical_native_success <= 0.5:
                    score -= 0.04
                    reasons.append("原生 .robot 语义历史成功率不足")

        normalized = self._normalize_score(score)
        excluded = (
            status in {"blocked", "failed", "error"}
            or verification_status in {"high_risk_pre_action_confirmation_required"}
            or normalized < effective_thresholds["exclude"]
        )
        soft_verified_needs_review = (
            verification_level == "soft_verified"
            and compiled_use in {"find_and_type", "type_text"}
            and verification_status in {"soft_verified_target_only"}
        )
        review_required = (
            excluded
            or bool(risk.get("highRiskAction"))
            or normalized < effective_thresholds["review"]
            or verification_passed is False
            or verification_level == "review_required"
            or soft_verified_needs_review
        )
        assessment_status = "excluded" if excluded else ("review_required" if review_required else "accepted")
        decision = self._step_decision_explainer(
            assessment_status=assessment_status,
            trace_signals=trace_signals,
            score=normalized,
            calibration=calibration,
        )
        signals.update(decision["decisionSignals"])
        band = "high" if normalized >= 0.84 else ("medium" if normalized >= effective_thresholds["review"] else "low")
        if not reasons and assessment_status == "accepted":
            reasons.append("结构化验证稳定，适合自动编译")
        return RPAStepAssessment(
            score=normalized,
            status=assessment_status,
            band=band,
            reasons=list(dict.fromkeys(reasons)),
            review_required=review_required,
            excluded=excluded,
            signals={
                **signals,
                "decisionScope": decision["decisionScope"],
                "decisionReasonGroup": decision["decisionReasonGroup"],
                "decisionSignals": decision["decisionSignals"],
            },
            trust_model=trust_model,
        )

    def _assessment_for_script(
        self,
        steps: List[RPAScriptStep],
        *,
        excluded_steps: int,
        script_id: str,
        fingerprint: str,
    ) -> RPAScriptAssessment:
        calibration = self.script_store.get_script_calibration(script_id=script_id, fingerprint=fingerprint)
        effective_thresholds = self._effective_script_thresholds(calibration)
        trust_model = {
            "version": TRUST_MODEL_VERSION,
            "scriptTrustedThreshold": SCRIPT_TRUSTED_THRESHOLD,
            "scriptReviewThreshold": SCRIPT_REVIEW_THRESHOLD,
            "scriptFallbackHeavyThreshold": SCRIPT_FALLBACK_HEAVY_THRESHOLD,
            "blockedAcceptedRatioMin": SCRIPT_BLOCKED_ACCEPTED_RATIO_MIN,
            "blockedExcludedRatioMax": SCRIPT_BLOCKED_EXCLUDED_RATIO_MAX,
            "profileAugmentedPenaltyRatio": SCRIPT_PROFILE_AUGMENTED_PENALTY_RATIO,
            "profileAugmentedReviewRatio": SCRIPT_PROFILE_AUGMENTED_REVIEW_RATIO,
            "calibrationMinRuns": CALIBRATION_MIN_RUNS,
            "effectiveScriptTrustedThreshold": effective_thresholds["trusted"],
            "effectiveScriptReviewThreshold": effective_thresholds["review"],
            "effectiveScriptFallbackHeavyThreshold": effective_thresholds["fallbackHeavy"],
            "effectiveBlockedAcceptedRatioMin": effective_thresholds["blockedAcceptedRatioMin"],
            "effectiveBlockedExcludedRatioMax": effective_thresholds["blockedExcludedRatioMax"],
        }
        if not steps:
            return RPAScriptAssessment(
                score=0.0,
                status="compile_blocked",
                band="low",
                reasons=["没有任何可编译步骤进入 RPA draft。"],
                accepted_steps=0,
                review_required_steps=0,
                excluded_steps=excluded_steps,
                signals={
                    "acceptedRatio": 0.0,
                    "excludedRatio": 1.0,
                    "nativeSemanticRatio": 0.0,
                    "historicalScriptRuns": calibration.get("runs"),
                    "bindingSummary": {},
                    "preflightSummary": {},
                    "recoverySummary": {},
                    "decisionScope": "script",
                    "decisionReasonGroup": "compile_rule",
                    "decisionSignals": {
                        "acceptedRatio": 0.0,
                        "excludedRatio": 1.0,
                        "assessmentStatus": "compile_blocked",
                    },
                },
                trust_model=trust_model,
            )
        scores = [step.assessment.score for step in steps if step.assessment is not None]
        accepted = sum(1 for step in steps if step.assessment and step.assessment.status == "accepted")
        review_required = sum(1 for step in steps if step.assessment and step.assessment.review_required)
        native_semantic_steps = sum(
            1 for step in steps if step.assessment and bool((step.assessment.signals or {}).get("nativeSemanticReady"))
        )
        high_risk_steps = sum(
            1 for step in steps if step.assessment and bool((step.assessment.signals or {}).get("highRisk"))
        )
        recovery_heavy_steps = sum(
            1 for step in steps if step.assessment and bool((step.assessment.signals or {}).get("recoveryPerformed"))
        )
        profile_augmented_steps = sum(
            1 for step in steps if step.assessment and bool((step.assessment.signals or {}).get("profileAugmented"))
        )
        overall = self._normalize_score(sum(scores) / len(scores)) if scores else 0.0
        total_considered = len(steps) + excluded_steps
        accepted_ratio = accepted / total_considered if total_considered else 0.0
        excluded_ratio = excluded_steps / total_considered if total_considered else 0.0
        native_ratio = native_semantic_steps / len(steps) if steps else 0.0
        recovery_ratio = recovery_heavy_steps / len(steps) if steps else 0.0
        profile_augmented_ratio = profile_augmented_steps / len(steps) if steps else 0.0
        historical_runs = [
            int(((step.assessment.signals or {}).get("historicalRuns") or 0))
            for step in steps
            if step.assessment
        ]
        calibrated_steps = sum(1 for runs in historical_runs if runs >= CALIBRATION_MIN_RUNS)
        historical_success_values = [
            float((step.assessment.signals or {}).get("historicalSuccessRate"))
            for step in steps
            if step.assessment
            and int(((step.assessment.signals or {}).get("historicalRuns") or 0)) >= CALIBRATION_MIN_RUNS
            and (step.assessment.signals or {}).get("historicalSuccessRate") is not None
        ]
        historical_fallback_values = [
            float((step.assessment.signals or {}).get("historicalFallbackHeavyRate"))
            for step in steps
            if step.assessment
            and int(((step.assessment.signals or {}).get("historicalRuns") or 0)) >= CALIBRATION_MIN_RUNS
            and (step.assessment.signals or {}).get("historicalFallbackHeavyRate") is not None
        ]
        historical_native_values = [
            float((step.assessment.signals or {}).get("historicalNativeSuccessRate"))
            for step in steps
            if step.assessment
            and int(((step.assessment.signals or {}).get("historicalRuns") or 0)) >= CALIBRATION_MIN_RUNS
            and (step.assessment.signals or {}).get("historicalNativeSuccessRate") is not None
        ]
        avg_historical_success = (
            round(sum(historical_success_values) / len(historical_success_values), 3)
            if historical_success_values
            else None
        )
        avg_historical_fallback = (
            round(sum(historical_fallback_values) / len(historical_fallback_values), 3)
            if historical_fallback_values
            else None
        )
        avg_historical_native = (
            round(sum(historical_native_values) / len(historical_native_values), 3)
            if historical_native_values
            else None
        )
        script_runs = int(calibration.get("runs") or 0)
        script_completed_rate = calibration.get("completedRate")
        script_profile_augmented_rate = calibration.get("profileAugmentedRatio")
        script_step_level_fallback_rate = calibration.get("stepLevelFallbackRate")
        script_avg_recovered_steps = calibration.get("avgRecoveredSteps")
        script_local_repair_rate = calibration.get("localRepairRate")
        script_avg_repaired_steps = calibration.get("avgRepairedSteps")
        if calibrated_steps:
            if avg_historical_success is not None and avg_historical_success >= 0.88:
                overall = self._normalize_score(overall + 0.04)
            elif avg_historical_success is not None and avg_historical_success <= 0.5:
                overall = self._normalize_score(overall - 0.06)
            if avg_historical_fallback is not None and avg_historical_fallback >= 0.4:
                overall = self._normalize_score(overall - 0.05)
            if avg_historical_native is not None and avg_historical_native >= 0.85 and native_ratio >= 0.5:
                overall = self._normalize_score(overall + 0.02)
        if profile_augmented_ratio >= SCRIPT_PROFILE_AUGMENTED_PENALTY_RATIO:
            profile_penalty = 0.04
            if (
                script_runs >= CALIBRATION_MIN_RUNS
                and script_completed_rate is not None
                and float(script_completed_rate) >= 0.85
                and script_profile_augmented_rate is not None
                and float(script_profile_augmented_rate) >= SCRIPT_PROFILE_AUGMENTED_PENALTY_RATIO
                and calibration.get("nativeSuccessRate") is not None
                and float(calibration.get("nativeSuccessRate")) >= 0.85
            ):
                profile_penalty = 0.02
            overall = self._normalize_score(overall - profile_penalty)
        if profile_augmented_ratio >= SCRIPT_PROFILE_AUGMENTED_REVIEW_RATIO:
            overall = self._normalize_score(overall - 0.05)
        if script_step_level_fallback_rate is not None and float(script_step_level_fallback_rate) >= 0.35:
            overall = self._normalize_score(overall - 0.03)
        if script_local_repair_rate is not None and float(script_local_repair_rate) >= 0.35:
            overall = self._normalize_score(overall - 0.04)
        reasons: List[str] = []
        if excluded_steps:
            reasons.append(f"{excluded_steps} 个步骤因阻断/失败被排除")
        if review_required:
            reasons.append(f"{review_required} 个步骤需要 review")
        if calibrated_steps:
            reasons.append(f"{calibrated_steps} 个步骤带有历史运行校准数据")
        if avg_historical_fallback is not None and avg_historical_fallback >= 0.4:
            reasons.append("历史回退依赖偏高，建议保留 ComputerUse 回退")
        if script_profile_augmented_rate is not None and float(script_profile_augmented_rate) >= 0.6:
            reasons.append("历史上这类流程较多依赖 app profile 合成定位")
        if script_step_level_fallback_rate is not None and float(script_step_level_fallback_rate) >= 0.35:
            reasons.append("历史上这类流程较多依赖 step-level 局部恢复")
        if script_local_repair_rate is not None and float(script_local_repair_rate) >= 0.35:
            reasons.append("历史上这类流程较多依赖局部脚本修补")
        profile_augmented_review = (
            profile_augmented_ratio >= SCRIPT_PROFILE_AUGMENTED_REVIEW_RATIO
            and (
                script_runs < CALIBRATION_MIN_RUNS
                or script_completed_rate is None
                or float(script_completed_rate) < 0.8
            )
        ) or (
            script_profile_augmented_rate is not None
            and float(script_profile_augmented_rate) >= SCRIPT_PROFILE_AUGMENTED_REVIEW_RATIO
            and (
                script_completed_rate is None
                or float(script_completed_rate) < 0.78
            )
        )
        if profile_augmented_ratio >= SCRIPT_PROFILE_AUGMENTED_PENALTY_RATIO:
            reasons.append("较多步骤依赖 app profile 合成定位，脚本可信度会更保守")
        if profile_augmented_review:
            reasons.append("大量步骤依赖 app profile 合成定位，当前仍需人工复核")
        native_fast_track = (
            review_required == 0
            and excluded_steps == 0
            and accepted == len(steps)
            and native_ratio >= 0.8
            and recovery_ratio < 0.2
            and profile_augmented_ratio < SCRIPT_PROFILE_AUGMENTED_PENALTY_RATIO
            and overall >= (effective_thresholds["trusted"] - 0.03)
        )
        if native_fast_track:
            reasons.append("全部步骤具备稳定原生语义且无 review，触发 native fast-track")
        summaries = self._aggregate_trace_summaries(
            [
                {
                    "signals": dict((step.assessment.signals or {}).get("traceSignals") or {}),
                    "metadata": dict(step.metadata or {}),
                    "phase": (step.assessment.signals or {}).get("tracePhase"),
                    "verification": dict(step.verification or {}),
                    "recovery": dict(step.recovery or {}),
                    "scene": dict((step.metadata or {}).get("scene") or {}),
                }
                for step in steps
                if step.assessment is not None
            ]
        )
        status = "accepted"
        if accepted_ratio < effective_thresholds["blockedAcceptedRatioMin"] and excluded_ratio > effective_thresholds["blockedExcludedRatioMax"]:
            status = "compile_blocked"
            reasons.append("有效步骤占比过低，当前流程不适合直接导出为 RPA")
        elif overall < effective_thresholds["fallbackHeavy"] or recovery_ratio >= 0.5 or (avg_historical_fallback is not None and avg_historical_fallback >= 0.45):
            status = "fallback_heavy"
            reasons.append("整体置信度偏低或恢复链依赖过重，建议优先保留 ComputerUse 回退")
        elif script_local_repair_rate is not None and float(script_local_repair_rate) >= 0.45:
            status = "fallback_heavy"
            reasons.append("历史上局部修补依赖较高，建议保留 RPA -> ComputerUse 自愈链")
        elif review_required or excluded_steps or ((overall < effective_thresholds["trusted"]) and not native_fast_track) or profile_augmented_review:
            status = "review_required"
        if overall < effective_thresholds["review"] and status == "accepted":
            status = "review_required"
            reasons.append("整体置信度未达到 accepted 阈值")
        decision = self._script_decision_explainer(
            assessment_status=status,
            accepted_ratio=accepted_ratio,
            excluded_ratio=excluded_ratio,
            recovery_ratio=recovery_ratio,
            profile_augmented_ratio=profile_augmented_ratio,
            calibration=calibration,
            binding_summary=summaries["bindingSummary"],
            preflight_summary=summaries["preflightSummary"],
            recovery_summary=summaries["recoverySummary"],
        )
        band = "high" if (status == "accepted" and overall >= (effective_thresholds["trusted"] - 0.03)) else ("medium" if overall >= effective_thresholds["review"] else "low")
        if not reasons:
            reasons.append("步骤验证稳定，可直接作为 RPA draft 使用")
        return RPAScriptAssessment(
            score=overall,
            status=status,
            band=band,
            reasons=reasons,
            accepted_steps=accepted,
            review_required_steps=review_required,
            excluded_steps=excluded_steps,
            signals={
                "acceptedRatio": round(accepted_ratio, 3),
                "excludedRatio": round(excluded_ratio, 3),
                "nativeSemanticRatio": round(native_ratio, 3),
                "recoveryHeavyRatio": round(recovery_ratio, 3),
                "bindingSummary": summaries["bindingSummary"],
                "preflightSummary": summaries["preflightSummary"],
                "recoverySummary": summaries["recoverySummary"],
                "executionSummary": summaries["executionSummary"],
                "profileAugmentedSteps": profile_augmented_steps,
                "profileAugmentedRatio": round(profile_augmented_ratio, 3),
                "highRiskSteps": high_risk_steps,
                "nativeSemanticSteps": native_semantic_steps,
                "calibratedSteps": calibrated_steps,
                "historicalSuccessRate": avg_historical_success,
                "historicalFallbackHeavyRate": avg_historical_fallback,
                "historicalNativeSuccessRate": avg_historical_native,
                "historicalScriptRuns": calibration.get("runs"),
                "historicalScriptCompletedRate": calibration.get("completedRate"),
                "historicalScriptFallbackHeavyRate": calibration.get("fallbackHeavyRate"),
                "historicalScriptNativeSuccessRate": calibration.get("nativeSuccessRate"),
                "historicalScriptReviewRequiredRate": calibration.get("reviewRequiredRate"),
                "historicalScriptCompileBlockedRate": calibration.get("compileBlockedRate"),
                "historicalScriptCalibrationSource": calibration.get("source"),
                "historicalScriptProfileAugmentedRatio": calibration.get("profileAugmentedRatio"),
                "historicalScriptStepLevelFallbackRate": calibration.get("stepLevelFallbackRate"),
                "historicalScriptAvgRecoveredSteps": calibration.get("avgRecoveredSteps"),
                "historicalScriptLocalRepairRate": calibration.get("localRepairRate"),
                "historicalScriptAvgRepairedSteps": calibration.get("avgRepairedSteps"),
                "decisionScope": decision["decisionScope"],
                "decisionReasonGroup": decision["decisionReasonGroup"],
                "decisionSignals": decision["decisionSignals"],
            },
            trust_model=trust_model,
        )

    def _launch_text_for_app(self, app_id: str, step: Dict[str, Any]) -> str:
        params = dict(step.get("params") or {})
        for key in ("command", "launch_command", "launchCommand", "app_name", "appName"):
            value = params.get(key)
            if isinstance(value, list) and value:
                return str(value[0]).strip()
            if value not in (None, ""):
                return str(value).strip()
        launch_command = self.app_profiles.launch_command_for(app_id)
        if launch_command:
            return str(launch_command[0]).strip()
        return app_id

    def _prefer_bridge_runtime(self, *, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        if params.get("prefer_native_windows_semantics") is True or params.get("use_native_windows_semantics") is True:
            return False
        risk = dict(step.get("risk") or {})
        risk_details = dict(risk.get("details") or {})
        action = str(step.get("action") or "").strip().lower()
        if action:
            return True
        if params.get("target_text") not in (None, "") or risk_details.get("targetText") not in (None, ""):
            return True
        if params.get("file_path") not in (None, "") or list(params.get("file_paths") or []) or list(params.get("attachment_paths") or []):
            return True
        if params.get("point") not in (None, ""):
            return True
        if params.get("point_rect") not in (None, ""):
            return True
        if list(params.get("point_candidates") or []):
            return True
        if dict(params.get("spatial_anchor") or {}):
            return True
        if bool(params.get("prefer_sendinput_click")) or bool(params.get("window_typing")):
            return True
        if params.get("preferred_result_region") not in (None, "") or params.get("preferred_result_index") not in (None, ""):
            return True
        if params.get("required_exact_match") not in (None, "") or list(params.get("forbidden_result_tokens") or []):
            return True
        if params.get("post_action_settle_timeout_ms") not in (None, "") or risk_details.get("postActionSettleTimeoutMs") not in (None, ""):
            return True
        if params.get("post_action_settle_poll_ms") not in (None, "") or risk_details.get("postActionSettlePollMs") not in (None, ""):
            return True
        if params.get("post_action_stable_rounds") not in (None, "") or risk_details.get("postActionStableRounds") not in (None, ""):
            return True
        if params.get("abort_on_major_deviation") not in (None, "") or risk_details.get("abortOnMajorDeviation") not in (None, ""):
            return True
        if dict(risk_details.get("updateRequest") or {}):
            return True
        return False

    def _robot_semantic_for_step(self, *, app_id: str, step: Dict[str, Any], compiled_use: str, params: Dict[str, Any]) -> Optional[RPAStepRobotSemantic]:
        fallback_keyword = " ".join(part.capitalize() for part in compiled_use.split("_") if part)
        action_name = str(params.get("action_name") or params.get("toolbar_action_name") or "").strip().lower()
        step = self._profile_augmented_step(app_id, step, action_name=action_name)
        if app_id in {"browser_checkout", "browser", "chrome", "edge"}:
            browser_locator = self._browser_selector_locator(step)
            if compiled_use == "open_app":
                return RPAStepRobotSemantic(
                    library="RPA.Browser.Selenium",
                    keyword="Open Available Browser",
                    arguments=self._browser_open_arguments(app_id=app_id, step=step),
                    fallback_keyword=fallback_keyword,
                    notes=["浏览器场景优先导出为 Selenium 打开浏览器语义；使用 Agent 专用浏览器 profile。"],
                )
            select_value = self._browser_select_value(params)
            if compiled_use == "find_and_type" and browser_locator and select_value:
                if self._is_browser_file_input_like(step, params):
                    return RPAStepRobotSemantic(
                        library="RPA.Browser.Selenium",
                        keyword="Choose File",
                        arguments=[browser_locator, select_value],
                        fallback_keyword=fallback_keyword,
                        locator=browser_locator,
                        notes=["浏览器文件上传优先映射为 Selenium 的 Choose File。"],
                    )
                if self._is_browser_select_like(step, params):
                    return RPAStepRobotSemantic(
                        library="RPA.Browser.Selenium",
                        keyword="Select From List By Value",
                        arguments=[browser_locator, select_value],
                        fallback_keyword=fallback_keyword,
                        locator=browser_locator,
                        notes=["浏览器下拉/列表选择优先映射为 Selenium 的 Select From List By Value。"],
                    )
                keyword = "Input Password" if self._is_password_like(step, params) else "Input Text When Element Is Visible"
                return RPAStepRobotSemantic(
                    library="RPA.Browser.Selenium",
                    keyword=keyword,
                    arguments=[browser_locator, select_value],
                    fallback_keyword=fallback_keyword,
                    locator=browser_locator,
                    notes=["浏览器输入步骤优先映射为 Selenium 可见后输入。"],
                )
            if compiled_use == "focus_window":
                window_title = str(((step.get("target") or {}).get("window") or {}).get("title") or "").strip()
                if window_title:
                    return RPAStepRobotSemantic(
                        library="RPA.Browser.Selenium",
                        keyword="Switch Window",
                        arguments=[window_title],
                        fallback_keyword=fallback_keyword,
                        notes=["浏览器聚焦优先按窗口标题切换。"],
                    )
            if compiled_use == "click_toolbar_action" and browser_locator:
                action_name = str(params.get("action_name") or "").strip().lower()
                if action_name == "refresh":
                    return RPAStepRobotSemantic(
                        library="RPA.Browser.Selenium",
                        keyword="Reload Page",
                        arguments=[],
                        fallback_keyword=fallback_keyword,
                        notes=["浏览器刷新优先映射为原生 Reload Page。"],
                    )
                if action_name == "back":
                    return RPAStepRobotSemantic(
                        library="RPA.Browser.Selenium",
                        keyword="Go Back",
                        arguments=[],
                        fallback_keyword=fallback_keyword,
                        notes=["浏览器返回优先映射为原生 Go Back。"],
                    )
                if action_name == "forward":
                    return RPAStepRobotSemantic(
                        library="RPA.Browser.Selenium",
                        keyword="Execute Javascript",
                        arguments=["window.history.forward();"],
                        fallback_keyword=fallback_keyword,
                        notes=["当前本地浏览器库未直接暴露 Go Forward，改用 history.forward()。"],
                    )
                if self._is_browser_checkbox_like(step, params):
                    return RPAStepRobotSemantic(
                        library="RPA.Browser.Selenium",
                        keyword="Select Checkbox",
                        arguments=[browser_locator],
                        fallback_keyword=fallback_keyword,
                        locator=browser_locator,
                        notes=["浏览器确认/同意类复选操作优先映射为 Selenium 的 Select Checkbox。"],
                    )
                if action_name not in {"confirm", "pay", "submit"} and self._is_browser_button_like(step, params):
                    return RPAStepRobotSemantic(
                        library="RPA.Browser.Selenium",
                        keyword="Click Button",
                        arguments=[browser_locator],
                        fallback_keyword=fallback_keyword,
                        locator=browser_locator,
                        notes=["浏览器按钮类操作优先映射为 Selenium 的 Click Button。"],
                    )
                keyword = "Click Element When Clickable" if action_name in {"confirm", "pay", "submit"} else "Click Element When Visible"
                return RPAStepRobotSemantic(
                    library="RPA.Browser.Selenium",
                    keyword=keyword,
                    arguments=[browser_locator],
                    fallback_keyword=fallback_keyword,
                    locator=browser_locator,
                    notes=["浏览器高风险动作优先使用 Selenium 的等待后点击语义。"],
                )
            if compiled_use == "double_click" and browser_locator:
                return RPAStepRobotSemantic(
                    library="RPA.Browser.Selenium",
                    keyword="Double Click Element",
                    arguments=[browser_locator],
                    fallback_keyword=fallback_keyword,
                    locator=browser_locator,
                    notes=["浏览器双击优先映射为 Selenium 的 Double Click Element。"],
                )
            if compiled_use == "hotkey" and params.get("sequence") not in (None, ""):
                return RPAStepRobotSemantic(
                    library="RPA.Browser.Selenium",
                    keyword="Press Keys",
                    arguments=["None", params.get("sequence")],
                    fallback_keyword=fallback_keyword,
                    notes=["浏览器快捷键优先映射为 Selenium 的 Press Keys。"],
                )
            if compiled_use == "scroll_list":
                amount = int(params.get("amount") or params.get("delta") or 600)
                if browser_locator:
                    return RPAStepRobotSemantic(
                        library="RPA.Browser.Selenium",
                        keyword="Scroll Element Into View",
                        arguments=[browser_locator],
                        fallback_keyword=fallback_keyword,
                        locator=browser_locator,
                        notes=["浏览器滚动优先滚到目标元素。"],
                    )
                return RPAStepRobotSemantic(
                    library="RPA.Browser.Selenium",
                    keyword="Execute Javascript",
                    arguments=[f"window.scrollBy(0, {amount});"],
                    fallback_keyword=fallback_keyword,
                    notes=["浏览器滚动默认映射为 window.scrollBy。"],
                )
            if compiled_use == "wait_for_element" and browser_locator:
                return RPAStepRobotSemantic(
                    library="RPA.Browser.Selenium",
                    keyword="Wait Until Element Is Visible",
                    arguments=[browser_locator],
                    fallback_keyword=fallback_keyword,
                    locator=browser_locator,
                    notes=["浏览器等待步骤优先映射为可见等待。"],
                )
            if compiled_use == "capture_screenshot":
                if browser_locator:
                    return RPAStepRobotSemantic(
                        library="RPA.Browser.Selenium",
                        keyword="Capture Element Screenshot",
                        arguments=[browser_locator, "${OUTPUT DIR}${/}step_capture.png"],
                        fallback_keyword=fallback_keyword,
                        locator=browser_locator,
                        notes=["浏览器截图优先截取目标元素。"],
                    )
                return RPAStepRobotSemantic(
                    library="RPA.Browser.Selenium",
                    keyword="Capture Page Screenshot",
                    arguments=["${OUTPUT DIR}${/}step_capture.png"],
                    fallback_keyword=fallback_keyword,
                    notes=["浏览器截图默认导出为整页截图。"],
                )
            return None

        locator = self._combined_windows_locator(step)
        notes: List[str] = []
        if self._prefer_bridge_runtime(step=step, params=params):
            return None

        if compiled_use == "open_app":
            launch_text = self._launch_text_for_app(app_id, step)
            if launch_text:
                return RPAStepRobotSemantic(
                    library="RPA.Windows",
                    keyword="Windows Run",
                    arguments=[launch_text],
                    fallback_keyword=fallback_keyword,
                    notes=["优先使用 RPA.Windows 原生启动关键词。"],
                )
            return None

        if compiled_use == "focus_window" and locator:
            return RPAStepRobotSemantic(
                library="RPA.Windows",
                keyword="Control Window",
                arguments=[locator],
                fallback_keyword=fallback_keyword,
                locator=locator,
                notes=["优先让 Robot Framework 直接控制目标窗口。"],
            )

        if compiled_use == "find_and_type" and locator and params.get("text") not in (None, ""):
            return RPAStepRobotSemantic(
                library="RPA.Windows",
                keyword="Set Value",
                arguments=[locator, params.get("text")],
                fallback_keyword=fallback_keyword,
                locator=locator,
                notes=["优先映射到 RPA.Windows 的 Set Value。"],
            )

        if compiled_use == "click_toolbar_action" and locator:
            if not params.get("action_name") and params.get("toolbar_action_name"):
                notes.append("已从 toolbar_action_name 规范化为 action_name。")
            return RPAStepRobotSemantic(
                library="RPA.Windows",
                keyword="Click",
                arguments=[locator],
                fallback_keyword=fallback_keyword,
                locator=locator,
                notes=["优先映射到 RPA.Windows 的 Click。", *notes],
            )

        if compiled_use == "double_click" and locator:
            return RPAStepRobotSemantic(
                library="RPA.Windows",
                keyword="Double Click",
                arguments=[locator],
                fallback_keyword=fallback_keyword,
                locator=locator,
                notes=["桌面双击优先映射到 RPA.Windows 的 Double Click。"],
            )

        if compiled_use == "hotkey" and params.get("sequence") not in (None, ""):
            return RPAStepRobotSemantic(
                library="RPA.Windows",
                keyword="Send Keys",
                arguments=[params.get("sequence")],
                fallback_keyword=fallback_keyword,
                notes=["优先映射到 RPA.Windows 的 Send Keys。"],
            )

        if compiled_use == "scroll_list":
            amount = int(params.get("amount") or params.get("delta") or 1)
            repeat = max(1, min(abs(amount), 5))
            key = "{PGDN}" if amount >= 0 else "{PGUP}"
            sequence = key * repeat
            return RPAStepRobotSemantic(
                library="RPA.Windows",
                keyword="Send Keys",
                arguments=[sequence],
                fallback_keyword=fallback_keyword,
                locator=locator,
                notes=["桌面滚动优先映射到分页滚动热键。"],
            )

        if compiled_use == "capture_screenshot" and locator:
            return RPAStepRobotSemantic(
                library="RPA.Windows",
                keyword="Screenshot",
                arguments=[locator, "${OUTPUT DIR}${/}step_capture.png"],
                fallback_keyword=fallback_keyword,
                locator=locator,
                notes=["优先映射到 RPA.Windows 的 Screenshot。"],
            )

        if compiled_use == "wait_for_element" and locator:
            return RPAStepRobotSemantic(
                library="RPA.Windows",
                keyword="Get Element",
                arguments=[locator],
                fallback_keyword=fallback_keyword,
                locator=locator,
                notes=["用 Get Element 作为显式等待的最小原生语义。"],
            )

        return None

    def _step_use(self, step: Dict[str, Any]) -> str:
        action = str(step.get("action") or "").strip()
        intent = str(step.get("intent") or "").strip()
        params = dict(step.get("params") or {})
        if action == "screenshot":
            return "capture_screenshot"
        if action == "wait" and (
            str(params.get("selector_key") or "").strip()
            or isinstance((step.get("target") or {}).get("selector"), dict)
        ):
            return "wait_for_element"
        if action == "type_text" and (
            intent == "find_and_type" or str(params.get("selector_key") or "").strip()
        ):
            return "find_and_type"
        if action == "scroll" and intent == "scroll_list":
            return "scroll_list"
        if action == "click" and bool(params.get("double")):
            return "double_click"
        if action == "click" and str(params.get("toolbar_action_name") or "").strip():
            return "click_toolbar_action"
        return action

    def _placeholder_like(self, value: Any) -> bool:
        text = str(value or "").strip()
        return bool(text.startswith("{{") and text.endswith("}}"))

    def _compiled_step_params_from_trace_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        compiled_params = dict(step.get("params") or {})
        raw_params = dict(step.get("rawParams") or {})
        for key, raw_value in raw_params.items():
            normalized_key = str(key or "").strip().lower()
            current_value = compiled_params.get(key)
            if normalized_key in self._NON_VARIABLE_PARAM_KEYS:
                compiled_params[key] = raw_value
                continue
            if isinstance(raw_value, bool) and self._placeholder_like(current_value):
                compiled_params[key] = raw_value
        return compiled_params

    def _normalize_trace_variable_item(self, item: Dict[str, Any] | None) -> Dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        name = str(item.get("name") or "").strip()
        if not name:
            return None
        original_key = str(item.get("originalKey") or "").strip().lower()
        example_value = item.get("exampleValue")
        if original_key in self._NON_VARIABLE_PARAM_KEYS:
            return None
        if isinstance(example_value, bool):
            return None
        payload = dict(item)
        payload["name"] = name
        if original_key:
            payload["originalKey"] = original_key
        return payload

    def _compile_variables(self, steps: List[Dict[str, Any]]) -> List[RPAScriptVariable]:
        variables: Dict[str, RPAScriptVariable] = {}
        for step in steps:
            for item in list(step.get("variables") or []):
                normalized_item = self._normalize_trace_variable_item(item)
                if not normalized_item:
                    continue
                name = str(normalized_item.get("name") or "").strip()
                if name in variables:
                    continue
                variables[name] = RPAScriptVariable(
                    name=name,
                    var_type=self._variable_type(normalized_item.get("exampleValue")),
                    required=bool(normalized_item.get("required", True)),
                    placeholder=str(normalized_item.get("placeholder") or f"{{{{{name}}}}}"),
                    source=str(normalized_item.get("source") or "computer_use_trace"),
                    example_value=normalized_item.get("exampleValue"),
                )
        return list(variables.values())

    def _merge_script_variables(
        self,
        existing_variables: List[Dict[str, Any]] | None,
        next_variables: List[Dict[str, Any]] | None,
    ) -> List[RPAScriptVariable]:
        merged: Dict[str, RPAScriptVariable] = {}
        for collection in (existing_variables or [], next_variables or []):
            for item in collection:
                normalized_item = self._normalize_trace_variable_item(item)
                if not normalized_item:
                    continue
                name = str(normalized_item.get("name") or "").strip()
                current = merged.get(name)
                candidate = RPAScriptVariable(
                    name=name,
                    var_type=str(normalized_item.get("type") or normalized_item.get("varType") or "string"),
                    required=bool(normalized_item.get("required", True)),
                    placeholder=str(normalized_item.get("placeholder") or f"{{{{{name}}}}}"),
                    source=str(normalized_item.get("source") or "computer_use_trace"),
                    example_value=normalized_item.get("exampleValue"),
                )
                if current is None:
                    merged[name] = candidate
                    continue
                if current.example_value in (None, "") and candidate.example_value not in (None, ""):
                    current.example_value = candidate.example_value
                if current.source == "computer_use_trace" and candidate.source != "computer_use_trace":
                    current.source = candidate.source
                current.required = bool(current.required or candidate.required)
        return list(merged.values())

    def _rpa_step_assessment_from_dict(self, payload: Dict[str, Any] | None) -> Optional[RPAStepAssessment]:
        if not isinstance(payload, dict):
            return None
        return RPAStepAssessment(
            score=float(payload.get("score") or 0.0),
            status=str(payload.get("status") or "accepted"),
            band=str(payload.get("band") or "medium"),
            reasons=[str(item) for item in list(payload.get("reasons") or []) if str(item).strip()],
            review_required=bool(payload.get("reviewRequired") or payload.get("review_required")),
            excluded=bool(payload.get("excluded")),
            signals=dict(payload.get("signals") or {}),
            trust_model=dict(payload.get("trustModel") or payload.get("trust_model") or {}),
        )

    def _script_assessment_from_dict(self, payload: Dict[str, Any] | None) -> Optional[RPAScriptAssessment]:
        if not isinstance(payload, dict):
            return None
        return RPAScriptAssessment(
            score=float(payload.get("score") or 0.0),
            status=normalize_script_assessment_status(payload.get("status") or "accepted"),
            band=str(payload.get("band") or "medium"),
            reasons=[str(item) for item in list(payload.get("reasons") or []) if str(item).strip()],
            accepted_steps=int(payload.get("acceptedSteps") or payload.get("accepted_steps") or 0),
            review_required_steps=int(payload.get("reviewRequiredSteps") or payload.get("review_required_steps") or 0),
            excluded_steps=int(payload.get("excludedSteps") or payload.get("excluded_steps") or 0),
            signals=dict(payload.get("signals") or {}),
            trust_model=dict(payload.get("trustModel") or payload.get("trust_model") or {}),
        )

    def _rpa_step_approval_from_dict(self, payload: Dict[str, Any] | None) -> Optional[RPAStepApproval]:
        if not isinstance(payload, dict):
            return None
        return RPAStepApproval(
            mode=str(payload.get("mode") or ""),
            reason=str(payload.get("reason") or ""),
            required=bool(payload.get("required", True)),
        )

    def _rpa_step_robot_from_dict(self, payload: Dict[str, Any] | None) -> Optional[RPAStepRobotSemantic]:
        if not isinstance(payload, dict):
            return None
        return RPAStepRobotSemantic(
            library=str(payload.get("library") or ""),
            keyword=str(payload.get("keyword") or ""),
            arguments=list(payload.get("arguments") or []),
            fallback_keyword=str(payload.get("fallbackKeyword") or payload.get("fallback_keyword") or ""),
            locator=str(payload.get("locator") or ""),
            notes=[str(item) for item in list(payload.get("notes") or []) if str(item).strip()],
        )

    def _rpa_step_from_dict(self, payload: Dict[str, Any]) -> RPAScriptStep:
        metadata = dict(payload.get("metadata") or {})
        if isinstance(payload.get("primitive"), dict) and payload.get("primitive"):
            metadata.setdefault("primitive", dict(payload.get("primitive") or {}))
        if isinstance(payload.get("scene"), dict) and payload.get("scene"):
            metadata.setdefault("scene", dict(payload.get("scene") or {}))
        if isinstance(payload.get("budget"), dict) and payload.get("budget"):
            metadata.setdefault("budget", dict(payload.get("budget") or {}))
        return RPAScriptStep(
            step_id=str(payload.get("stepId") or payload.get("step_id") or "step"),
            use=str(payload.get("use") or ""),
            intent=str(payload.get("intent") or payload.get("use") or ""),
            params=dict(payload.get("params") or {}),
            target=dict(payload.get("target") or {}),
            verification=dict(payload.get("verification") or {}),
            recovery=dict(payload.get("recovery") or {}),
            risk=dict(payload.get("risk") or {}),
            timing=dict(payload.get("timing") or {}),
            artifacts=[dict(item) for item in list(payload.get("artifacts") or []) if isinstance(item, dict)],
            approval=self._rpa_step_approval_from_dict(payload.get("approval")),
            assessment=self._rpa_step_assessment_from_dict(payload.get("assessment")),
            robot=self._rpa_step_robot_from_dict(payload.get("robot")),
            metadata=metadata,
        )

    def _value_signature(self, value: Any) -> str:
        return hashlib.md5(repr(value).encode("utf-8")).hexdigest()

    def _looks_like_file_path(self, value: Any) -> bool:
        if isinstance(value, list) and value:
            return all(self._looks_like_file_path(item) for item in value)
        text = str(value or "").strip()
        if not text:
            return False
        return bool(re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith(("\\\\", "/", "./", "../")))

    def _infer_variable_name(
        self,
        *,
        key: str,
        step_index: int,
        matched_steps: List[Dict[str, Any]],
        actual_values: List[Any],
    ) -> str:
        explicit = self._variable_name_for_key(key=key, step_index=step_index, matched_steps=matched_steps)
        fallback_name = self._slug(f"{key}_{step_index}", fallback=f"step_{step_index}_value")
        if explicit and explicit != fallback_name:
            return explicit
        sample_step = dict(matched_steps[0] or {})
        params = dict(sample_step.get("params") or {})
        target = dict(sample_step.get("target") or {})
        selector = dict(target.get("selector") or {})
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        selector_name = str(selector.get("name") or "").strip().lower()
        action = str(sample_step.get("action") or "").strip().lower()
        intent = str(sample_step.get("intent") or "").strip().lower()
        app_id = str(sample_step.get("appId") or "desktop").strip().lower()
        context = " ".join(item for item in [key.lower(), selector_key, selector_name, action, intent, app_id] if item)
        sample_value = next((item for item in actual_values if item not in (None, "")), None)
        sample_text = str(sample_value or "").strip()
        if isinstance(sample_value, list) and sample_value and self._looks_like_file_path(sample_value):
            if any(token in context for token in ("upload", "file", "附件", "上传")):
                return "file_paths"
            return "attachment_paths"
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", sample_text):
            if any(token in context for token in ("recipient", "receiver", "contact", "收件")):
                return "recipient_email"
            return "email_address"
        if self._looks_like_file_path(sample_text):
            if "invoice" in context:
                return "invoice_path"
            if any(token in context for token in ("upload", "file", "附件", "上传")):
                return "file_path"
            return "target_path"
        if re.match(r"^https?://", sample_text, flags=re.IGNORECASE):
            return "target_url"
        if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", sample_text):
            return "date_value"
        if re.match(r"^\d+(?:\.\d+)?$", sample_text) and any(token in context for token in ("amount", "price", "fee", "金额", "价格", "pay", "payment")):
            return "amount"
        if any(token in context for token in ("password", "passwd", "pwd", "密码")):
            return "password"
        if any(token in context for token in ("message", "chat", "reply", "输入框", "消息")):
            return "message"
        if any(token in context for token in ("search", "keyword", "query", "搜索")):
            return "search_keyword"
        if any(token in context for token in ("subject", "title", "主题")):
            return "email_subject"
        if any(token in context for token in ("contact", "recipient", "friend", "联系人")):
            return "contact_name"
        if any(token in context for token in ("song", "music", "track", "歌曲", "播放")):
            return "song_name"
        if any(token in context for token in ("country", "province", "city", "region", "国家", "地区")):
            return "region_value"
        if selector_key:
            return self._slug(selector_key, fallback=fallback_name)
        return fallback_name

    def _step_merge_key(self, step: Dict[str, Any]) -> str:
        compiled_use = self._step_use(step)
        app_id = str(step.get("appId") or "desktop").strip().lower()
        params = dict(step.get("params") or {})
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        window = dict(target.get("window") or {})
        action_name = str(params.get("action_name") or params.get("toolbar_action_name") or "").strip().lower()
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        control_type = str(selector.get("controlType") or "").strip().lower()
        class_name = str(selector.get("className") or window.get("className") or "").strip().lower()
        process_name = str(window.get("processName") or "").strip().lower()
        return "|".join(
            [
                app_id,
                compiled_use,
                str(step.get("intent") or compiled_use).strip().lower(),
                action_name,
                selector_key,
                control_type,
                class_name,
                process_name,
            ]
        )

    def _variable_name_for_key(self, *, key: str, step_index: int, matched_steps: List[Dict[str, Any]]) -> str:
        for step in matched_steps:
            for item in list(step.get("variables") or []):
                if not isinstance(item, dict):
                    continue
                if str(item.get("originalKey") or "").strip() == key and str(item.get("name") or "").strip():
                    return str(item.get("name")).strip()
        return self._slug(f"{key}_{step_index}", fallback=f"step_{step_index}_value")

    def _merge_target(self, values: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not values:
            return {}
        merged: Dict[str, Any] = {}
        reference = dict(values[0] or {})
        all_keys = {key for item in values for key in dict(item or {}).keys()}
        for key in sorted(all_keys):
            candidates = [dict(item or {}).get(key) for item in values if dict(item or {}).get(key) not in (None, "")]
            if not candidates:
                continue
            signatures = {self._value_signature(item) for item in candidates}
            if len(signatures) == 1:
                merged[key] = candidates[0]
            else:
                merged[key] = reference.get(key) or candidates[0]
                merged[f"{key}Candidates"] = list(dict.fromkeys(str(item) for item in candidates if item not in (None, "")))
        return merged

    def _merge_step_group(
        self,
        *,
        matched_steps: List[Dict[str, Any]],
        run_ids: List[str],
        step_index: int,
        total_trace_count: int,
    ) -> Dict[str, Any]:
        if not matched_steps:
            raise ValueError("matched_steps 不能为空。")
        reference = dict(matched_steps[0])
        params_union = {
            key
            for step in matched_steps
            for key in set(dict(step.get("rawParams") or {}).keys()) | set(dict(step.get("params") or {}).keys())
        }
        merged_params = dict(reference.get("params") or {})
        merged_raw_params = dict(reference.get("rawParams") or {})
        variables_by_key: Dict[str, Dict[str, Any]] = {}
        merge_examples: Dict[str, List[Any]] = {}

        for key in sorted(params_union):
            values: List[Any] = []
            for step in matched_steps:
                raw_params = dict(step.get("rawParams") or {})
                params = dict(step.get("params") or {})
                if key in raw_params:
                    values.append(raw_params.get(key))
                elif key in params:
                    values.append(params.get(key))
            actual_values = [item for item in values if item not in (None, "")]
            distinct_signatures = {self._value_signature(item) for item in actual_values}
            if not actual_values:
                continue
            if key.startswith("_") or all(isinstance(item, bool) for item in actual_values):
                representative = actual_values[0]
                merged_params[key] = representative
                merged_raw_params[key] = representative
                if len(distinct_signatures) > 1:
                    merge_examples[key] = actual_values[:5]
                continue
            if key.lower() in self._NON_VARIABLE_PARAM_KEYS:
                representative = actual_values[0]
                merged_params[key] = representative
                merged_raw_params[key] = representative
                if len(distinct_signatures) > 1:
                    merge_examples[key] = actual_values[:5]
                continue
            if len(distinct_signatures) <= 1:
                representative = actual_values[0]
                merged_params[key] = representative
                merged_raw_params[key] = representative
                continue
            variable_name = self._infer_variable_name(
                key=key,
                step_index=step_index,
                matched_steps=matched_steps,
                actual_values=actual_values,
            )
            placeholder = f"{{{{{variable_name}}}}}"
            merged_params[key] = placeholder
            merged_raw_params[key] = actual_values[0]
            variables_by_key[key] = {
                "name": variable_name,
                "placeholder": placeholder,
                "originalKey": key,
                "required": True,
                "source": "merged_trace_diff",
                "exampleValue": actual_values[0],
            }
            merge_examples[key] = actual_values[:5]

        merged_target = dict(reference.get("target") or {})
        merged_target["window"] = self._merge_target([dict((step.get("target") or {}).get("window") or {}) for step in matched_steps])
        merged_target["selector"] = self._merge_target([dict((step.get("target") or {}).get("selector") or {}) for step in matched_steps])

        merged_recovery = dict(reference.get("recovery") or {})
        merged_recovery["performed"] = any(bool(dict(step.get("recovery") or {}).get("performed")) for step in matched_steps)
        merged_recovery["transient"] = any(bool(dict(step.get("recovery") or {}).get("transient")) for step in matched_steps)
        fallback_orders = []
        for step in matched_steps:
            recovery = dict(step.get("recovery") or {})
            order = list(recovery.get("fallbackOrder") or [])
            if order:
                fallback_orders.extend(order)
        if fallback_orders:
            merged_recovery["fallbackOrder"] = list(dict.fromkeys(str(item) for item in fallback_orders if str(item).strip()))

        merged_risk = dict(reference.get("risk") or {})
        merged_risk["highRiskAction"] = any(bool(dict(step.get("risk") or {}).get("highRiskAction")) for step in matched_steps)
        merged_risk["requiresPreGuard"] = any(bool(dict(step.get("risk") or {}).get("requiresPreGuard")) for step in matched_steps)
        merged_risk["requiresPostGuard"] = any(bool(dict(step.get("risk") or {}).get("requiresPostGuard")) for step in matched_steps)

        merged_timing = dict(reference.get("timing") or {})
        merged_timing["retryLimit"] = max(int(dict(step.get("timing") or {}).get("retryLimit") or 1) for step in matched_steps)
        merged_timing["attemptCount"] = max(int(dict(step.get("timing") or {}).get("attemptCount") or 1) for step in matched_steps)
        merged_timing["waitTimeoutMs"] = max(int(dict(step.get("timing") or {}).get("waitTimeoutMs") or 6000) for step in matched_steps)
        phase_priority = {"observation": 0, "decision": 1, "action": 2, "verification": 3, "recovery": 4}
        merged_phase = max(
            [str(step.get("phase") or "action").strip().lower() or "action" for step in matched_steps],
            key=lambda item: phase_priority.get(item, 2),
        )
        merged_signals = self._aggregate_trace_summaries(matched_steps)
        merged_signals["failureCategory"] = str(
            next(
                (
                    self._normalize_trace_signals(step).get("failureCategory")
                    for step in matched_steps
                    if self._normalize_trace_signals(step).get("failureCategory") not in (None, "", "unknown")
                ),
                "unknown",
            )
        )

        metadata = dict(reference.get("metadata") or {})
        metadata["mergedTraceRunIds"] = list(dict.fromkeys(run_ids))
        metadata["mergedTraceCount"] = len(run_ids)
        metadata["mergeExamples"] = merge_examples
        metadata["mergeKey"] = self._step_merge_key(reference)
        metadata["mergeCoverage"] = round(len(list(dict.fromkeys(run_ids))) / max(1, int(total_trace_count or 1)), 3)
        metadata["autoDiscoveredVariableKeys"] = list(variables_by_key.keys())
        metadata["traceSchemaVersion"] = max(int(dict(step.get("metadata") or {}).get("traceSchemaVersion") or 1) for step in matched_steps)

        reference["params"] = merged_params
        reference["rawParams"] = merged_raw_params
        reference["variables"] = list(variables_by_key.values())
        reference["target"] = merged_target
        reference["recovery"] = merged_recovery
        reference["risk"] = merged_risk
        reference["timing"] = merged_timing
        reference["phase"] = merged_phase
        reference["signals"] = merged_signals
        reference["metadata"] = metadata
        reference["index"] = step_index
        return reference

    def _sequence_match_count(self, reference_steps: List[Dict[str, Any]], candidate_steps: List[Dict[str, Any]]) -> int:
        cursor = 0
        matched = 0
        for reference_step in reference_steps:
            merge_key = self._step_merge_key(reference_step)
            while cursor < len(candidate_steps):
                if self._step_merge_key(candidate_steps[cursor]) == merge_key:
                    matched += 1
                    cursor += 1
                    break
                cursor += 1
        return matched

    def _select_reference_trace(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(traces) <= 1:
            return dict(traces[0])
        best_trace = dict(traces[0])
        best_score = -1
        best_length = -1
        for trace in traces:
            trace_steps = [item for item in list(trace.get("steps") or []) if isinstance(item, dict)]
            score = 0
            for other in traces:
                if other is trace:
                    continue
                other_steps = [item for item in list(other.get("steps") or []) if isinstance(item, dict)]
                score += self._sequence_match_count(trace_steps, other_steps)
            if score > best_score or (score == best_score and len(trace_steps) > best_length):
                best_trace = dict(trace)
                best_score = score
                best_length = len(trace_steps)
        return best_trace

    def _align_trace_steps(self, reference_steps: List[Dict[str, Any]], candidate_steps: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        matches: Dict[int, Dict[str, Any]] = {}
        cursor = 0
        for index, reference_step in enumerate(reference_steps):
            merge_key = self._step_merge_key(reference_step)
            while cursor < len(candidate_steps):
                candidate = candidate_steps[cursor]
                if self._step_merge_key(candidate) == merge_key:
                    matches[index] = candidate
                    cursor += 1
                    break
                cursor += 1
        return matches

    def _merge_traces(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not traces:
            raise ValueError("至少需要一条 ComputerUse trace。")
        if len(traces) == 1:
            return dict(traces[0])
        reference = self._select_reference_trace(traces)
        reference_steps = [item for item in list(reference.get("steps") or []) if isinstance(item, dict)]
        merged_steps: List[Dict[str, Any]] = []
        source_run_ids = [str(trace.get("runId") or "").strip() for trace in traces if str(trace.get("runId") or "").strip()]
        source_session_ids = [str(trace.get("sessionId") or "").strip() for trace in traces if str(trace.get("sessionId") or "").strip()]
        aligned_trace_steps: List[tuple[Dict[str, Any], Dict[int, Dict[str, Any]]]] = []
        for trace in traces:
            candidate_steps = [item for item in list(trace.get("steps") or []) if isinstance(item, dict)]
            aligned_trace_steps.append((trace, self._align_trace_steps(reference_steps, candidate_steps)))

        majority_threshold = len(traces) if len(traces) <= 2 else (len(traces) // 2) + 1
        dropped_steps: List[Dict[str, Any]] = []
        for zero_based_index, step in enumerate(reference_steps):
            matched_steps: List[Dict[str, Any]] = []
            matched_run_ids: List[str] = []
            for trace, alignment in aligned_trace_steps:
                candidate = alignment.get(zero_based_index)
                if not isinstance(candidate, dict):
                    continue
                matched_steps.append(candidate)
                run_id = str(trace.get("runId") or "").strip()
                if run_id:
                    matched_run_ids.append(run_id)
            if len(matched_steps) < majority_threshold:
                dropped_steps.append(
                    {
                        "stepId": step.get("stepId"),
                        "mergeKey": self._step_merge_key(step),
                        "matchedRunIds": matched_run_ids,
                        "matchedTraceCount": len(matched_steps),
                    }
                )
                continue
            merged_steps.append(
                self._merge_step_group(
                    matched_steps=matched_steps,
                    run_ids=matched_run_ids,
                    step_index=len(merged_steps) + 1,
                    total_trace_count=len(traces),
                )
            )

        merged_trace = {
            "version": reference.get("version") or 1,
            "runId": reference.get("runId"),
            "sessionId": source_session_ids[0] if source_session_ids else reference.get("sessionId"),
            "runtimeKind": reference.get("runtimeKind") or "computer_use",
            "goal": reference.get("goal") or "computer_use_flow",
            "createdAt": reference.get("createdAt"),
            "updatedAt": utc_now_iso(),
            "metadata": {
                **dict(reference.get("metadata") or {}),
                "traceSchemaVersion": max(int(dict(trace.get("metadata") or {}).get("traceSchemaVersion") or 1) for trace in traces),
                "appId": str(reference.get("metadata", {}).get("appId") or reference_steps[0].get("appId") or "desktop"),
                "merged": True,
                "sourceRunIds": source_run_ids,
                "sourceSessionIds": list(dict.fromkeys(source_session_ids)),
                "sourceTraceCount": len(source_run_ids),
                "mergeMajorityThreshold": majority_threshold,
                "droppedOptionalSteps": dropped_steps,
            },
            "steps": merged_steps,
            "stepCount": len(merged_steps),
        }
        return merged_trace

    def compile_trace(self, trace: Dict[str, Any]) -> RPAScript:
        if not isinstance(trace, dict):
            raise ValueError("trace payload 无效。")
        steps = list(trace.get("steps") or [])
        if not steps:
            raise ValueError("trace 不包含任何步骤。")
        trace_metadata = dict(trace.get("metadata") or {})
        trace_schema_version = int(trace_metadata.get("traceSchemaVersion") or 1)
        trace_summaries = self._aggregate_trace_summaries(
            [dict(step) for step in steps if isinstance(step, dict)]
        )
        app_id = str(steps[0].get("appId") or trace.get("metadata", {}).get("appId") or "desktop")
        goal = str(trace.get("goal") or "computer_use_flow")
        script_id = self._script_id(app_id=app_id, goal=goal)
        fingerprint = self._script_fingerprint(app_id=app_id, steps=steps)
        script_steps: List[RPAScriptStep] = []
        excluded_steps = 0
        compile_issues: List[str] = []
        for step in steps:
            action = str(step.get("action") or "").strip()
            if not action:
                continue
            compiled_use = self._step_use(step)
            compiled_params = self._compiled_step_params_from_trace_step(step)
            action_name = str(compiled_params.get("action_name") or compiled_params.get("toolbar_action_name") or "").strip().lower()
            if compiled_use == "click_toolbar_action":
                toolbar_action_name = str(compiled_params.get("toolbar_action_name") or "").strip()
                if toolbar_action_name and not str(compiled_params.get("action_name") or "").strip():
                    compiled_params["action_name"] = toolbar_action_name
                    action_name = toolbar_action_name.lower()
            augmented_step = self._profile_augmented_step(app_id, step, action_name=action_name)
            robot_semantic = self._robot_semantic_for_step(
                app_id=app_id,
                step=augmented_step,
                compiled_use=compiled_use,
                params=compiled_params,
            )
            assessment = self._assessment_for_step(
                augmented_step,
                app_id=app_id,
                compiled_use=compiled_use,
                robot_semantic=robot_semantic,
            )
            if assessment.excluded:
                excluded_steps += 1
                compile_issues.append(f"{step.get('stepId') or action}: {assessment.reasons[0] if assessment.reasons else '步骤已排除'}")
                continue
            script_steps.append(
                RPAScriptStep(
                    step_id=str(step.get("stepId") or action),
                    use=compiled_use,
                    intent=str(step.get("intent") or action),
                    params=compiled_params,
                    target=dict(augmented_step.get("target") or {}),
                    verification=dict(augmented_step.get("verification") or {}),
                    recovery=dict(augmented_step.get("recovery") or {}),
                    risk=dict(augmented_step.get("risk") or {}),
                    timing=dict(augmented_step.get("timing") or {}),
                    artifacts=[dict(item) for item in list(augmented_step.get("artifacts") or []) if isinstance(item, dict)],
                    approval=self._approval_for_step(augmented_step, assessment),
                    assessment=assessment,
                    robot=robot_semantic,
                    metadata={
                        **(dict(step.get("metadata") or {}) if isinstance(step.get("metadata"), dict) else {}),
                        "executionRoute": dict((dict(step.get("metadata") or {}) if isinstance(step.get("metadata"), dict) else {}).get("executionRoute") or {}),
                        "traceStepId": step.get("stepId"),
                        "traceIndex": step.get("index"),
                        "traceSchemaVersion": trace_schema_version,
                        "tracePhase": step.get("phase") or "action",
                        "traceSignals": self._normalize_trace_signals(step),
                        "status": step.get("metadata", {}).get("status") if isinstance(step.get("metadata"), dict) else None,
                        "admissionStatus": assessment.status,
                        "profileAugmented": bool((assessment.signals or {}).get("profileAugmented")),
                        "decisionScope": (assessment.signals or {}).get("decisionScope"),
                        "decisionReasonGroup": (assessment.signals or {}).get("decisionReasonGroup"),
                        "decisionSignals": dict((assessment.signals or {}).get("decisionSignals") or {}),
                    },
                )
            )
        if not script_steps:
            raise ValueError("trace 中没有可编译步骤。")
        script_assessment = self._assessment_for_script(
            script_steps,
            excluded_steps=excluded_steps,
            script_id=script_id,
            fingerprint=fingerprint,
        )
        if script_assessment.status == "compile_blocked":
            compile_issues.append("script: trust model 判定为 compile_blocked，建议继续由 ComputerUse 探索而不是直接运行 RPA。")
        script = RPAScript(
            script_id=script_id,
            name=self._script_name(app_id=app_id, goal=goal),
            app_id=app_id,
            goal=goal,
            variables=self._compile_variables(steps),
            steps=script_steps,
            source={
                "type": "computer_use_trace_merge" if bool(dict(trace.get("metadata") or {}).get("merged")) else "computer_use_trace",
                "traceRunId": trace.get("runId"),
                "traceRunIds": list(dict(trace.get("metadata") or {}).get("sourceRunIds") or [trace.get("runId")]),
                "traceSessionId": trace.get("sessionId"),
                "traceSessionIds": list(dict(trace.get("metadata") or {}).get("sourceSessionIds") or [trace.get("sessionId")]),
                "traceVersion": trace.get("version"),
                "repairTraceRunIds": list(trace_metadata.get("repairTraceRunIds") or []),
                "repairTraceSessionIds": list(trace_metadata.get("repairTraceSessionIds") or []),
                "compiledAt": utc_now_iso(),
            },
            metadata={
                "traceSchemaVersion": trace_schema_version,
                "stepCount": len(script_steps),
                "sourceStepCount": len(steps),
                "sourceTraceCount": int(dict(trace.get("metadata") or {}).get("sourceTraceCount") or 1),
                "runtimeKind": trace.get("runtimeKind"),
                "traceUpdatedAt": trace.get("updatedAt"),
                "bindingSummary": trace_summaries["bindingSummary"],
                "preflightSummary": trace_summaries["preflightSummary"],
                "recoverySummary": trace_summaries["recoverySummary"],
                "executionSummary": trace_summaries["executionSummary"],
                "mergeMajorityThreshold": dict(trace.get("metadata") or {}).get("mergeMajorityThreshold"),
                "droppedOptionalSteps": list(dict(trace.get("metadata") or {}).get("droppedOptionalSteps") or []),
                "compileIssues": compile_issues,
                "trustModelVersion": TRUST_MODEL_VERSION,
                "fingerprint": fingerprint,
                "decisionScope": (script_assessment.signals or {}).get("decisionScope"),
                "decisionReasonGroup": (script_assessment.signals or {}).get("decisionReasonGroup"),
                "decisionSignals": dict((script_assessment.signals or {}).get("decisionSignals") or {}),
            },
            assessment=script_assessment,
            robot=self._robot_options(app_id=app_id, goal=goal, trace=trace, steps=script_steps, script_assessment=script_assessment),
        )
        return script

    def compile_run_to_draft(self, run_id: str, *, save: bool = True) -> Dict[str, Any]:
        trace = self.trace_store.get_trace(run_id)
        if not trace:
            raise ValueError(f"未找到 run_id={run_id} 对应的 computer use trace。")
        script = self.compile_trace(trace)
        payload = script.as_dict()
        if save:
            payload = self.script_store.save_draft(payload)
            payload = self.sync_template_for_script(payload, save=True)
        return payload

    def compile_runs_to_draft(self, run_ids: List[str], *, save: bool = True) -> Dict[str, Any]:
        normalized_run_ids = [str(item or "").strip() for item in run_ids if str(item or "").strip()]
        if not normalized_run_ids:
            raise ValueError("至少需要一个 ComputerUse run_id。")
        traces = self.trace_store.get_traces(normalized_run_ids)
        if not traces:
            raise ValueError("未找到任何可用的 ComputerUse trace。")
        missing = [run_id for run_id in normalized_run_ids if all(str(trace.get("runId") or "").strip() != run_id for trace in traces)]
        if missing:
            raise ValueError(f"以下 run_id 未找到对应 trace: {', '.join(missing)}")
        merged_trace = self._merge_traces(traces)
        script = self.compile_trace(merged_trace)
        payload = script.as_dict()
        if save:
            payload = self.script_store.save_draft(payload)
            payload = self.sync_template_for_script(payload, save=True)
        return payload

    def build_template_candidate(self, *, script_payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(script_payload, dict):
            raise ValueError("script_payload 无效。")
        steps = [dict(item) for item in list(script_payload.get("steps") or []) if isinstance(item, dict)]
        if not steps:
            raise ValueError("draft 不包含可生成模板的步骤。")

        app_id = str(script_payload.get("appId") or "desktop").strip() or "desktop"
        goal = str(script_payload.get("goal") or script_payload.get("name") or "workflow").strip() or "workflow"
        source = dict(script_payload.get("source") or {})
        metadata = dict(script_payload.get("metadata") or {})
        assessment_payload = dict(script_payload.get("assessment") or {})
        profile_binding = self._template_profile_binding(app_id)
        trace_run_ids = [
            str(item).strip()
            for item in list(source.get("traceRunIds") or [])
            if str(item).strip()
        ]
        singular_trace_run_id = str(source.get("traceRunId") or "").strip()
        if singular_trace_run_id and singular_trace_run_id not in trace_run_ids:
            trace_run_ids.append(singular_trace_run_id)
        trace_session_ids = [
            str(item).strip()
            for item in list(source.get("traceSessionIds") or [])
            if str(item).strip()
        ]
        singular_trace_session_id = str(source.get("traceSessionId") or "").strip()
        if singular_trace_session_id and singular_trace_session_id not in trace_session_ids:
            trace_session_ids.append(singular_trace_session_id)
        repair_trace_run_ids = [
            str(item).strip()
            for item in list(source.get("repairTraceRunIds") or [])
            if str(item).strip()
        ]
        repair_trace_session_ids = [
            str(item).strip()
            for item in list(source.get("repairTraceSessionIds") or [])
            if str(item).strip()
        ]
        source_trace_ids = list(dict.fromkeys(trace_run_ids + repair_trace_run_ids))
        source_session_ids = list(dict.fromkeys(trace_session_ids + repair_trace_session_ids))
        template_fingerprint = self._fingerprint_from_rpa_steps(app_id=app_id, steps=steps)
        template_id = self._template_id(app_id=app_id, goal=goal, fingerprint=template_fingerprint)
        timing_signal_summary = self._timing_signal_summary_for_steps(steps)
        auto_keys: List[str] = []
        target_strategy_keys: List[str] = []
        target_strategy_profiles: List[Dict[str, Any]] = []
        clipboard_payload_modes: List[str] = []
        clipboard_payload_examples: List[Dict[str, Any]] = []
        attachment_capabilities: List[str] = []
        merge_examples: Dict[str, Any] = {}
        visual_semantic_roles: List[str] = []
        visual_locator_providers: List[str] = []
        visual_locator_backed_step_count = 0
        verified_visual_locator_step_count = 0
        visual_observation_reason_codes: List[str] = []
        visual_judge_step_count = 0
        visual_judge_selected_step_count = 0
        ambient_observation_backed_steps = 0
        window_binding_verified_steps = 0
        dialog_aware_steps = 0
        focus_aware_steps = 0
        blocking_aware_steps = 0
        transition_aware_steps = 0
        page_identities: List[str] = []
        affordances: List[str] = []
        blocker_states: List[str] = []
        dialog_confidence_levels: List[str] = []
        transition_states: List[str] = []
        notification_sensing_available = False
        sound_sensing_available = False
        notification_sensing_requested = False
        sound_sensing_requested = False
        notification_signal_providers: List[str] = []
        sound_signal_providers: List[str] = []
        notification_sensing_modes: List[str] = []
        sound_sensing_modes: List[str] = []
        notification_observed_steps = 0
        sound_observed_steps = 0
        max_notification_candidate_count = 0
        max_sound_active_session_count = 0

        def _normalized_signal_values(*values: Any) -> List[str]:
            normalized: List[str] = []
            for value in values:
                if isinstance(value, (list, tuple, set)):
                    for item in value:
                        if item in (None, ""):
                            continue
                        text = str(item).strip()
                        if text:
                            normalized.append(text)
                    continue
                if value in (None, ""):
                    continue
                text = str(value).strip()
                if text:
                    normalized.append(text)
            return normalized

        for step in steps:
            step_metadata = dict(step.get("metadata") or {})
            step_params = dict(step.get("params") or {})
            for key in list(step_metadata.get("autoDiscoveredVariableKeys") or []):
                normalized_key = str(key or "").strip()
                if normalized_key and normalized_key not in auto_keys:
                    auto_keys.append(normalized_key)
            if step_metadata.get("mergeExamples"):
                merge_examples[str(step.get("stepId") or step_metadata.get("traceStepId") or len(merge_examples) + 1)] = dict(step_metadata.get("mergeExamples") or {})
            target_strategy = dict(step_metadata.get("targetStrategyApplied") or {})
            if target_strategy:
                selector_key = str(target_strategy.get("selectorKey") or "").strip()
                if selector_key and selector_key not in target_strategy_keys:
                    target_strategy_keys.append(selector_key)
                profile = {
                    "selectorKey": selector_key or None,
                    "targetText": str(target_strategy.get("targetText") or "").strip() or None,
                    "strategy": dict(target_strategy.get("strategy") or {}),
                }
                if profile not in target_strategy_profiles:
                    target_strategy_profiles.append(profile)
            clipboard_payload = dict(step_metadata.get("clipboardPayload") or {})
            clipboard_mode = str(clipboard_payload.get("mode") or "").strip()
            if clipboard_mode and clipboard_mode not in clipboard_payload_modes:
                clipboard_payload_modes.append(clipboard_mode)
            if clipboard_payload:
                example = {
                    "mode": clipboard_mode or None,
                    "hasPayload": bool(clipboard_payload.get("has_payload")),
                    "hasText": clipboard_payload.get("text") not in (None, ""),
                    "fileCount": len(list(clipboard_payload.get("file_paths") or [])),
                }
                if example not in clipboard_payload_examples:
                    clipboard_payload_examples.append(example)
            if (
                step_params.get("file_path") not in (None, "")
                or list(step_params.get("file_paths") or [])
                or list(step_params.get("attachment_paths") or [])
            ) and "clipboard_files" not in attachment_capabilities:
                attachment_capabilities.append("clipboard_files")
            visual_locator = dict(step_metadata.get("visualLocator") or {})
            evidence_summary = dict(step_metadata.get("evidenceSummary") or {})
            visual_decision = dict(step_metadata.get("visualDecision") or evidence_summary.get("visualDecision") or {})
            visual_signal_summary = dict(
                step_metadata.get("visualSignalSummary")
                or evidence_summary.get("visualSignalSummary")
                or {}
            )
            step_timing_signal_summary = dict(
                step_metadata.get("timingSignalSummary")
                or evidence_summary.get("timingSignalSummary")
                or {}
            )
            environment_signal_summary = dict(
                step_metadata.get("environmentSignalSummary")
                or evidence_summary.get("environmentSignalSummary")
                or {}
            )
            visual_locator_backed = bool(
                visual_signal_summary.get("visualLocatorBacked")
                or visual_locator
                or evidence_summary.get("visualLocator")
                or evidence_summary.get("postActionVisualLocator")
                or evidence_summary.get("startVisualLocator")
                or evidence_summary.get("endVisualLocator")
            )
            if visual_locator_backed:
                visual_locator_backed_step_count += 1
                verification_payload = dict(step.get("verification") or {})
                verification_level = str(verification_payload.get("level") or "").strip().lower()
                if (
                    int(visual_signal_summary.get("verifiedVisualLocatorSteps") or 0) > 0
                    or verification_level == "verified"
                ):
                    verified_visual_locator_step_count += 1
            visual_observation = dict(
                step_metadata.get("visualObservation")
                or evidence_summary.get("visualObservation")
                or visual_locator.get("visualObservation")
                or {}
            )
            visual_judge = dict(
                step_metadata.get("visualJudge")
                or evidence_summary.get("visualJudge")
                or visual_locator.get("visualJudge")
                or {}
            )
            semantic_role = str(
                visual_decision.get("role")
                or visual_observation.get("role")
                or visual_locator.get("semanticRole")
                or next(
                    (
                        str(item).strip()
                        for item in list(visual_signal_summary.get("visualSemanticRoles") or [])
                        if str(item).strip()
                    ),
                    "",
                )
                or ""
            ).strip()
            if semantic_role and semantic_role not in visual_semantic_roles:
                visual_semantic_roles.append(semantic_role)
            for provider in [
                str(item).strip()
                for item in list(
                    visual_signal_summary.get("visualLocatorProviders")
                    or [
                        visual_locator.get("providerId"),
                    ]
                )
                if str(item).strip()
            ]:
                if provider not in visual_locator_providers:
                    visual_locator_providers.append(provider)
            for code in [str(item).strip() for item in list(visual_observation.get("reasonCodes") or []) if str(item).strip()]:
                if code not in visual_observation_reason_codes:
                    visual_observation_reason_codes.append(code)
            judge_decision = str(
                visual_decision.get("judgeDecision")
                or visual_judge.get("decision")
                or visual_judge.get("status")
                or ""
            ).strip().lower()
            judge_backed = bool(judge_decision) or bool(visual_signal_summary.get("visualJudgeBacked")) or int(
                visual_signal_summary.get("visualJudgeSteps") or 0
            ) > 0
            judge_selected = judge_decision == "candidate" or int(
                visual_signal_summary.get("visualJudgeSelectedSteps") or 0
            ) > 0
            if judge_backed:
                visual_judge_step_count += 1
                if judge_selected:
                    visual_judge_selected_step_count += 1
            if bool(environment_signal_summary.get("observationDriven")) or bool(
                environment_signal_summary.get("desktopEnvironmentAware")
            ):
                ambient_observation_backed_steps += 1
            if bool(environment_signal_summary.get("windowBindingVerified")):
                window_binding_verified_steps += 1
            if bool(environment_signal_summary.get("dialogDetected")):
                dialog_aware_steps += 1
            if bool(environment_signal_summary.get("focusKnown")) or int(
                environment_signal_summary.get("focusAwareSteps") or 0
            ) > 0:
                focus_aware_steps += 1
            blocker_values = _normalized_signal_values(
                environment_signal_summary.get("blockerState"),
                environment_signal_summary.get("blockerStates") or [],
            )
            for blocker_state in blocker_values:
                if blocker_state not in blocker_states:
                    blocker_states.append(blocker_state)
                if blocker_state.lower() not in {"none", "ready"}:
                    blocking_aware_steps += 1
            transition_values = _normalized_signal_values(
                environment_signal_summary.get("transitionState"),
                environment_signal_summary.get("transitionStates") or [],
            )
            for transition_state in transition_values:
                if transition_state not in transition_states:
                    transition_states.append(transition_state)
                transition_aware_steps += 1
            page_identity_values = _normalized_signal_values(
                environment_signal_summary.get("pageIdentity"),
                environment_signal_summary.get("pageIdentities") or [],
            )
            for page_identity in page_identity_values:
                if page_identity not in page_identities:
                    page_identities.append(page_identity)
            for affordance in [str(item).strip() for item in list(environment_signal_summary.get("affordances") or []) if str(item).strip()]:
                if affordance not in affordances:
                    affordances.append(affordance)
            dialog_confidence_values = _normalized_signal_values(
                environment_signal_summary.get("dialogConfidenceLevel"),
                environment_signal_summary.get("dialogConfidenceLevels") or [],
            )
            for dialog_confidence_level in dialog_confidence_values:
                if dialog_confidence_level not in dialog_confidence_levels:
                    dialog_confidence_levels.append(dialog_confidence_level)
            notification_sensing_available = notification_sensing_available or bool(
                environment_signal_summary.get("notificationSensingAvailable")
            )
            sound_sensing_available = sound_sensing_available or bool(
                environment_signal_summary.get("soundSensingAvailable")
            )
            notification_sensing_requested = notification_sensing_requested or bool(
                environment_signal_summary.get("notificationSensingRequested")
            )
            sound_sensing_requested = sound_sensing_requested or bool(
                environment_signal_summary.get("soundSensingRequested")
            )
            if bool(environment_signal_summary.get("notificationObserved")) or int(
                environment_signal_summary.get("notificationObservedSteps") or 0
            ) > 0:
                notification_observed_steps += 1
            if bool(environment_signal_summary.get("soundObserved")) or int(
                environment_signal_summary.get("soundObservedSteps") or 0
            ) > 0:
                sound_observed_steps += 1
            max_notification_candidate_count = max(
                max_notification_candidate_count,
                int(
                    environment_signal_summary.get("notificationCandidateCount")
                    or environment_signal_summary.get("maxNotificationCandidateCount")
                    or 0
                ),
            )
            max_sound_active_session_count = max(
                max_sound_active_session_count,
                int(
                    environment_signal_summary.get("soundActiveSessionCount")
                    or environment_signal_summary.get("maxSoundActiveSessionCount")
                    or 0
                ),
            )
            for provider in _normalized_signal_values(environment_signal_summary.get("notificationSignalProviders") or []):
                if provider not in notification_signal_providers:
                    notification_signal_providers.append(provider)
            for provider in _normalized_signal_values(environment_signal_summary.get("soundSignalProviders") or []):
                if provider not in sound_signal_providers:
                    sound_signal_providers.append(provider)
            for mode in _normalized_signal_values(environment_signal_summary.get("notificationSensingMode")):
                if mode not in notification_sensing_modes:
                    notification_sensing_modes.append(mode)
            for mode in _normalized_signal_values(environment_signal_summary.get("soundSensingMode")):
                if mode not in sound_sensing_modes:
                    sound_sensing_modes.append(mode)

        template_payload = RPATemplateCandidate(
            template_id=template_id,
            name=self._template_name(app_id=app_id, goal=goal),
            app_id=app_id,
            goal=goal,
            variables=self._merge_script_variables(list(script_payload.get("variables") or []), []),
            steps=[self._rpa_step_from_dict(item) for item in steps],
            profile=profile_binding,
            source={
                "type": "rpa_template_candidate",
                "draftId": script_payload.get("id"),
                "draftVersion": script_payload.get("version"),
                "traceRunIds": source_trace_ids,
                "traceSessionIds": source_session_ids,
                "repairTraceRunIds": repair_trace_run_ids,
                "repairTraceSessionIds": repair_trace_session_ids,
                "sourceType": source.get("type"),
                "compiledAt": utc_now_iso(),
            },
            metadata={
                "fingerprint": template_fingerprint,
                "templateStatus": "candidate",
                "profileLinked": profile_binding is not None,
                "sourceDraftFingerprint": metadata.get("fingerprint"),
                "traceSchemaVersion": int(metadata.get("traceSchemaVersion") or 1),
                "sourceTraceCount": len(source_trace_ids) or int(metadata.get("sourceTraceCount") or 1),
                "repairTraceRunIds": repair_trace_run_ids,
                "repairTraceSessionIds": repair_trace_session_ids,
                "localRepairCount": int(metadata.get("localRepairCount") or 0),
                "compileIssues": list(metadata.get("compileIssues") or []),
                "bindingSummary": dict(metadata.get("bindingSummary") or {}),
                "preflightSummary": dict(metadata.get("preflightSummary") or {}),
                "recoverySummary": dict(metadata.get("recoverySummary") or {}),
                "decisionScope": metadata.get("decisionScope"),
                "decisionReasonGroup": metadata.get("decisionReasonGroup"),
                "decisionSignals": dict(metadata.get("decisionSignals") or {}),
                "executionSummary": dict(metadata.get("executionSummary") or {}),
                "autoDiscoveredVariableKeys": auto_keys,
                "targetStrategyKeys": target_strategy_keys,
                "targetStrategyProfiles": target_strategy_profiles,
                "clipboardPayloadModes": clipboard_payload_modes,
                "clipboardPayloadExamples": clipboard_payload_examples,
                "attachmentCapabilities": attachment_capabilities,
                "mergeExamples": merge_examples,
                "visualSemanticRoles": visual_semantic_roles,
                "visualLocatorProviders": visual_locator_providers,
                "visualLocatorBackedSteps": visual_locator_backed_step_count,
                "verifiedVisualLocatorSteps": verified_visual_locator_step_count,
                "visualObservationReasonCodes": visual_observation_reason_codes,
                "visualJudgeStepCount": visual_judge_step_count,
                "visualJudgeSelectedStepCount": visual_judge_selected_step_count,
                "ambientObservationBackedSteps": ambient_observation_backed_steps,
                "windowBindingVerifiedSteps": window_binding_verified_steps,
                "dialogAwareSteps": dialog_aware_steps,
                "focusAwareSteps": focus_aware_steps,
                "blockingAwareSteps": blocking_aware_steps,
                "transitionAwareSteps": transition_aware_steps,
                "pageIdentities": page_identities,
                "affordances": affordances,
                "blockerStates": blocker_states,
                "dialogConfidenceLevels": dialog_confidence_levels,
                "notificationSensingRequested": notification_sensing_requested,
                "notificationSensingAvailable": notification_sensing_available,
                "notificationSignalProviders": notification_signal_providers,
                "notificationSensingModes": notification_sensing_modes,
                "notificationObservedSteps": notification_observed_steps,
                "maxNotificationCandidateCount": max_notification_candidate_count,
                "soundSensingRequested": sound_sensing_requested,
                "soundSensingAvailable": sound_sensing_available,
                "soundSignalProviders": sound_signal_providers,
                "soundSensingModes": sound_sensing_modes,
                "soundObservedSteps": sound_observed_steps,
                "maxSoundActiveSessionCount": max_sound_active_session_count,
                "waitSensitiveSteps": int(timing_signal_summary.get("waitSensitiveSteps") or 0),
                "loadingSensitiveSteps": int(timing_signal_summary.get("loadingSensitiveSteps") or 0),
                "transitionStates": list(timing_signal_summary.get("transitionStates") or []),
                "stabilityWaitObservedSteps": int(timing_signal_summary.get("stabilityWaitObservedSteps") or 0),
                "stabilityWaitTimeoutSteps": int(timing_signal_summary.get("stabilityWaitTimeoutSteps") or 0),
                "stabilityWaitStatuses": list(timing_signal_summary.get("stabilityWaitStatuses") or []),
                "budgetExceededSteps": int(timing_signal_summary.get("budgetExceededSteps") or 0),
                "timeBudgetExceededSteps": int(timing_signal_summary.get("timeBudgetExceededSteps") or 0),
                "maxSettleBudgetMs": int(timing_signal_summary.get("maxSettleBudgetMs") or 0),
                "maxElapsedMs": int(timing_signal_summary.get("maxElapsedMs") or 0),
                "maxPostActionSettleTimeoutMs": int(timing_signal_summary.get("maxPostActionSettleTimeoutMs") or 0),
                "maxPostActionSettlePollMs": int(timing_signal_summary.get("maxPostActionSettlePollMs") or 0),
                "maxPostActionStableRounds": int(timing_signal_summary.get("maxPostActionStableRounds") or 0),
            },
            assessment=self._script_assessment_from_dict(assessment_payload) if assessment_payload else None,
            robot=self._robot_options(
                app_id=app_id,
                goal=goal,
                trace={
                    "runId": source_trace_ids[0] if source_trace_ids else source.get("traceRunId"),
                    "sessionId": source_session_ids[0] if source_session_ids else source.get("traceSessionId"),
                    "source": source.get("type"),
                },
                steps=[self._rpa_step_from_dict(item) for item in steps],
                script_assessment=self._script_assessment_from_dict(assessment_payload) if assessment_payload else None,
            ),
        ).as_dict()
        existing = self.script_store.get_template(template_id)
        if existing and existing.get("createdAt"):
            template_payload["createdAt"] = existing.get("createdAt")
            template_payload["metadata"]["revision"] = int((existing.get("metadata") or {}).get("revision") or 0) + 1
        else:
            template_payload["metadata"]["revision"] = 1
        promotion_gate = evaluate_promotion_gate(
            script_payload=script_payload,
            template_payload=template_payload,
        )
        template_payload["promotionGate"] = promotion_gate
        template_payload["metadata"]["visualSignalSummary"] = draft_visual_signal_summary(
            promotion_gate,
            metadata=dict(template_payload.get("metadata") or {}),
        )
        template_payload["metadata"]["timingSignalSummary"] = draft_timing_signal_summary(
            promotion_gate,
            metadata=dict(template_payload.get("metadata") or {}),
        )
        template_payload["metadata"]["environmentSignalSummary"] = draft_environment_signal_summary(
            promotion_gate,
            metadata=dict(template_payload.get("metadata") or {}),
        )
        template_payload["metadata"]["executionRouteSummary"] = dict(
            (template_payload.get("metadata") or {}).get("executionSummary") or {}
        )
        template_payload["metadata"]["promotionGateVersion"] = promotion_gate.get("version")
        governance = self._template_governance(template_payload=template_payload, script_payload=script_payload)
        template_payload["governance"] = governance
        template_payload["metadata"]["templateTrustPolicyVersion"] = TEMPLATE_TRUST_POLICY_VERSION
        return template_payload

    def sync_template_for_script(self, script_payload: Dict[str, Any], *, save: bool = True) -> Dict[str, Any]:
        template_payload = self.build_template_candidate(script_payload=script_payload)
        if save:
            template_payload = self.script_store.save_template(template_payload)
        next_payload = dict(script_payload)
        next_source = dict(next_payload.get("source") or {})
        next_source["templateId"] = template_payload.get("id")
        next_source["templateFingerprint"] = dict(template_payload.get("metadata") or {}).get("fingerprint")
        next_source["templateUpdatedAt"] = template_payload.get("updatedAt")
        next_source["templateStatus"] = dict(template_payload.get("governance") or {}).get("templateStatus")
        next_source["templateStage"] = dict(template_payload.get("governance") or {}).get("stage")
        next_payload["source"] = next_source
        next_metadata = dict(next_payload.get("metadata") or {})
        next_metadata["templateCandidateId"] = template_payload.get("id")
        next_metadata["templateCandidateRevision"] = dict(template_payload.get("metadata") or {}).get("revision")
        next_metadata["templateSourceTraceCount"] = dict(template_payload.get("metadata") or {}).get("sourceTraceCount")
        next_metadata["templateTargetStrategyKeys"] = list(dict(template_payload.get("metadata") or {}).get("targetStrategyKeys") or [])
        next_metadata["templateTargetStrategyProfiles"] = list(dict(template_payload.get("metadata") or {}).get("targetStrategyProfiles") or [])
        next_metadata["templateClipboardPayloadModes"] = list(dict(template_payload.get("metadata") or {}).get("clipboardPayloadModes") or [])
        next_metadata["templateClipboardPayloadExamples"] = list(dict(template_payload.get("metadata") or {}).get("clipboardPayloadExamples") or [])
        next_metadata["templateAttachmentCapabilities"] = list(dict(template_payload.get("metadata") or {}).get("attachmentCapabilities") or [])
        next_metadata["templateVisualSemanticRoles"] = list(dict(template_payload.get("metadata") or {}).get("visualSemanticRoles") or [])
        next_metadata["templateVisualObservationReasonCodes"] = list(dict(template_payload.get("metadata") or {}).get("visualObservationReasonCodes") or [])
        next_metadata["templateVisualJudgeStepCount"] = int(dict(template_payload.get("metadata") or {}).get("visualJudgeStepCount") or 0)
        next_metadata["templateVisualJudgeSelectedStepCount"] = int(dict(template_payload.get("metadata") or {}).get("visualJudgeSelectedStepCount") or 0)
        template_governance = draft_template_governance_summary(dict(template_payload.get("governance") or {}))
        template_promotion_gate = draft_promotion_gate_summary(dict(template_payload.get("promotionGate") or {}))
        next_metadata["templateVisualSignalSummary"] = draft_visual_signal_summary(
            template_promotion_gate,
            metadata=dict(template_payload.get("metadata") or {}),
        )
        next_metadata["templateTimingSignalSummary"] = draft_timing_signal_summary(
            template_promotion_gate,
            metadata=dict(template_payload.get("metadata") or {}),
        )
        next_metadata["templateEnvironmentSignalSummary"] = draft_environment_signal_summary(
            template_promotion_gate,
            metadata=dict(template_payload.get("metadata") or {}),
        )
        next_metadata["templateExecutionRouteSummary"] = dict(
            (template_payload.get("metadata") or {}).get("executionSummary") or {}
        )
        next_metadata["templateGovernance"] = template_governance
        next_metadata["templateGovernanceStage"] = template_governance.get("stage")
        next_metadata["templateRecommendedDecision"] = template_governance.get("recommendedDecision")
        next_metadata["templateTrustConfidence"] = template_governance.get("confidence")
        next_metadata["templatePreferExecution"] = bool(template_governance.get("preferTemplateExecution"))
        next_metadata["templateRolloutMode"] = template_governance.get("rolloutMode")
        next_metadata["templateTrustPolicyVersion"] = template_governance.get("version")
        next_metadata["templatePromotionGate"] = template_promotion_gate
        next_metadata["templatePromotionEligible"] = bool(template_promotion_gate.get("eligible"))
        next_metadata["templatePromotionGateVersion"] = template_promotion_gate.get("version")
        next_metadata["templatePromotionGateStatus"] = template_promotion_gate.get("status")
        next_metadata["templatePromotionGateBlocked"] = bool(template_promotion_gate.get("blockedPromotion"))
        next_metadata["templatePromotionGateReasons"] = list(template_promotion_gate.get("reasons") or [])[:5]
        next_metadata["templatePromotionGateSignals"] = dict(template_promotion_gate.get("signals") or {})
        next_payload["metadata"] = next_metadata
        if save:
            next_payload = self.script_store.save_draft(next_payload)
        return next_payload

    def _fingerprint_from_rpa_steps(self, *, app_id: str, steps: List[Dict[str, Any]]) -> str:
        parts: List[str] = [self._slug(app_id, "desktop")]
        for step in steps:
            if not isinstance(step, dict):
                continue
            use = str(step.get("use") or "").strip().lower()
            params = dict(step.get("params") or {})
            target = dict(step.get("target") or {})
            selector = dict(target.get("selector") or {})
            action_name = str(params.get("action_name") or params.get("toolbar_action_name") or "").strip().lower()
            selector_key = str(params.get("selector_key") or selector.get("selectorKey") or "").strip().lower()
            control_type = str(selector.get("controlType") or "").strip().lower()
            parts.append(f"{use}:{action_name}:{selector_key}:{control_type}")
        digest = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:12]
        return f"fp.{self._slug(app_id, 'desktop')}.{digest}"

    def _timing_signal_summary_for_steps(self, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        transition_states: List[str] = []
        stability_wait_statuses: List[str] = []
        wait_sensitive_steps = 0
        loading_sensitive_steps = 0
        stability_wait_observed_steps = 0
        stability_wait_timeout_steps = 0
        budget_exceeded_steps = 0
        time_budget_exceeded_steps = 0
        max_settle_budget_ms = 0
        max_elapsed_ms = 0
        max_post_action_settle_timeout_ms = 0
        max_post_action_settle_poll_ms = 0
        max_post_action_stable_rounds = 0

        for step in steps:
            if not isinstance(step, dict):
                continue
            metadata = dict(step.get("metadata") or {})
            params = dict(step.get("params") or {})
            timing = dict(step.get("timing") or {})
            risk_details = dict(dict(step.get("risk") or {}).get("details") or {})
            evidence_summary = dict(metadata.get("evidenceSummary") or {})
            timing_signal_summary = dict(
                metadata.get("timingSignalSummary")
                or evidence_summary.get("timingSignalSummary")
                or {}
            )
            scene = dict(metadata.get("scene") or {})
            budget = dict(metadata.get("budget") or {})
            stability_wait = dict(
                metadata.get("stabilityWait")
                or evidence_summary.get("stabilityWait")
                or timing.get("stabilityWait")
                or {}
            )

            transition_state = str(scene.get("transitionState") or "").strip().lower()
            if not transition_state:
                transition_state = str(
                    next(
                        (
                            item
                            for item in list(timing_signal_summary.get("transitionStates") or [])
                            if str(item).strip()
                        ),
                        "",
                    )
                ).strip().lower()
            if transition_state:
                transition_states.append(transition_state)
            if transition_state in {"waiting", "loading", "waiting_for_transition"}:
                loading_sensitive_steps += max(1, int(timing_signal_summary.get("loadingSensitiveSteps") or 0))
            elif int(timing_signal_summary.get("loadingSensitiveSteps") or 0) > 0:
                loading_sensitive_steps += int(timing_signal_summary.get("loadingSensitiveSteps") or 0)

            settle_timeout_ms = int(
                timing_signal_summary.get("maxPostActionSettleTimeoutMs")
                or params.get("post_action_settle_timeout_ms")
                or risk_details.get("postActionSettleTimeoutMs")
                or timing.get("postActionSettleTimeoutMs")
                or 0
            )
            settle_poll_ms = int(
                timing_signal_summary.get("maxPostActionSettlePollMs")
                or params.get("post_action_settle_poll_ms")
                or risk_details.get("postActionSettlePollMs")
                or timing.get("postActionSettlePollMs")
                or 0
            )
            stable_rounds = int(
                timing_signal_summary.get("maxPostActionStableRounds")
                or params.get("post_action_stable_rounds")
                or risk_details.get("postActionStableRounds")
                or timing.get("postActionStableRounds")
                or 0
            )
            if settle_timeout_ms > 0 or settle_poll_ms > 0 or stable_rounds > 0:
                wait_sensitive_steps += max(1, int(timing_signal_summary.get("waitSensitiveSteps") or 0))
            elif int(timing_signal_summary.get("waitSensitiveSteps") or 0) > 0:
                wait_sensitive_steps += int(timing_signal_summary.get("waitSensitiveSteps") or 0)
            max_post_action_settle_timeout_ms = max(max_post_action_settle_timeout_ms, settle_timeout_ms)
            max_post_action_settle_poll_ms = max(max_post_action_settle_poll_ms, settle_poll_ms)
            max_post_action_stable_rounds = max(max_post_action_stable_rounds, stable_rounds)

            settle_budget_ms = int(
                timing_signal_summary.get("maxSettleBudgetMs")
                or budget.get("settleBudgetMs")
                or 0
            )
            elapsed_ms = int(
                timing_signal_summary.get("maxElapsedMs")
                or budget.get("elapsedMs")
                or 0
            )
            max_settle_budget_ms = max(max_settle_budget_ms, settle_budget_ms)
            max_elapsed_ms = max(max_elapsed_ms, elapsed_ms)

            if budget.get("withinBudget") is False or int(timing_signal_summary.get("budgetExceededSteps") or 0) > 0:
                budget_exceeded_steps += max(1, int(timing_signal_summary.get("budgetExceededSteps") or 0))
            exceeded = {str(item).strip().lower() for item in list(budget.get("exceeded") or []) if str(item).strip()}
            if "time" in exceeded or int(timing_signal_summary.get("timeBudgetExceededSteps") or 0) > 0:
                time_budget_exceeded_steps += max(1, int(timing_signal_summary.get("timeBudgetExceededSteps") or 0))

            if stability_wait:
                stability_wait_observed_steps += max(1, int(timing_signal_summary.get("stabilityWaitObservedSteps") or 0))
                status = str(stability_wait.get("status") or "").strip().lower()
                if status:
                    stability_wait_statuses.append(status)
                if status == "timeout":
                    stability_wait_timeout_steps += max(1, int(timing_signal_summary.get("stabilityWaitTimeoutSteps") or 0))
            elif int(timing_signal_summary.get("stabilityWaitObservedSteps") or 0) > 0:
                stability_wait_observed_steps += int(timing_signal_summary.get("stabilityWaitObservedSteps") or 0)
                for item in [str(entry).strip().lower() for entry in list(timing_signal_summary.get("stabilityWaitStatuses") or []) if str(entry).strip()]:
                    stability_wait_statuses.append(item)
                stability_wait_timeout_steps += int(timing_signal_summary.get("stabilityWaitTimeoutSteps") or 0)

        return {
            "waitSensitiveSteps": wait_sensitive_steps,
            "loadingSensitiveSteps": loading_sensitive_steps,
            "transitionStates": list(dict.fromkeys(transition_states)),
            "stabilityWaitObservedSteps": stability_wait_observed_steps,
            "stabilityWaitTimeoutSteps": stability_wait_timeout_steps,
            "stabilityWaitStatuses": list(dict.fromkeys(stability_wait_statuses)),
            "budgetExceededSteps": budget_exceeded_steps,
            "timeBudgetExceededSteps": time_budget_exceeded_steps,
            "maxSettleBudgetMs": max_settle_budget_ms,
            "maxElapsedMs": max_elapsed_ms,
            "maxPostActionSettleTimeoutMs": max_post_action_settle_timeout_ms,
            "maxPostActionSettlePollMs": max_post_action_settle_poll_ms,
            "maxPostActionStableRounds": max_post_action_stable_rounds,
        }

    def repair_script_from_trace(
        self,
        *,
        script_payload: Dict[str, Any],
        trace: Dict[str, Any],
        start_index: int,
        save: bool = True,
    ) -> Dict[str, Any]:
        if not isinstance(script_payload, dict):
            raise ValueError("script_payload 无效。")
        if not isinstance(trace, dict):
            raise ValueError("trace 无效。")
        existing_steps = [dict(item) for item in list(script_payload.get("steps") or []) if isinstance(item, dict)]
        if not existing_steps:
            raise ValueError("draft 不包含可修补步骤。")

        safe_start_index = max(0, min(int(start_index or 0), len(existing_steps)))
        repaired_fragment = self.compile_trace(trace).as_dict()
        repaired_steps = [dict(item) for item in list(repaired_fragment.get("steps") or []) if isinstance(item, dict)]
        if not repaired_steps:
            raise ValueError("repair trace 未产生任何可编译步骤。")

        combined_steps = existing_steps[:safe_start_index] + repaired_steps
        combined_step_objects = [self._rpa_step_from_dict(item) for item in combined_steps]

        app_id = str(script_payload.get("appId") or repaired_fragment.get("appId") or "desktop").strip() or "desktop"
        fingerprint = self._fingerprint_from_rpa_steps(app_id=app_id, steps=combined_steps)
        excluded_steps = int(((script_payload.get("assessment") or {}).get("excludedSteps")) or 0)
        script_assessment = self._assessment_for_script(
            combined_step_objects,
            excluded_steps=excluded_steps,
            script_id=str(script_payload.get("id") or repaired_fragment.get("id") or self._script_id(app_id=app_id, goal=str(script_payload.get("goal") or repaired_fragment.get("goal") or "workflow"))),
            fingerprint=fingerprint,
        )
        merged_variables = self._merge_script_variables(
            list(script_payload.get("variables") or []),
            list(repaired_fragment.get("variables") or []),
        )

        source = dict(script_payload.get("source") or {})
        repair_trace_run_id = str(trace.get("runId") or "").strip()
        repair_trace_session_id = str(trace.get("sessionId") or "").strip()
        repair_trace_run_ids = [
            str(item).strip()
            for item in list(source.get("repairTraceRunIds") or [])
            if str(item).strip()
        ]
        repair_trace_session_ids = [
            str(item).strip()
            for item in list(source.get("repairTraceSessionIds") or [])
            if str(item).strip()
        ]
        if repair_trace_run_id and repair_trace_run_id not in repair_trace_run_ids:
            repair_trace_run_ids.append(repair_trace_run_id)
        if repair_trace_session_id and repair_trace_session_id not in repair_trace_session_ids:
            repair_trace_session_ids.append(repair_trace_session_id)
        source["repairTraceRunIds"] = repair_trace_run_ids
        source["repairTraceSessionIds"] = repair_trace_session_ids
        if repair_trace_run_id:
            source["lastRepairTraceRunId"] = repair_trace_run_id
        if repair_trace_session_id:
            source["lastRepairTraceSessionId"] = repair_trace_session_id
        source["lastRepairedAt"] = utc_now_iso()

        metadata = dict(script_payload.get("metadata") or {})
        repair_history = [dict(item) for item in list(metadata.get("repairHistory") or []) if isinstance(item, dict)]
        repair_entry = {
            "traceRunId": repair_trace_run_id or None,
            "traceSessionId": repair_trace_session_id or None,
            "startIndex": safe_start_index,
            "replacedStepCount": max(0, len(existing_steps) - safe_start_index),
            "patchedStepCount": len(repaired_steps),
            "compiledAt": utc_now_iso(),
        }
        repair_history.append(repair_entry)
        metadata["repairHistory"] = repair_history[-20:]
        metadata["lastRepair"] = repair_entry
        metadata["localRepairCount"] = int(metadata.get("localRepairCount") or 0) + 1
        metadata["stepCount"] = len(combined_steps)
        metadata["compileIssues"] = list(repaired_fragment.get("metadata", {}).get("compileIssues") or [])
        metadata["fingerprint"] = fingerprint
        metadata["traceSchemaVersion"] = int(
            repaired_fragment.get("metadata", {}).get("traceSchemaVersion")
            or metadata.get("traceSchemaVersion")
            or 1
        )
        metadata["sourceTraceCount"] = int(
            len(list(source.get("traceRunIds") or []))
            or metadata.get("sourceTraceCount")
            or repaired_fragment.get("metadata", {}).get("sourceTraceCount")
            or 1
        )
        metadata["bindingSummary"] = dict(
            repaired_fragment.get("metadata", {}).get("bindingSummary")
            or metadata.get("bindingSummary")
            or {}
        )
        metadata["preflightSummary"] = dict(
            repaired_fragment.get("metadata", {}).get("preflightSummary")
            or metadata.get("preflightSummary")
            or {}
        )
        metadata["recoverySummary"] = dict(
            repaired_fragment.get("metadata", {}).get("recoverySummary")
            or metadata.get("recoverySummary")
            or {}
        )
        metadata["decisionScope"] = (
            repaired_fragment.get("metadata", {}).get("decisionScope")
            or metadata.get("decisionScope")
        )
        metadata["decisionReasonGroup"] = (
            repaired_fragment.get("metadata", {}).get("decisionReasonGroup")
            or metadata.get("decisionReasonGroup")
        )
        metadata["decisionSignals"] = dict(
            repaired_fragment.get("metadata", {}).get("decisionSignals")
            or metadata.get("decisionSignals")
            or {}
        )

        repaired_payload = RPAScript(
            script_id=str(script_payload.get("id") or repaired_fragment.get("id")),
            name=str(script_payload.get("name") or repaired_fragment.get("name") or self._script_name(app_id=app_id, goal=str(script_payload.get("goal") or repaired_fragment.get("goal") or "workflow"))),
            version=str(script_payload.get("version") or repaired_fragment.get("version") or "0.1.0"),
            kind=str(script_payload.get("kind") or repaired_fragment.get("kind") or "rpa_script"),
            runtime=str(script_payload.get("runtime") or repaired_fragment.get("runtime") or "robot_framework"),
            app_id=app_id,
            goal=str(script_payload.get("goal") or repaired_fragment.get("goal") or ""),
            variables=merged_variables,
            steps=combined_step_objects,
            source=source,
            metadata=metadata,
            assessment=script_assessment,
            robot=self._robot_options(
                app_id=app_id,
                goal=str(script_payload.get("goal") or repaired_fragment.get("goal") or ""),
                trace=trace,
                steps=combined_step_objects,
                script_assessment=script_assessment,
            ),
        ).as_dict()
        if script_payload.get("createdAt"):
            repaired_payload["createdAt"] = script_payload.get("createdAt")
        if script_payload.get("updatedAt"):
            repaired_payload["previousUpdatedAt"] = script_payload.get("updatedAt")

        if save:
            repaired_payload = self.script_store.save_draft(repaired_payload)
            repaired_payload = self.sync_template_for_script(repaired_payload, save=True)
        return repaired_payload


rpa_trace_compiler = RPATraceCompiler()
