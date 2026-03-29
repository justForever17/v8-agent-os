from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class ComputerUsePrimitive:
    primitive_id: str
    category: str
    action: str
    affordances: List[str] = field(default_factory=list)
    requires_page_identity: bool = True
    requires_verification_contract: bool = True
    requires_recovery_policy: bool = True
    supports_rpa_promotion: bool = True
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.primitive_id,
            "category": self.category,
            "action": self.action,
            "affordances": list(self.affordances),
            "requiresPageIdentity": bool(self.requires_page_identity),
            "requiresVerificationContract": bool(self.requires_verification_contract),
            "requiresRecoveryPolicy": bool(self.requires_recovery_policy),
            "supportsRpaPromotion": bool(self.supports_rpa_promotion),
            "notes": list(self.notes),
        }


_PRIMITIVE_SPECS: Dict[str, ComputerUsePrimitive] = {
    "launch_app": ComputerUsePrimitive(
        primitive_id="window.open",
        category="window",
        action="open",
        affordances=["window_open", "window_focus"],
        notes=["高层原语别名，应继续下沉到 open_app 语义。"],
    ),
    "open_app": ComputerUsePrimitive(
        primitive_id="window.open",
        category="window",
        action="open",
        affordances=["window_open", "window_focus"],
        notes=["需要等待主窗口可用，再进入后续操作。"],
    ),
    "ensure_window": ComputerUsePrimitive(
        primitive_id="window.focus",
        category="window",
        action="focus",
        affordances=["window_focus", "window_activate", "window_binding"],
        notes=["高层原语别名，应优先用于已打开窗口的绑定与前台确认。"],
    ),
    "focus_window": ComputerUsePrimitive(
        primitive_id="window.focus",
        category="window",
        action="focus",
        affordances=["window_focus", "window_activate"],
    ),
    "observe_scene": ComputerUsePrimitive(
        primitive_id="observe.scene",
        category="observe",
        action="observe",
        affordances=["scene_scan", "window_context", "page_identity"],
        supports_rpa_promotion=False,
        requires_recovery_policy=False,
        notes=["高层原语别名，应返回 scene/binding/page identity 证据。"],
    ),
    "observe": ComputerUsePrimitive(
        primitive_id="observe.scene",
        category="observe",
        action="observe",
        affordances=["scene_scan", "window_context"],
        supports_rpa_promotion=False,
        requires_recovery_policy=False,
        notes=["观察步骤只提供上下文，不应单独提级为稳定模板动作。"],
    ),
    "find": ComputerUsePrimitive(
        primitive_id="observe.find_element",
        category="observe",
        action="find_element",
        affordances=["selector_lookup"],
        supports_rpa_promotion=False,
        requires_recovery_policy=False,
    ),
    "click": ComputerUsePrimitive(
        primitive_id="pointer.click",
        category="pointer",
        action="click",
        affordances=["clickable"],
    ),
    "click_target": ComputerUsePrimitive(
        primitive_id="pointer.click",
        category="pointer",
        action="click",
        affordances=["clickable", "target_resolved"],
        notes=["高层原语别名，应优先走目标语义与窗口绑定。"],
    ),
    "double_click": ComputerUsePrimitive(
        primitive_id="pointer.double_click",
        category="pointer",
        action="double_click",
        affordances=["clickable", "activatable"],
    ),
    "right_click": ComputerUsePrimitive(
        primitive_id="pointer.right_click",
        category="pointer",
        action="right_click",
        affordances=["context_menu"],
    ),
    "right_click_target": ComputerUsePrimitive(
        primitive_id="pointer.right_click",
        category="pointer",
        action="right_click",
        affordances=["context_menu", "target_resolved"],
        notes=["高层原语别名，应验证上下文菜单或目标状态变化。"],
    ),
    "hover": ComputerUsePrimitive(
        primitive_id="pointer.hover",
        category="pointer",
        action="hover",
        affordances=["hoverable"],
    ),
    "hover_target": ComputerUsePrimitive(
        primitive_id="pointer.hover",
        category="pointer",
        action="hover",
        affordances=["hoverable", "target_resolved"],
        notes=["高层原语别名，应验证 hover affordance 或 tooltip 变化。"],
    ),
    "drag": ComputerUsePrimitive(
        primitive_id="pointer.drag",
        category="pointer",
        action="drag",
        affordances=["draggable", "droppable"],
        notes=["拖拽是一级原语，必须保留起终点与轨迹证据。"],
    ),
    "drag_pointer": ComputerUsePrimitive(
        primitive_id="pointer.drag",
        category="pointer",
        action="drag",
        affordances=["draggable", "droppable", "target_resolved"],
        notes=["高层原语别名，应继续保留起终点与轨迹证据。"],
    ),
    "scroll": ComputerUsePrimitive(
        primitive_id="viewport.scroll_wheel",
        category="viewport",
        action="scroll_wheel",
        affordances=["scrollable"],
    ),
    "page_scroll": ComputerUsePrimitive(
        primitive_id="viewport.page_scroll",
        category="viewport",
        action="page_scroll",
        affordances=["scrollable", "paginated"],
    ),
    "scroll_list": ComputerUsePrimitive(
        primitive_id="viewport.scroll_wheel",
        category="viewport",
        action="scroll_wheel",
        affordances=["scrollable", "list_view"],
    ),
    "scroll_view": ComputerUsePrimitive(
        primitive_id="viewport.scroll_wheel",
        category="viewport",
        action="scroll_wheel",
        affordances=["scrollable", "viewport_change"],
        notes=["高层原语别名，可映射到 scroll/page_scroll/scroll_list。"],
    ),
    "input_text": ComputerUsePrimitive(
        primitive_id="input.text",
        category="input",
        action="text",
        affordances=["editable", "replaceable", "focus_confirmed"],
        notes=["高层原语别名，应统一清空、聚焦和输入后验证。"],
    ),
    "type_text": ComputerUsePrimitive(
        primitive_id="input.text",
        category="input",
        action="text",
        affordances=["editable", "replaceable"],
    ),
    "paste_text": ComputerUsePrimitive(
        primitive_id="input.paste_text",
        category="input",
        action="paste_text",
        affordances=["editable", "replaceable", "clipboard_text", "focus_confirmed"],
        notes=["高层原语别名，应统一文本剪贴板载荷与输入后文本验证。"],
    ),
    "paste_files": ComputerUsePrimitive(
        primitive_id="input.paste_files",
        category="input",
        action="paste_files",
        affordances=["file_payload", "content_receiver", "focus_confirmed"],
        notes=["高层原语别名，应统一文件载荷、接收区聚焦和文件系统侧验证。"],
    ),
    "find_and_type": ComputerUsePrimitive(
        primitive_id="input.find_and_type",
        category="input",
        action="find_and_type",
        affordances=["editable", "selector_lookup"],
    ),
    "send_hotkey": ComputerUsePrimitive(
        primitive_id="keyboard.hotkey",
        category="keyboard",
        action="hotkey",
        affordances=["hotkey_dispatch", "focus_confirmed"],
        notes=["高层原语别名，应继续下沉到平台热键与安全阻断链。"],
    ),
    "hotkey": ComputerUsePrimitive(
        primitive_id="keyboard.hotkey",
        category="keyboard",
        action="hotkey",
        affordances=["hotkey_dispatch"],
    ),
    "wait": ComputerUsePrimitive(
        primitive_id="observe.wait_for_element",
        category="observe",
        action="wait_for_element",
        affordances=["waitable", "selector_lookup"],
        supports_rpa_promotion=False,
    ),
    "wait_for_element": ComputerUsePrimitive(
        primitive_id="observe.wait_for_element",
        category="observe",
        action="wait_for_element",
        affordances=["waitable", "selector_lookup"],
        supports_rpa_promotion=False,
    ),
    "screenshot": ComputerUsePrimitive(
        primitive_id="observe.capture_screenshot",
        category="observe",
        action="capture_screenshot",
        affordances=["snapshot"],
        supports_rpa_promotion=False,
        requires_recovery_policy=False,
    ),
    "capture_screenshot": ComputerUsePrimitive(
        primitive_id="observe.capture_screenshot",
        category="observe",
        action="capture_screenshot",
        affordances=["snapshot"],
        supports_rpa_promotion=False,
        requires_recovery_policy=False,
    ),
    "click_toolbar_action": ComputerUsePrimitive(
        primitive_id="pointer.click",
        category="pointer",
        action="click",
        affordances=["clickable", "toolbar_action"],
    ),
}

