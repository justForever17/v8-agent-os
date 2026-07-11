from __future__ import annotations

import mimetypes
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

from core.artifact_store import artifact_store
from core.multimodal_payload_adapter import utc_now_iso


_EXECUTABLE_SUFFIXES = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".sh", ".js", ".jar", ".app", ".dmg", ".pkg"}
_SAFE_OPEN_SUFFIXES = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".csv", ".json", ".html", ".htm"}


@dataclass(slots=True)
class PlaybookExecutionContext:
    runtime: Any
    task_loop: dict[str, Any]
    goal: str
    session_id: str | None = None
    run_id: str | None = None
    user_id: str = "anonymous"
    project_id: str | None = None
    workspace_id: str | None = None
    workspace_path: str | None = None
    playbook_inputs: dict[str, Any] = field(default_factory=dict)
    allow_real_click: bool = False
    invocation_metadata: dict[str, Any] = field(default_factory=dict)


class ComputerUsePlaybookExecutor(Protocol):
    playbook_id: str

    def can_handle(self, task_loop: dict[str, Any]) -> bool:
        ...

    def execute(self, context: PlaybookExecutionContext) -> dict[str, Any]:
        ...

    def verify(self, result: dict[str, Any]) -> dict[str, Any]:
        ...


class ComputerUsePlaybookExecutorRegistry:
    def __init__(self, executors: list[ComputerUsePlaybookExecutor] | None = None) -> None:
        self._executors = {executor.playbook_id: executor for executor in list(executors or [])}

    def get(self, playbook_id: str | None) -> ComputerUsePlaybookExecutor | None:
        return self._executors.get(str(playbook_id or "").strip())

    def can_handle(self, task_loop: dict[str, Any]) -> bool:
        return self.get(_selected_playbook_id(task_loop)) is not None

    def execute(self, context: PlaybookExecutionContext) -> dict[str, Any]:
        selected = _selected_playbook_id(context.task_loop)
        executor = self.get(selected)
        if executor is None:
            return {"status": "not_applicable", "selectedPlaybook": selected, "taskLoop": context.task_loop}
        result = executor.execute(context)
        result.setdefault("selectedPlaybookExecutor", executor.__class__.__name__)
        result.setdefault("selectedPlaybook", selected)
        result.setdefault("taskLoop", context.task_loop)
        result["verification"] = executor.verify(result)
        return result


class GitHubStarExecutor:
    playbook_id = "github.star_repository"

    def can_handle(self, task_loop: dict[str, Any]) -> bool:
        return _selected_playbook_id(task_loop) == self.playbook_id

    def execute(self, context: PlaybookExecutionContext) -> dict[str, Any]:
        return context.runtime.execute_github_star_playbook(
            goal=context.goal,
            allow_real_click=context.allow_real_click,
            desired_state=str(context.playbook_inputs.get("desired_state") or context.playbook_inputs.get("desiredState") or "starred"),
            session_id=context.session_id,
            run_id=context.run_id,
            user_id=context.user_id,
            project_id=context.project_id,
            workspace_id=context.workspace_id,
            workspace_path=context.workspace_path,
            invocation_metadata=context.invocation_metadata,
        )

    def verify(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"status": "passed" if result.get("status") == "succeeded" else "not_passed", "kind": "github_star"}


class BrowserSearchOpenExecutor:
    playbook_id = "web.search_and_open_result"

    def can_handle(self, task_loop: dict[str, Any]) -> bool:
        return _selected_playbook_id(task_loop) == self.playbook_id

    def execute(self, context: PlaybookExecutionContext) -> dict[str, Any]:
        return _execute_browser_open_and_verify(context, expected_kind="search_and_open")

    def verify(self, result: dict[str, Any]) -> dict[str, Any]:
        post = dict(result.get("postState") or {})
        target = str(result.get("canonicalUrl") or "")
        return {"status": "passed" if target and _same_domain(target, str(post.get("url") or "")) else "not_passed", "kind": "url_loaded"}


