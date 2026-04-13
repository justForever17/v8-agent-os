from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol, runtime_checkable


class DesktopDriverError(RuntimeError):
    pass


@dataclass(slots=True)
class DesktopInputCapabilities:
    strategy_order: List[str] = field(default_factory=list)
    supports_send_keys: bool = False
    supports_sendinput: bool = False
    supports_window_message: bool = False
    supports_clipboard_text: bool = False
    supports_clipboard_files: bool = False
    supports_modifier_normalization: bool = False
    supports_coordinate_typing: bool = False
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "strategyOrder": list(self.strategy_order),
            "supportsSendKeys": bool(self.supports_send_keys),
            "supportsSendInput": bool(self.supports_sendinput),
            "supportsWindowMessage": bool(self.supports_window_message),
            "supportsClipboardText": bool(self.supports_clipboard_text),
            "supportsClipboardFiles": bool(self.supports_clipboard_files),
            "supportsModifierNormalization": bool(self.supports_modifier_normalization),
            "supportsCoordinateTyping": bool(self.supports_coordinate_typing),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class DesktopWindowCapabilities:
    supports_focus: bool = False
    supports_activate: bool = False
    supports_dialog_detection: bool = False
    supports_window_candidates: bool = False
    supports_foreground_window: bool = False
    supports_root_capture_recovery: bool = False
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "supportsFocus": bool(self.supports_focus),
            "supportsActivate": bool(self.supports_activate),
            "supportsDialogDetection": bool(self.supports_dialog_detection),
            "supportsWindowCandidates": bool(self.supports_window_candidates),
            "supportsForegroundWindow": bool(self.supports_foreground_window),
            "supportsRootCaptureRecovery": bool(self.supports_root_capture_recovery),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class DesktopPointerCapabilities:
    supports_move: bool = False
    supports_click: bool = False
    supports_double_click: bool = False
    supports_right_click: bool = False
    supports_hover: bool = False
    supports_drag: bool = False
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "supportsMove": bool(self.supports_move),
            "supportsClick": bool(self.supports_click),
            "supportsDoubleClick": bool(self.supports_double_click),
            "supportsRightClick": bool(self.supports_right_click),
            "supportsHover": bool(self.supports_hover),
            "supportsDrag": bool(self.supports_drag),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class DesktopViewportCapabilities:
    supports_wheel: bool = False
    supports_page_scroll: bool = False
    supports_scrollbar_drag: bool = False
    supports_ensure_visible: bool = False
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "supportsWheel": bool(self.supports_wheel),
            "supportsPageScroll": bool(self.supports_page_scroll),
            "supportsScrollbarDrag": bool(self.supports_scrollbar_drag),
            "supportsEnsureVisible": bool(self.supports_ensure_visible),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class DesktopAccessibilityCapabilities:
    primary_backend: str
    fallback_backends: List[str] = field(default_factory=list)
    supports_window_enumeration: bool = True
    supports_element_observation: bool = True
    supports_visual_fallback: bool = False
    supports_foreground_window: bool = False
    supports_root_capture_recovery: bool = False
    future_platform_targets: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "primaryBackend": self.primary_backend,
            "fallbackBackends": list(self.fallback_backends),
            "supportsWindowEnumeration": bool(self.supports_window_enumeration),
            "supportsElementObservation": bool(self.supports_element_observation),
            "supportsVisualFallback": bool(self.supports_visual_fallback),
            "supportsForegroundWindow": bool(self.supports_foreground_window),
            "supportsRootCaptureRecovery": bool(self.supports_root_capture_recovery),
            "futurePlatformTargets": list(self.future_platform_targets),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class DesktopObservationCapabilities:
    supports_scene_identity: bool = False
    supports_blocker_detection: bool = False
    supports_goal_state_detection: bool = False
    supports_keyframe_visual_fallback: bool = False
    frame_sequence_sampling_available: bool = False
    frame_sequence_semantic_verification_available: bool = False
    supports_browser_automation: bool = False
    browser_lane_available: bool = False
    browser_lane_provider: str | None = None
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "supportsSceneIdentity": bool(self.supports_scene_identity),
            "supportsBlockerDetection": bool(self.supports_blocker_detection),
            "supportsGoalStateDetection": bool(self.supports_goal_state_detection),
            "supportsKeyframeVisualFallback": bool(self.supports_keyframe_visual_fallback),
            "frameSequenceSamplingAvailable": bool(self.frame_sequence_sampling_available),
            "frameSequenceSemanticVerificationAvailable": bool(self.frame_sequence_semantic_verification_available),
            "supportsBrowserAutomation": bool(self.supports_browser_automation),
            "browserLaneAvailable": bool(self.browser_lane_available),
            "browserLaneProvider": self.browser_lane_provider,
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class DesktopVerificationCapabilities:
    supports_window_verification: bool = False
    supports_focus_verification: bool = False
    supports_text_verification: bool = False
    supports_file_verification: bool = False
    supports_viewport_verification: bool = False
    supports_business_verification: bool = False
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "supportsWindowVerification": bool(self.supports_window_verification),
            "supportsFocusVerification": bool(self.supports_focus_verification),
            "supportsTextVerification": bool(self.supports_text_verification),
            "supportsFileVerification": bool(self.supports_file_verification),
            "supportsViewportVerification": bool(self.supports_viewport_verification),
            "supportsBusinessVerification": bool(self.supports_business_verification),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class DesktopExecutionRouteCapabilities:
    supports_native_command: bool = False
    supports_semantic_route: bool = False
    supports_visual_route: bool = False
    supports_coordinate_fallback: bool = False
    supports_browser_automation: bool = False
    preferred_route_order: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "supportsNativeCommand": bool(self.supports_native_command),
            "supportsSemanticRoute": bool(self.supports_semantic_route),
            "supportsVisualRoute": bool(self.supports_visual_route),
            "supportsCoordinateFallback": bool(self.supports_coordinate_fallback),
            "supportsBrowserAutomation": bool(self.supports_browser_automation),
            "preferredRouteOrder": list(self.preferred_route_order),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class DesktopPermissionCapabilities:
    accessibility_status: str = "unknown"
    automation_status: str = "unknown"
    screenshot_status: str = "unknown"
    input_synthesis_status: str = "unknown"
    portal_capture_status: str = "unsupported"
    portal_input_status: str = "unsupported"
    session_type: str = "unknown"
    compositor: str = "unknown"
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "accessibilityStatus": self.accessibility_status,
            "automationStatus": self.automation_status,
            "screenshotStatus": self.screenshot_status,
            "inputSynthesisStatus": self.input_synthesis_status,
            "portalCaptureStatus": self.portal_capture_status,
            "portalInputStatus": self.portal_input_status,
            "sessionType": self.session_type,
            "compositor": self.compositor,
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class DesktopDriverCapabilities:
    platform: str
    backend: str
    input: DesktopInputCapabilities
    accessibility: DesktopAccessibilityCapabilities
    window: DesktopWindowCapabilities | None = None
    pointer: DesktopPointerCapabilities | None = None
    viewport: DesktopViewportCapabilities | None = None
    observation: DesktopObservationCapabilities | None = None
    verification: DesktopVerificationCapabilities | None = None
    execution: DesktopExecutionRouteCapabilities | None = None
    permission: DesktopPermissionCapabilities | None = None

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "platform": self.platform,
            "backend": self.backend,
            "input": self.input.as_dict(),
            "accessibility": self.accessibility.as_dict(),
        }
        if self.window is not None:
            payload["window"] = self.window.as_dict()
        if self.pointer is not None:
            payload["pointer"] = self.pointer.as_dict()
        if self.viewport is not None:
            payload["viewport"] = self.viewport.as_dict()
        if self.observation is not None:
            payload["observation"] = self.observation.as_dict()
        if self.verification is not None:
            payload["verification"] = self.verification.as_dict()
        if self.execution is not None:
            payload["execution"] = self.execution.as_dict()
        if self.permission is not None:
            payload["permission"] = self.permission.as_dict()
        return payload


@runtime_checkable
class DesktopInputAdapter(Protocol):
    def capability_summary(self) -> Dict[str, Any]:
        ...


@runtime_checkable
class DesktopAccessibilityAdapter(Protocol):
    platform: str
    backend: str

    def is_available(self) -> bool:
        ...

    def list_windows(self, **kwargs) -> List[Dict[str, Any]]:
        ...

    def observe_desktop(self, **kwargs) -> Any:
        ...

    def foreground_window(self, **kwargs) -> Dict[str, Any] | None:
        ...


@runtime_checkable
class DesktopControlDriver(DesktopInputAdapter, DesktopAccessibilityAdapter, Protocol):
    platform: str
    backend: str

    def capability_summary(self) -> Dict[str, Any]:
        ...