_PRIMITIVE_CATEGORY_ORDER = [
    "window",
    "pointer",
    "viewport",
    "input",
    "keyboard",
    "observe",
    "custom",
]


def resolve_computer_use_primitive(action_type: str, action_payload: Dict[str, Any] | None = None) -> ComputerUsePrimitive:
    normalized = str(action_type or "").strip().lower()
    payload = dict(action_payload or {})
    primitive = _PRIMITIVE_SPECS.get(normalized)
    if primitive is None:
        primitive = ComputerUsePrimitive(
            primitive_id=f"custom.{normalized or 'action'}",
            category="custom",
            action=normalized or "unknown",
            affordances=[],
            supports_rpa_promotion=False,
            notes=["未知动作类型，暂不允许自动提级到 RPA。"],
        )
    affordances = list(primitive.affordances)
    notes = list(primitive.notes)
    if payload.get("file_path") not in (None, "") or list(payload.get("file_paths") or []) or list(payload.get("attachment_paths") or []):
        if "file_payload" not in affordances:
            affordances.append("file_payload")
        notes.append("动作包含文件载荷，必须保留文件侧验证证据。")
    if payload.get("point") not in (None, "") or list(payload.get("point_candidates") or []):
        if "coordinate_target" not in affordances:
            affordances.append("coordinate_target")
        notes.append("动作包含坐标定位，提级前必须补足页面身份与稳定验证。")
    if bool(payload.get("window_typing")):
        affordances.append("window_typing")
    return ComputerUsePrimitive(
        primitive_id=primitive.primitive_id,
        category=primitive.category,
        action=primitive.action,
        affordances=list(dict.fromkeys(affordances)),
        requires_page_identity=primitive.requires_page_identity,
        requires_verification_contract=primitive.requires_verification_contract,
        requires_recovery_policy=primitive.requires_recovery_policy,
        supports_rpa_promotion=primitive.supports_rpa_promotion,
        notes=list(dict.fromkeys(note for note in notes if str(note).strip())),
    )


