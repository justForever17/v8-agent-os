from __future__ import annotations

import json
import re
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
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

    def _model_response_text(self, response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if text:
                        parts.append(str(text))
            if parts:
                return "\n".join(parts)
        return str(content or "").strip()

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

    def right_click(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.right_click(**self._base_kwargs(), **payload))

    def hover(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.hover(**self._base_kwargs(), **payload))

    def drag(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.drag(**self._base_kwargs(), **payload))

    def type_text(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.type_text(**self._base_kwargs(), **payload))

    def find_and_type(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.find_and_type(**self._base_kwargs(), **payload))

    def scroll(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.scroll(**self._base_kwargs(), **payload))

    def page_scroll(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        return self._ensure_success(computer_use_runtime.page_scroll(**self._base_kwargs(), **payload))

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

    def assert_text(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        expected = str(
            payload.get("expected")
            or payload.get("expected_text")
            or payload.get("text")
            or payload.get("value")
            or ""
        ).strip()
        observation = self.observe(*args, **kwargs)
        if expected:
            haystack = json.dumps(observation, ensure_ascii=False)
            if expected not in haystack:
                raise AssertionError(f"未在当前观测结果中找到文本：{expected}")
        return {"asserted": True, "expectedText": expected, "observation": observation}

    def assert_condition(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        expected = payload.get("expected") or payload.get("expected_text") or payload.get("text")
        if expected not in (None, ""):
            return self.assert_text(*args, **kwargs)
        condition = payload.get("condition")
        if isinstance(condition, bool):
            if not condition:
                raise AssertionError("RPA 断言条件为 false。")
            return {"asserted": True, "condition": condition}
        return {"asserted": True, "condition": condition or "not_specified"}

    def set_workflow_variable(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        name = str(payload.get("name") or payload.get("variable") or "").strip()
        if not name:
            raise ValueError("set_variable 步骤缺少变量名。")
        return {"variable": name, "value": payload.get("value")}

    def copy_file(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        source = str(payload.get("source") or payload.get("src") or payload.get("from") or "").strip()
        target = str(payload.get("target") or payload.get("dest") or payload.get("to") or "").strip()
        if not source or not target:
            raise ValueError("file_copy 步骤缺少 source/target。")
        copied_to = shutil.copy2(source, target)
        return {"source": source, "target": str(copied_to)}

    def http_request(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("http_request 步骤缺少 URL。")
        method = str(payload.get("method") or "GET").strip().upper()
        body = payload.get("body")
        data = None if body in (None, "") else str(body).encode("utf-8")
        headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
        timeout = float(payload.get("timeout") or payload.get("timeout_seconds") or 30)
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                text = raw.decode("utf-8", errors="replace")
                return {
                    "ok": 200 <= int(response.status) < 400,
                    "status": int(response.status),
                    "bodyPreview": text[:2000],
                    "bodyBytes": len(raw),
                }
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
            raise AssertionError(f"HTTP {exc.code}: {text[:1000]}") from exc

    def ocr(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        observation = self.observe(*args, **kwargs)
        return {
            "mode": "computer_use_observation",
            "query": payload.get("query") or payload.get("text"),
            "observation": observation,
        }

    def llm_call(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        prompt = str(payload.get("prompt") or payload.get("input") or payload.get("text") or "").strip()
        if not prompt:
            raise ValueError("llm_call 步骤缺少 prompt/input/text。")
        role = str(payload.get("role") or "rpa_runtime").strip() or "rpa_runtime"
        system = str(
            payload.get("system")
            or "You are executing a V8 RPA workflow node. Return concise, directly usable output for the workflow."
        )
        temperature = float(payload.get("temperature") or 0)
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from core.llm_factory import llm_factory

            model = llm_factory.create_for_role(role, streaming=False, temperature=temperature)
            response = model.invoke(
                [SystemMessage(content=system), HumanMessage(content=prompt)],
                config={"callbacks": []},
            )
            text = self._model_response_text(response)
        except Exception as exc:
            raise RuntimeError(f"llm_call 节点执行失败：{type(exc).__name__}: {exc}") from exc
        return {
            "role": role,
            "promptPreview": prompt[:500],
            "text": text,
        }

    def run_subflow(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = self._robot_kwargs(args, kwargs)
        script_id = str(
            payload.get("subflowId")
            or payload.get("scriptId")
            or payload.get("draftId")
            or payload.get("name")
            or ""
        ).strip()
        robot_file = str(payload.get("robotFile") or payload.get("path") or "").strip()
        variables = payload.get("variables") if isinstance(payload.get("variables"), dict) else {}
        timeout_ms = int(payload.get("timeout_ms") or payload.get("timeoutMs") or 600000)
        cwd = str(payload.get("cwd") or "").strip() or None
        try:
            from runtimes.rpa.robot_adapter import robot_framework_adapter

            if script_id:
                prepared = robot_framework_adapter.prepare_draft_run(script_id=script_id, variables=variables)
                subject = script_id
            elif robot_file:
                prepared = robot_framework_adapter.prepare_existing_run(robot_file=Path(robot_file), variables=variables)
                subject = robot_file
            else:
                raise ValueError("subflow 步骤缺少 subflowId/scriptId/draftId/name 或 robotFile。")
            result = robot_framework_adapter.run_command(
                command=list(prepared.get("command") or []),
                timeout_ms=timeout_ms,
                cwd=cwd,
            )
        except Exception as exc:
            raise RuntimeError(f"subflow 节点执行失败：{type(exc).__name__}: {exc}") from exc
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        returncode = int(result.get("returncode") or 0)
        if returncode != 0:
            raise AssertionError((stderr or stdout or f"Subflow failed: {subject}")[:2000])
        return {
            "subflow": subject,
            "returncode": returncode,
            "stdoutPreview": stdout[-2000:],
            "stderrPreview": stderr[-1000:],
        }