class BrowserLoginGateExecutor:
    playbook_id = "browser.login_gate"

    def can_handle(self, task_loop: dict[str, Any]) -> bool:
        return _selected_playbook_id(task_loop) == self.playbook_id

    def execute(self, context: PlaybookExecutionContext) -> dict[str, Any]:
        target_url = _target_url(context)
        if not target_url:
            return _human_attention_result(context, reason="login_target_url_required")
        opened, decision, run_handle = _open_browser_target(context, target_url, preserve_on_human=True)
        pre_state = _browser_page_state(context.runtime, opened)
        if run_handle:
            run_handle.emit("computer_use.playbook.login_gate.pre_state", pre_state)
        resource_lease = _finish_run(context, run_handle, status="needs_human_login", reason="login_gate")
        return {
            "status": "needs_human_login",
            "recommendedNextAction": "ask_user",
            "humanInputRequest": context.runtime._human_input_request_payload(
                reason="needs_human_login",
                target_url=target_url,
                browser_target=opened,
            ),
            "canonicalUrl": target_url,
            "browserTarget": opened,
            "laneDecision": decision.as_dict(),
            "preState": pre_state,
            "postState": pre_state,
            "resourceLease": resource_lease,
            "runId": getattr(run_handle, "run_id", context.run_id),
            "sessionId": getattr(run_handle, "session_id", context.session_id),
        }

    def verify(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"status": "pending_human" if result.get("status") == "needs_human_login" else "not_passed", "kind": "login_gate"}


class BrowserFormSubmitExecutor:
    playbook_id = "web.form_submit"

    def can_handle(self, task_loop: dict[str, Any]) -> bool:
        return _selected_playbook_id(task_loop) == self.playbook_id

    def execute(self, context: PlaybookExecutionContext) -> dict[str, Any]:
        inputs = dict(context.playbook_inputs or {})
        fields = dict(inputs.get("fields") or {})
        if not fields:
            return _human_attention_result(context, reason="form_fields_required")
        target_url = _target_url(context) or str(inputs.get("url") or "").strip()
        if not target_url:
            return _human_attention_result(context, reason="form_target_url_required")
        opened, decision, run_handle = _open_browser_target(context, target_url)
        pre_state = _browser_page_state(context.runtime, opened)
        destructive = bool(inputs.get("destructive") or _looks_destructive(inputs.get("submitText") or inputs.get("submitSelector")))
        if destructive:
            _assess_safety(context, run_handle, "click", {"target_text": inputs.get("submitText") or "destructive form submit", "url": target_url})
        action = context.runtime.browser_automation._evaluate(
            target_id=str(opened.get("targetId") or ""),
            expression=_form_submit_script(
                fields=fields,
                submit_selector=str(inputs.get("submitSelector") or ""),
                submit_text=str(inputs.get("submitText") or "Submit"),
            ),
        ).get("value") or {}
        time.sleep(float(inputs.get("settleSeconds") or 0.3))
        post_state = _browser_page_state(context.runtime, opened)
        status = "succeeded" if dict(action).get("ok") else "failed"
        resource_lease = _finish_run(context, run_handle, status=status, reason="form_submit")
        return _playbook_result(
            status=status,
            canonical_url=target_url,
            opened=opened,
            decision=decision,
            pre_state=pre_state,
            action=action,
            post_state=post_state,
            resource_lease=resource_lease,
            run_handle=run_handle,
        )

    def verify(self, result: dict[str, Any]) -> dict[str, Any]:
        action = dict(result.get("action") or {})
        return {"status": "passed" if action.get("ok") else "not_passed", "kind": "form_submit"}


class BrowserFileUploadExecutor:
    playbook_id = "web.file_upload"

    def can_handle(self, task_loop: dict[str, Any]) -> bool:
        return _selected_playbook_id(task_loop) == self.playbook_id

    def execute(self, context: PlaybookExecutionContext) -> dict[str, Any]:
        inputs = dict(context.playbook_inputs or {})
        selector = str(inputs.get("selector") or inputs.get("fileInputSelector") or "").strip()
        file_path = str(inputs.get("filePath") or inputs.get("file_path") or "").strip()
        if not selector or not file_path:
            return _human_attention_result(context, reason="file_selector_and_path_required")
        path = Path(file_path).expanduser()
        if not path.exists() or not path.is_file():
            return _human_attention_result(context, reason="upload_file_not_found")
        target_url = _target_url(context) or str(inputs.get("url") or "").strip()
        if not target_url:
            return _human_attention_result(context, reason="upload_target_url_required")
        opened, decision, run_handle = _open_browser_target(context, target_url)
        pre_state = _browser_page_state(context.runtime, opened)
        action = context.runtime.browser_automation.set_files(
            payload={"browser_target_id": opened.get("targetId"), "browser_selector": selector, "file_paths": [str(path)]},
            decision=decision,
        )
        post_state = _browser_page_state(context.runtime, opened)
        visible = _page_contains(context.runtime, opened, path.name)
        status = "succeeded" if visible or dict(action.get("metadata") or {}).get("browserResult", {}).get("fileCount") else "failed"
        resource_lease = _finish_run(context, run_handle, status=status, reason="file_upload")
        return _playbook_result(
            status=status,
            canonical_url=target_url,
            opened=opened,
            decision=decision,
            pre_state=pre_state,
            action=action,
            post_state=post_state,
            resource_lease=resource_lease,
            run_handle=run_handle,
        )

    def verify(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"status": "passed" if result.get("status") == "succeeded" else "not_passed", "kind": "file_upload"}