def primitive_from_step(step: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(step or {})
    action = str(payload.get("action") or payload.get("use") or "").strip().lower()
    return resolve_computer_use_primitive(action, payload).as_dict()


def list_computer_use_primitives(*, category: str | None = None) -> List[Dict[str, Any]]:
    normalized_category = str(category or "").strip().lower() or None
    items: List[Dict[str, Any]] = []
    for action_type, primitive in sorted(
        _PRIMITIVE_SPECS.items(),
        key=lambda item: (
            _PRIMITIVE_CATEGORY_ORDER.index(item[1].category)
            if item[1].category in _PRIMITIVE_CATEGORY_ORDER
            else len(_PRIMITIVE_CATEGORY_ORDER),
            item[1].primitive_id,
            item[0],
        ),
    ):
        if normalized_category and primitive.category != normalized_category:
            continue
        payload = primitive.as_dict()
        payload["actionType"] = action_type
        items.append(payload)
    return items


def primitive_validation_matrix() -> Dict[str, Any]:
    primitives = list_computer_use_primitives()
    categories = {
        category: [item for item in primitives if str(item.get("category") or "") == category]
        for category in _PRIMITIVE_CATEGORY_ORDER
        if any(str(item.get("category") or "") == category for item in primitives)
    }
    return {
        "summary": {
            "primitiveCount": len(primitives),
            "categoryCount": len(categories),
            "categories": list(categories.keys()),
            "promotionEligibleCount": sum(1 for item in primitives if bool(item.get("supportsRpaPromotion"))),
            "requiresVerificationContractCount": sum(
                1 for item in primitives if bool(item.get("requiresVerificationContract"))
            ),
            "requiresRecoveryPolicyCount": sum(
                1 for item in primitives if bool(item.get("requiresRecoveryPolicy"))
            ),
        },
        "categories": {
            category: {
                "count": len(items),
                "actions": [str(item.get("actionType") or "") for item in items],
                "primitiveIds": [str(item.get("id") or "") for item in items],
            }
            for category, items in categories.items()
        },
        "primitives": primitives,
    }
