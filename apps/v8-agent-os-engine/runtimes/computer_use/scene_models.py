from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

from runtimes.computer_use.primitives import resolve_computer_use_primitive
from runtimes.computer_use.window_scene import (
    is_probable_dialog_window,
    normalize_window_payload,
    window_title_match_score,
)


@dataclass(slots=True)
class ComputerUseSceneAssessment:
    page_identity: str
    blocker_state: str
    transition_state: str
    affordances: List[str] = field(default_factory=list)
    confidence: str = "low"
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pageIdentity": self.page_identity,
            "blockerState": self.blocker_state,
            "transitionState": self.transition_state,
            "affordances": list(self.affordances),
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _iter_elements(observation: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(observation, dict):
        return []
    return [item for item in list(observation.get("elements") or []) if isinstance(item, dict)]


def _element_text_tokens(element: Dict[str, Any]) -> List[str]:
    metadata = dict(element.get("metadata") or {})
    candidates = [
        element.get("name"),
        element.get("automationId"),
        element.get("className"),
        metadata.get("value"),
        metadata.get("currentValue"),
        metadata.get("text"),
        metadata.get("description"),
        metadata.get("helpText"),
    ]
    return [normalized for normalized in (_normalize_text(item) for item in candidates) if normalized]


def _observation_text_tokens(observation: Dict[str, Any] | None) -> List[str]:
    payload = dict(observation or {})
    metadata = dict(payload.get("metadata") or {})
    values: List[str] = []
    for item in (
        payload.get("windowTitle"),
        payload.get("app"),
        metadata.get("className"),
        metadata.get("processName"),
    ):
        normalized = _normalize_text(item)
        if normalized:
            values.append(normalized)
    for element in _iter_elements(payload):
        values.extend(_element_text_tokens(element))
    return list(dict.fromkeys(values))


def _contains_token(tokens: Iterable[str], *needles: str) -> bool:
    normalized_needles = [_normalize_text(item) for item in needles if _normalize_text(item)]
    if not normalized_needles:
        return False
    for token in tokens:
        normalized_token = _normalize_text(token)
        if not normalized_token:
            continue
        for needle in normalized_needles:
            if needle and needle in normalized_token:
                return True
    return False


def _target_visible(observation: Dict[str, Any] | None, target_text: str | None) -> bool:
    normalized_target = _normalize_text(target_text)
    if not normalized_target:
        return False
    return _contains_token(_observation_text_tokens(observation), normalized_target)


def infer_affordances(
    *,
    observation: Dict[str, Any] | None,
    primitive_affordances: Iterable[str] | None = None,
) -> List[str]:
    affordances: List[str] = [str(item).strip() for item in list(primitive_affordances or []) if str(item).strip()]
    elements = _iter_elements(observation)
    focused_id = str((observation or {}).get("focusedElementId") or "").strip()
    has_focus_context = False
    has_clickable = False
    has_scrollable = False
    has_dialog_actions = False
    has_editable = False
    has_list_context = False
    has_drag_context = False
    for element in elements:
        role = _normalize_text(element.get("role"))
        actions = {_normalize_text(item) for item in list(element.get("actions") or []) if _normalize_text(item)}
        metadata = dict(element.get("metadata") or {})
        if element.get("elementId") == focused_id:
            has_focus_context = True
        if role in {"edit", "document", "combobox"} or bool((metadata.get("textInputCapability") or {}).get("allowed")):
            has_editable = True
        if role in {"list", "listitem", "dataitem", "tree", "treeitem", "table"}:
            has_list_context = True
        if role in {"scrollbar", "pane"} or "scroll" in actions:
            has_scrollable = True
        if actions.intersection({"click", "invoke", "select", "toggle", "press"}):
            has_clickable = True
        if role in {"button", "menuitem"}:
            has_dialog_actions = True
        if actions.intersection({"drag", "dragdrop", "move"}):
            has_drag_context = True
    if has_focus_context:
        affordances.append("focus_context")
    if has_editable:
        affordances.append("editable_context")
    if has_clickable:
        affordances.append("clickable_context")
    if has_scrollable:
        affordances.append("scrollable_context")
    if has_list_context:
        affordances.append("list_context")
    if has_dialog_actions:
        affordances.append("dialog_actions")
    if has_drag_context:
        affordances.append("drag_context")
    return list(dict.fromkeys(affordances))


def infer_page_identity(
    *,
    app_id: str | None,
    action_type: str,
    action_payload: Dict[str, Any] | None = None,
    observation: Dict[str, Any] | None = None,
    target: Dict[str, Any] | None = None,
) -> tuple[str, str, List[str]]:
    payload = dict(action_payload or {})
    observation_payload = dict(observation or {})
    target_payload = dict(target or {})
    metadata = dict(observation_payload.get("metadata") or {})
    reasons: List[str] = []
    explicit = str(
        payload.get("page_identity")
        or metadata.get("pageIdentity")
        or target_payload.get("pageIdentity")
        or ""
    ).strip()
    if explicit:
        return explicit, "high", ["动作或观察已显式提供 page identity。"]
    normalized_app = str(app_id or metadata.get("appId") or target_payload.get("appId") or "desktop").strip() or "desktop"
    selector_key = str(payload.get("selector_key") or "").strip()
    if selector_key:
        reasons.append("根据 selector_key 推断页面场景。")
        return f"{normalized_app}.{selector_key}", "medium", reasons
    focused_id = str(observation_payload.get("focusedElementId") or "").strip()
    if focused_id:
        reasons.append("根据当前焦点元素推断页面场景。")
        return f"{normalized_app}.focused", "medium", reasons
    window_title = _normalize_text(
        observation_payload.get("windowTitle")
        or payload.get("window_title")
        or target_payload.get("windowTitle")
        or target_payload.get("title")
    )
    if "search" in window_title:
        reasons.append("窗口标题包含搜索态。")
        return f"{normalized_app}.search", "low", reasons
    reasons.append("缺少更强信号，回退为动作级页面身份。")
    return f"{normalized_app}.{str(action_type or 'action').strip().lower() or 'action'}", "low", reasons


def infer_goal_state_match(
    *,
    app_id: str | None,
    action_type: str,
    action_payload: Dict[str, Any] | None = None,
    observation: Dict[str, Any] | None = None,
    target: Dict[str, Any] | None = None,
) -> Tuple[bool, List[str]]:
    payload = dict(action_payload or {})
    observation_payload = dict(observation or {})
    target_payload = dict(target or {})
    reasons: List[str] = []
    window_title = _normalize_text(
        observation_payload.get("windowTitle")
        or target_payload.get("windowTitle")
        or target_payload.get("title")
    )
    expected_window_title = _normalize_text(
        payload.get("window_title")
        or target_payload.get("windowTitle")
        or target_payload.get("title")
    )
    target_text = str(
        payload.get("target_text")
        or payload.get("name")
        or payload.get("name_contains")
        or payload.get("text")
        or ""
    ).strip()
    selector_key = str(payload.get("selector_key") or "").strip()
    normalized_action = _normalize_text(payload.get("profile_action") or action_type)
    if normalized_action in {"open_app", "focus_window"}:
        metadata = dict(observation_payload.get("metadata") or {})
        expected_process_names = {
            _normalize_text(item)
            for item in list(payload.get("process_names") or payload.get("processNames") or [])
            if _normalize_text(item)
        }
        observed_process_name = _normalize_text(
            metadata.get("processName")
            or observation_payload.get("processName")
            or target_payload.get("processName")
        )
        process_match = not expected_process_names or observed_process_name in expected_process_names
        title_match_score = window_title_match_score(window_title, [expected_window_title]) if expected_window_title else 0
        if expected_window_title and process_match and title_match_score >= 40:
            reasons.append("当前窗口标题与进程已共同匹配目标窗口。")
            return True, reasons
        if expected_window_title and not expected_process_names and title_match_score >= 100:
            reasons.append("当前窗口标题已精确匹配目标窗口。")
            return True, reasons
        normalized_app_id = _normalize_text(app_id or observation_payload.get("app") or target_payload.get("appId"))
        if normalized_app_id and normalized_app_id != "desktop":
            observation_tokens = _observation_text_tokens(observation_payload)
            if process_match and _contains_token(observation_tokens, normalized_app_id):
                reasons.append("当前观察结果已落在目标应用上下文。")
                return True, reasons
    if normalized_action in {"wait_for_element", "find_element", "observe"} and target_text and _target_visible(observation_payload, target_text):
        reasons.append("观察结果中已经存在目标元素文本。")
        return True, reasons
    if normalized_action == "wait_for_element" and selector_key:
        inferred_identity, _, _ = infer_page_identity(
            app_id=app_id,
            action_type=action_type,
            action_payload=payload,
            observation=observation_payload,
            target=target_payload,
        )
        if inferred_identity.endswith(f".{selector_key}"):
            reasons.append("当前页面身份已经对齐 selector_key。")
            return True, reasons
    return False, reasons


def infer_blocker_state(
    *,
    observation: Dict[str, Any] | None,
    verification: Dict[str, Any] | None = None,
    update_request: Dict[str, Any] | None = None,
    visual_guard: Dict[str, Any] | None = None,
) -> tuple[str, List[str]]:
    reasons: List[str] = []
    observation_payload = dict(observation or {})
    text_tokens = _observation_text_tokens(observation_payload)
    if isinstance(update_request, dict) and update_request:
        reasons.append("动作触发了 update_request。")
        return "major_deviation", reasons
    verification_payload = dict(verification or {})
    verification_status = str(verification_payload.get("status") or "").strip().lower()
    if verification_status in {
        "visual_guard_unconfirmed",
        "high_risk_visual_confirmation_required",
        "high_risk_pre_action_confirmation_required",
    }:
        reasons.append("验证阶段要求视觉确认。")
        return "confirmation_required", reasons
    visual_payload = dict(visual_guard or {})
    if str(visual_payload.get("status") or "").strip().lower() == "analyzed" and visual_payload.get("confirmed") is False:
        reasons.append("视觉保底确认失败。")
        return "visual_mismatch", reasons
    if _contains_token(text_tokens, "登录", "重新登录", "扫码登录", "sign in", "log in"):
        reasons.append("界面出现登录/重新登录信号。")
        return "login_blocker", reasons
    if _contains_token(text_tokens, "允许", "权限", "permission", "access", "授权"):
        reasons.append("界面出现权限/授权信号。")
        return "permission_dialog", reasons
    if _contains_token(text_tokens, "加载中", "请稍候", "正在加载", "loading", "缓冲", "重试中"):
        reasons.append("界面仍处于加载或等待态。")
        return "loading_mask", reasons
    if _contains_token(text_tokens, "确认", "确定", "取消", "是否", "保存更改", "delete", "confirm"):
        reasons.append("界面出现确认类阻塞提示。")
        return "confirmation_required", reasons
    window_payload = normalize_window_payload(
        {
            "title": observation_payload.get("windowTitle"),
            "className": dict(observation_payload.get("metadata") or {}).get("className"),
            "handle": dict(observation_payload.get("metadata") or {}).get("windowHandle"),
        }
    )
    if is_probable_dialog_window(window_payload):
        reasons.append("当前窗口更像系统/应用对话框。")
        return "dialog", reasons
    return "none", reasons


def infer_transition_state(
    *,
    app_id: str | None = None,
    action_type: str = "",
    action_payload: Dict[str, Any] | None = None,
    before_observation: Dict[str, Any] | None,
    after_observation: Dict[str, Any] | None,
    verification: Dict[str, Any] | None = None,
    update_request: Dict[str, Any] | None = None,
    blocker_state: str | None = None,
    target: Dict[str, Any] | None = None,
) -> tuple[str, List[str]]:
    reasons: List[str] = []
    if isinstance(update_request, dict) and update_request:
        reasons.append("动作后触发更新请求。")
        return "update_requested", reasons
    if str(blocker_state or "").strip().lower() not in {"", "none"}:
        reasons.append("当前页面存在 blocker。")
        return "blocked", reasons
    goal_state_matched, goal_state_reasons = infer_goal_state_match(
        app_id=app_id,
        action_type=action_type,
        action_payload=action_payload,
        observation=after_observation,
        target=target,
    )
    if goal_state_matched:
        reasons.extend(goal_state_reasons)
        return "already_in_target_state", reasons
    verification_payload = dict(verification or {})
    if verification_payload.get("passed") is True:
        before_tree = str((before_observation or {}).get("treeHash") or "").strip()
        after_tree = str((after_observation or {}).get("treeHash") or "").strip()
        before_screen = str((before_observation or {}).get("screenHash") or "").strip()
        after_screen = str((after_observation or {}).get("screenHash") or "").strip()
        if before_tree and after_tree and before_tree != after_tree:
            reasons.append("tree hash 已变化。")
            return "state_advanced", reasons
        if before_screen and after_screen and before_screen != after_screen:
            reasons.append("screen hash 已变化。")
            return "state_advanced", reasons
        reasons.append("结构化验证已通过。")
        return "verified_stable", reasons
    if verification_payload.get("passed") is False:
        reasons.append("结构化验证未通过。")
        return "failed", reasons
    before_tree = str((before_observation or {}).get("treeHash") or "").strip()
    after_tree = str((after_observation or {}).get("treeHash") or "").strip()
    before_screen = str((before_observation or {}).get("screenHash") or "").strip()
    after_screen = str((after_observation or {}).get("screenHash") or "").strip()
    if (before_tree and after_tree and before_tree != after_tree) or (before_screen and after_screen and before_screen != after_screen):
        reasons.append("界面结构已变化，仍等待进一步验证。")
        return "waiting_for_transition", reasons
    return "unknown", reasons


def build_scene_assessment(
    *,
    app_id: str | None,
    action_type: str,
    action_payload: Dict[str, Any] | None = None,
    observation: Dict[str, Any] | None = None,
    target: Dict[str, Any] | None = None,
    before_observation: Dict[str, Any] | None = None,
    verification: Dict[str, Any] | None = None,
    update_request: Dict[str, Any] | None = None,
    visual_guard: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    primitive = resolve_computer_use_primitive(action_type, action_payload)
    page_identity, identity_confidence, identity_reasons = infer_page_identity(
        app_id=app_id,
        action_type=action_type,
        action_payload=action_payload,
        observation=observation,
        target=target,
    )
    blocker_state, blocker_reasons = infer_blocker_state(
        observation=observation,
        verification=verification,
        update_request=update_request,
        visual_guard=visual_guard,
    )
    transition_state, transition_reasons = infer_transition_state(
        app_id=app_id,
        action_type=action_type,
        action_payload=action_payload,
        before_observation=before_observation,
        after_observation=observation,
        verification=verification,
        update_request=update_request,
        blocker_state=blocker_state,
        target=target,
    )
    affordances = infer_affordances(
        observation=observation,
        primitive_affordances=primitive.affordances,
    )
    confidence = "high" if (
        identity_confidence == "high"
        and blocker_state == "none"
        and transition_state in {"verified_stable", "state_advanced", "already_in_target_state"}
    ) else ("medium" if identity_confidence != "low" and blocker_state in {"none", "loading_mask"} else "low")
    return ComputerUseSceneAssessment(
        page_identity=page_identity,
        blocker_state=blocker_state,
        transition_state=transition_state,
        affordances=affordances,
        confidence=confidence,
        reasons=list(dict.fromkeys(identity_reasons + blocker_reasons + transition_reasons)),
    ).as_dict()