class DownloadAndOpenExecutor:
    playbook_id = "download_and_open"

    def can_handle(self, task_loop: dict[str, Any]) -> bool:
        return _selected_playbook_id(task_loop) == self.playbook_id

    def execute(self, context: PlaybookExecutionContext) -> dict[str, Any]:
        target_url = _target_url(context) or str((context.playbook_inputs or {}).get("url") or "").strip()
        if not target_url:
            return _human_attention_result(context, reason="download_url_required")
        run_handle = _begin_run(context)
        suffix = _suffix_from_url(target_url)
        if suffix in _EXECUTABLE_SUFFIXES:
            _assess_safety(context, run_handle, "click", {"target_text": f"download executable {suffix}", "url": target_url})
            resource_lease = _finish_run(context, run_handle, status="review_required", reason="download_executable_requires_review")
            return {
                "status": "review_required",
                "reason": "download_executable_requires_review",
                "canonicalUrl": target_url,
                "recommendedNextAction": "approval",
                "resourceLease": resource_lease,
                "runId": getattr(run_handle, "run_id", context.run_id),
                "sessionId": getattr(run_handle, "session_id", context.session_id),
            }
        download_path = _download_to_tmp(target_url)
        artifact = artifact_store.record_local_file(
            file_path=download_path,
            session_id=getattr(run_handle, "session_id", context.session_id),
            run_id=getattr(run_handle, "run_id", context.run_id),
            metadata={"sourceUrl": target_url, "playbookId": self.playbook_id, "downloadedAt": utc_now_iso()},
            source_component="computer_use_runtime",
            node="download_and_open",
        )
        opened = False
        if suffix in _SAFE_OPEN_SUFFIXES and bool((context.playbook_inputs or {}).get("openAfterDownload", False)):
            try:
                os.startfile(str(download_path))  # type: ignore[attr-defined]
                opened = True
            except Exception:
                opened = False
        resource_lease = _finish_run(context, run_handle, status="succeeded", reason="download_registered")
        return {
            "status": "succeeded",
            "canonicalUrl": target_url,
            "artifact": artifact,
            "downloadedPath": str(download_path),
            "opened": opened,
            "resourceLease": resource_lease,
            "runId": getattr(run_handle, "run_id", context.run_id),
            "sessionId": getattr(run_handle, "session_id", context.session_id),
        }

    def verify(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"status": "passed" if result.get("artifact") else "not_passed", "kind": "download_artifact"}


class SettingsToggleExecutor:
    playbook_id = "settings.toggle_option"

    def can_handle(self, task_loop: dict[str, Any]) -> bool:
        return _selected_playbook_id(task_loop) == self.playbook_id

    def execute(self, context: PlaybookExecutionContext) -> dict[str, Any]:
        inputs = dict(context.playbook_inputs or {})
        target_url = _target_url(context) or str(inputs.get("url") or "").strip()
        if target_url:
            opened, decision, run_handle = _open_browser_target(context, target_url)
            pre_state = _browser_page_state(context.runtime, opened)
            _assess_safety(context, run_handle, "click", {"target_text": inputs.get("label") or "settings toggle", "url": target_url})
            action = context.runtime.browser_automation._evaluate(
                target_id=str(opened.get("targetId") or ""),
                expression=_toggle_script(
                    selector=str(inputs.get("selector") or ""),
                    label=str(inputs.get("label") or ""),
                    desired=inputs.get("desired"),
                ),
            ).get("value") or {}
            post_state = _browser_page_state(context.runtime, opened)
            status = "succeeded" if dict(action).get("changed") or dict(action).get("alreadyDesired") else "failed"
            resource_lease = _finish_run(context, run_handle, status=status, reason="settings_toggle")
            return _playbook_result(
                status=status,
                canonical_url=target_url,
                opened=opened,
                decision=decision,
                pre_state=pre_state,
                action=action,
                post_state=post_state,
                resource_lease=resource_lease,
                run_handle=run_handle,
            )
        return _human_attention_result(context, reason="settings_target_requires_url_or_visual_sequence")

    def verify(self, result: dict[str, Any]) -> dict[str, Any]:
        action = dict(result.get("action") or {})
        return {"status": "passed" if action.get("changed") or action.get("alreadyDesired") else "not_passed", "kind": "settings_toggle"}


