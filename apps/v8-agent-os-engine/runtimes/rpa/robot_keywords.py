from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional

from runtimes.computer_use.runtime import computer_use_runtime


class V8ChatRPAKeywords:
    ROBOT_LIBRARY_SCOPE = "SUITE"

    def __init__(
        self,
        session_id: str | None = None,
        user_id: str = "robot_framework",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.project_id = project_id
        self.workspace_id = workspace_id
        self.workspace_path = workspace_path

    def _base_kwargs(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "user_id": self.user_id,
        }
        if self.session_id:
            payload["session_id"] = self.session_id
        if self.project_id:
            payload["project_id"] = self.project_id
        if self.workspace_id:
            payload["workspace_id"] = self.workspace_id
        if self.workspace_path:
            payload["workspace_path"] = self.workspace_path
        return payload

    def _ensure_success(self, result: Dict[str, Any]) -> Dict[str, Any]:
        verification = (((result or {}).get("result") or {}).get("verification") or {})
        if verification and verification.get("passed") is False:
            raise AssertionError(str(verification.get("reason") or "RPA 步骤验证失败。"))
        return result

    def _coerce_robot_value(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        lowered = text.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if re.fullmatch(r"-?\d+", text):
            try:
                return int(text)
            except Exception:
                return text
        if re.fullmatch(r"-?\d+\.\d+", text):
            try:
                return float(text)
            except Exception:
                return text
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except Exception:
                return text
        return text

    def _robot_kwargs(self, args: tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(kwargs or {})
        for item in args:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                payload[key.strip()] = self._coerce_robot_value(value)
        return payload

    def open_app(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.open_app(**self._base_kwargs(), **payload))

    def focus_window(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.focus_window(**self._base_kwargs(), **payload))

    def click(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.click(**self._base_kwargs(), **payload))

    def double_click(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        payload["double"] = True
        return self._ensure_success(computer_use_runtime.click(**self._base_kwargs(), **payload))

    def type_text(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.type_text(**self._base_kwargs(), **payload))

    def find_and_type(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.find_and_type(**self._base_kwargs(), **payload))

    def scroll(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.scroll(**self._base_kwargs(), **payload))

    def scroll_list(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.scroll_list(**self._base_kwargs(), **payload))

    def click_toolbar_action(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.click_toolbar_action(**self._base_kwargs(), **payload))

    def hotkey(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.hotkey(**self._base_kwargs(), **payload))

    def wait_for_element(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.wait_for_element(**self._base_kwargs(), **payload))

    def capture_screenshot(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.capture_screenshot(**self._base_kwargs(), **payload))

    def execute_plan(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.execute_plan(**self._base_kwargs(), **payload))

    def wait(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        seconds = payload.get("seconds")
        timeout_ms = payload.get("timeout_ms")
        duration = 0.0
        if seconds not in (None, ""):
            duration = max(0.0, float(seconds))
        elif timeout_ms not in (None, ""):
            duration = max(0.0, float(timeout_ms) / 1000.0)
        time.sleep(duration)
        return {"waitedSeconds": duration}

    def observe(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return computer_use_runtime.observe(**self._base_kwargs(), **payload)

    def observe_desktop(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.observe(*args, **kwargs)