def create_default_playbook_executor_registry() -> ComputerUsePlaybookExecutorRegistry:
    return ComputerUsePlaybookExecutorRegistry(
        [
            GitHubStarExecutor(),
            BrowserSearchOpenExecutor(),
            BrowserLoginGateExecutor(),
            BrowserFormSubmitExecutor(),
            BrowserFileUploadExecutor(),
            DownloadAndOpenExecutor(),
            SettingsToggleExecutor(),
        ]
    )


def _selected_playbook_id(task_loop: dict[str, Any]) -> str:
    return str((task_loop.get("domain") or {}).get("selectedPlaybook") or "").strip()


def _target_url(context: PlaybookExecutionContext) -> str | None:
    plan = dict(context.task_loop.get("plan") or {})
    if plan.get("targetUrl"):
        return str(plan.get("targetUrl"))
    for item in list(context.task_loop.get("factEvidence") or []):
        if isinstance(item, dict) and item.get("url"):
            return str(item.get("url"))
    return None


def _begin_run(context: PlaybookExecutionContext):
    return context.runtime.begin_or_attach_run(
        session_id=context.session_id,
        run_id=context.run_id,
        user_id=context.user_id,
        project_id=context.project_id,
        workspace_id=context.workspace_id,
        workspace_path=context.workspace_path,
        goal=context.goal,
        trigger_source="computer_use_playbook_executor",
        metadata={
            "computer_use_goal": context.goal,
            "taskLoop": {
                "selectedPlaybook": _selected_playbook_id(context.task_loop),
                "status": context.task_loop.get("status"),
            },
            "invocation": dict(context.invocation_metadata or {}),
        },
    )


def _browser_decision(context: PlaybookExecutionContext, target_url: str):
    return context.runtime._browser_lane_decision(
        action_type="type_text",
        action_payload={"app_id": "browser_checkout", "app_name": "browser", "text": target_url},
        app_id="browser_checkout",
    )


def _open_browser_target(context: PlaybookExecutionContext, target_url: str, *, preserve_on_human: bool = False):
    run_handle = _begin_run(context)
    run_handle.emit("computer_use.task_loop.prepared", context.task_loop)
    decision = _browser_decision(context, target_url)
    run_handle.emit("computer_use.playbook.lane_decision", decision.as_dict())
    if not decision.available:
        _finish_run(context, run_handle, status="needs_human_attention", reason=decision.reason or "browser_lane_unavailable")
        raise RuntimeError(decision.reason or "browser_lane_unavailable")
    opened = context.runtime.browser_automation.open_tab(url=target_url, decision=decision)
    try:
        from runtimes.computer_use.browser_session_service import browser_session_service

        workbench_browser = browser_session_service.register_existing_target(
            session_id=context.session_id,
            run_id=context.run_id,
            provider=context.runtime.browser_automation,
            opened=opened,
        )
        run_handle.emit(
            "computer_use.workbench.browser_registered",
            {"browserSessionId": workbench_browser.get("browserSessionId")},
        )
    except Exception as exc:
        run_handle.emit(
            "computer_use.workbench.browser_registration_failed",
            {"errorClass": exc.__class__.__name__},
        )
    context.runtime._record_resource_lease(
        run_handle=run_handle,
        kind="browser_tab",
        resource={
            "targetId": opened.get("targetId"),
            "url": target_url,
            "provider": opened.get("provider"),
            "family": opened.get("family"),
            "targetPort": opened.get("targetPort"),
        },
        cleanup_on_complete=True,
        preserve_on_human_input=preserve_on_human,
        reason=f"{_selected_playbook_id(context.task_loop)}_opened_tab",
    )
    run_handle.emit("computer_use.playbook.opened", {"targetUrl": target_url, "targetId": opened.get("targetId")})
    return opened, decision, run_handle


def _browser_page_state(runtime: Any, opened: dict[str, Any]) -> dict[str, Any]:
    value = runtime.browser_automation._evaluate(
        target_id=str(opened.get("targetId") or ""),
        expression=(
            "(() => ({ url: location.href, title: document.title, text: "
            "(document.body ? document.body.innerText.slice(0, 2000) : ''), "
            "loggedOut: /sign in|log in|登录|登入/i.test(document.body ? document.body.innerText : '') }))()"
        ),
    ).get("value") or {}
    return dict(value or {})


def _finish_run(context: PlaybookExecutionContext, run_handle: Any, *, status: str, reason: str) -> dict[str, Any] | None:
    if run_handle is None:
        return None
    if status in {"succeeded", "needs_human_login", "needs_human_attention", "review_required"}:
        run_handle.transition("completed", reason=reason, node="computer_use_runtime")
        try:
            from erc.run_service import run_service

            run_service.transition_run(run_handle.run_id, status="completed")
        except Exception:
            pass
    else:
        try:
            run_handle.fail(reason, node="computer_use_runtime")
        except Exception:
            pass
    return context.runtime._cleanup_resource_lease(run_handle=run_handle, status=status, reason=reason)


def _playbook_result(
    *,
    status: str,
    canonical_url: str | None,
    opened: dict[str, Any],
    decision: Any,
    pre_state: dict[str, Any],
    action: Any,
    post_state: dict[str, Any],
    resource_lease: dict[str, Any] | None,
    run_handle: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "canonicalUrl": canonical_url,
        "browserTarget": opened,
        "laneDecision": decision.as_dict() if hasattr(decision, "as_dict") else decision,
        "preState": pre_state,
        "action": action,
        "postState": post_state,
        "resourceLease": resource_lease,
        "runId": getattr(run_handle, "run_id", None),
        "sessionId": getattr(run_handle, "session_id", None),
    }


def _execute_browser_open_and_verify(context: PlaybookExecutionContext, *, expected_kind: str) -> dict[str, Any]:
    target_url = _target_url(context) or str((context.playbook_inputs or {}).get("url") or "").strip()
    if not target_url:
        return _human_attention_result(context, reason="canonical_target_not_resolved")
    opened, decision, run_handle = _open_browser_target(context, target_url)
    pre_state = _browser_page_state(context.runtime, opened)
    resource_lease = _finish_run(context, run_handle, status="succeeded", reason=expected_kind)
    return _playbook_result(
        status="succeeded",
        canonical_url=target_url,
        opened=opened,
        decision=decision,
        pre_state=pre_state,
        action={"type": "navigate", "url": target_url},
        post_state=pre_state,
        resource_lease=resource_lease,
        run_handle=run_handle,
    )


def _human_attention_result(context: PlaybookExecutionContext, *, reason: str) -> dict[str, Any]:
    return {
        "status": "needs_human_attention",
        "reason": reason,
        "recommendedNextAction": "ask_user",
        "humanInputRequest": {
            "interactionKind": "ask_user",
            "reason": reason,
            "prompt": "Computer Use 需要更多明确目标或输入后才能安全继续。",
        },
        "runId": context.run_id,
        "sessionId": context.session_id,
    }


def _assess_safety(context: PlaybookExecutionContext, run_handle: Any, action_type: str, payload: dict[str, Any]) -> None:
    if run_handle is None:
        return
    context.runtime._assess_runtime_action_safety(
        run_handle=run_handle,
        action_type=action_type,
        action_payload=payload,
    )


def _form_submit_script(*, fields: dict[str, Any], submit_selector: str, submit_text: str) -> str:
    import json

    return (
        "(() => {\n"
        f"  const fields = {json.dumps(fields, ensure_ascii=False)};\n"
        f"  const submitSelector = {json.dumps(submit_selector, ensure_ascii=False)};\n"
        f"  const submitText = {json.dumps(submit_text, ensure_ascii=False)}.toLowerCase();\n"
        "  const norm = (v) => String(v || '').trim().toLowerCase();\n"
        "  const editable = Array.from(document.querySelectorAll('input, textarea, select, [contenteditable=\"true\"]'));\n"
        "  const filled = [];\n"
        "  for (const [key, value] of Object.entries(fields)) {\n"
        "    const token = norm(key);\n"
        "    const target = editable.find((el) => [el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.getAttribute('title')].some((v) => norm(v).includes(token))) || null;\n"
        "    if (!target) return { ok: false, reason: 'field_not_found', field: key, filled };\n"
        "    target.focus();\n"
        "    if (target.isContentEditable) target.textContent = String(value);\n"
        "    else target.value = String(value);\n"
        "    target.dispatchEvent(new Event('input', { bubbles: true }));\n"
        "    target.dispatchEvent(new Event('change', { bubbles: true }));\n"
        "    filled.push(key);\n"
        "  }\n"
        "  const buttons = Array.from(document.querySelectorAll('button, input[type=\"submit\"], [role=\"button\"]'));\n"
        "  let submit = submitSelector ? document.querySelector(submitSelector) : null;\n"
        "  if (!submit) submit = buttons.find((el) => norm(el.textContent || el.value || el.getAttribute('aria-label')).includes(submitText)) || buttons[0] || null;\n"
        "  if (!submit) return { ok: false, reason: 'submit_not_found', filled };\n"
        "  submit.click();\n"
        "  return { ok: true, filled, submitText: String(submit.textContent || submit.value || '').slice(0, 120) };\n"
        "})()"
    )


def _toggle_script(*, selector: str, label: str, desired: Any) -> str:
    import json

    desired_json = "null" if desired is None else json.dumps(bool(desired))
    return (
        "(() => {\n"
        f"  const selector = {json.dumps(selector, ensure_ascii=False)};\n"
        f"  const label = {json.dumps(label, ensure_ascii=False)}.toLowerCase();\n"
        f"  const desired = {desired_json};\n"
        "  const norm = (v) => String(v || '').trim().toLowerCase();\n"
        "  const candidates = Array.from(document.querySelectorAll('button, input[type=\"checkbox\"], [role=\"switch\"], [role=\"checkbox\"], [aria-checked]'));\n"
        "  let target = selector ? document.querySelector(selector) : null;\n"
        "  if (!target && label) target = candidates.find((el) => norm(el.textContent || el.getAttribute('aria-label') || el.id || el.name).includes(label)) || null;\n"
        "  if (!target) return { ok: false, reason: 'toggle_not_found' };\n"
        "  const before = target.checked ?? (String(target.getAttribute('aria-checked')).toLowerCase() === 'true');\n"
        "  if (desired !== null && before === desired) return { ok: true, alreadyDesired: true, before, after: before };\n"
        "  target.click();\n"
        "  const after = target.checked ?? (String(target.getAttribute('aria-checked')).toLowerCase() === 'true');\n"
        "  return { ok: true, changed: before !== after, before, after };\n"
        "})()"
    )


def _page_contains(runtime: Any, opened: dict[str, Any], token: str) -> bool:
    value = runtime.browser_automation._evaluate(
        target_id=str(opened.get("targetId") or ""),
        expression=f"(() => (document.body ? document.body.innerText : '').includes({token!r}))()",
    ).get("value")
    return bool(value)


def _same_domain(a: str, b: str) -> bool:
    try:
        return urlparse(a).netloc.lower().removeprefix("www.") == urlparse(b).netloc.lower().removeprefix("www.")
    except Exception:
        return False


def _looks_destructive(value: Any) -> bool:
    return bool(re.search(r"delete|remove|destroy|pay|purchase|删除|移除|支付|付款", str(value or ""), re.I))


def _suffix_from_url(url: str) -> str:
    path = urlparse(url).path
    return Path(path).suffix.lower()


def _download_to_tmp(url: str) -> Path:
    suffix = _suffix_from_url(url) or ".bin"
    fd, raw_path = tempfile.mkstemp(prefix="v8-computer-download-", suffix=suffix)
    os.close(fd)
    path = Path(raw_path)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    path.write_bytes(response.content)
    if not path.suffix or path.suffix == ".bin":
        guessed = mimetypes.guess_extension(response.headers.get("content-type", "").split(";")[0].strip())
        if guessed:
            next_path = path.with_suffix(guessed)
            path.replace(next_path)
            path = next_path
    return path
