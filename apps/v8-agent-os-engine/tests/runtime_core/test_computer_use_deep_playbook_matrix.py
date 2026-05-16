from __future__ import annotations

import threading
from pathlib import Path

from runtimes.computer_use.browser_automation import BrowserLaneDecision
from runtimes.computer_use.fact_resolver import classify_goal, resolve_goal_facts
from runtimes.computer_use.playbook_executors import (
    PlaybookExecutionContext,
    create_default_playbook_executor_registry,
)
from runtimes.computer_use.real_host_matrix import build_real_host_matrix_payload, merge_latest_real_host_matrix
from runtimes.computer_use.task_loop import prepare_task_loop


class _FakeRunHandle:
    run_id = "run-playbook"
    session_id = "session-playbook"

    def __init__(self):
        self.events = []
        self.transitions = []

    def emit(self, event_type: str, payload: dict):
        self.events.append((event_type, payload))

    def transition(self, status: str, reason: str | None = None, node: str | None = None):
        self.transitions.append((status, reason, node))

    def fail(self, reason: str, node: str | None = None):
        self.transitions.append(("failed", reason, node))


class _FakeBrowser:
    def __init__(self, *, state: dict | None = None):
        self.state = state or {"url": "https://example.com/docs", "title": "Docs", "text": "Docs page"}
        self.opened = []
        self.closed = []
        self.set_files_calls = []

    def open_tab(self, *, url: str, decision: BrowserLaneDecision):
        target = {"targetId": "target-1", "url": url, "family": decision.family, "targetPort": decision.target_port}
        self.opened.append(target)
        return target

    def close_tab(self, *, target_id: str, **_kwargs):
        self.closed.append(target_id)
        return {"closed": True, "targetId": target_id}

    def _evaluate(self, *, target_id: str, expression: str):
        if "document.querySelectorAll('button" in expression:
            return {"value": {"ok": True, "changed": True, "before": False, "after": True}}
        if "const fields =" in expression:
            return {"value": {"ok": True, "filled": ["email"], "submitText": "Submit"}}
        if "includes(" in expression:
            return {"value": True}
        return {"value": self.state}

    def set_files(self, *, payload: dict, decision: BrowserLaneDecision):
        self.set_files_calls.append(payload)
        return {"status": "succeeded", "metadata": {"browserResult": {"fileCount": len(payload.get("file_paths") or [])}}}


class _FakeRuntime:
    def __init__(self):
        self.browser_automation = _FakeBrowser()
        self._resource_leases = {}
        self._resource_lease_lock = threading.Lock()
        self.run_handle = _FakeRunHandle()
        self.safety_calls = []

    def begin_or_attach_run(self, **_kwargs):
        return self.run_handle

    def _browser_lane_decision(self, **_kwargs):
        return BrowserLaneDecision(
            enabled=True,
            available=True,
            family="chromium",
            reason="fake_browser",
            target_port=9222,
        )

    def _record_resource_lease(self, **_kwargs):
        return None

    def _cleanup_resource_lease(self, *, run_handle, status: str, reason: str):
        return {"cleanup": {"status": "completed" if status == "succeeded" else "skipped", "reason": reason}}

    def _human_input_request_payload(self, **kwargs):
        return {"interactionKind": "ask_user", **kwargs}

    def _assess_runtime_action_safety(self, **kwargs):
        self.safety_calls.append(kwargs)
        return {"verdict": "audit"}

    def execute_github_star_playbook(self, **_kwargs):
        return {"status": "succeeded", "action": "already_starred"}


def _execute(playbook_id: str, *, goal: str, inputs: dict | None = None, target_url: str | None = "https://example.com/docs"):
    task_loop = {
        "domain": {"selectedPlaybook": playbook_id},
        "status": "ready",
        "factEvidence": ([{"url": target_url, "kind": "canonical_url", "source": "test"}] if target_url else []),
        "plan": {"targetUrl": target_url, "selectedPlaybook": playbook_id},
    }
    context = PlaybookExecutionContext(
        runtime=_FakeRuntime(),
        task_loop=task_loop,
        goal=goal,
        playbook_inputs=dict(inputs or {}),
    )
    return create_default_playbook_executor_registry().execute(context)


def test_fact_resolver_routes_deep_playbooks_to_matching_operations():
    assert classify_goal("打开 https://example.com/docs")["operation"] == "search_and_open_result"
    assert classify_goal("登录 GitHub")["operation"] == "login_gate"
    assert classify_goal("下载 https://example.com/readme.pdf")["operation"] == "download_and_open"

    search = resolve_goal_facts(
        "搜索 V8 Agent OS docs 并打开",
        web_searcher=lambda _query: {"results": [{"url": "https://example.com/docs"}]},
    )
    assert search.status == "resolved"
    assert search.canonicalTarget["url"] == "https://example.com/docs"


def test_executor_registry_handles_all_runtime_native_deep_playbooks(tmp_path: Path):
    registry = create_default_playbook_executor_registry()
    for playbook_id in {
        "github.star_repository",
        "web.search_and_open_result",
        "browser.login_gate",
        "web.form_submit",
        "web.file_upload",
        "download_and_open",
        "settings.toggle_option",
    }:
        assert registry.get(playbook_id) is not None

    upload_file = tmp_path / "upload.txt"
    upload_file.write_text("ok", encoding="utf-8")
    assert _execute("web.search_and_open_result", goal="打开文档")["status"] == "succeeded"
    assert _execute("browser.login_gate", goal="登录")["recommendedNextAction"] == "ask_user"
    assert _execute("web.form_submit", goal="提交表单", inputs={"fields": {"email": "a@example.com"}})["status"] == "succeeded"
    assert _execute("web.file_upload", goal="上传", inputs={"selector": "input[type=file]", "filePath": str(upload_file)})["status"] == "succeeded"
    assert _execute("settings.toggle_option", goal="开启设置", inputs={"label": "Enable"})["status"] == "succeeded"


def test_download_and_open_executable_requires_review_without_fetching():
    result = _execute("download_and_open", goal="下载安装包", target_url="https://example.com/tool.exe")

    assert result["status"] == "review_required"
    assert result["recommendedNextAction"] == "approval"


def test_task_loop_selects_generic_runtime_native_playbooks():
    loop = prepare_task_loop("打开 https://example.com/docs", browser_decision={"available": True}).as_dict()

    assert loop["domain"]["selectedPlaybook"] == "web.search_and_open_result"
    assert loop["status"] == "ready"
    assert loop["plan"]["targetUrl"] == "https://example.com/docs"


def test_real_host_matrix_keeps_input_blocked_without_allow_input():
    payload = build_real_host_matrix_payload(
        real_host=True,
        allow_input=False,
        probe_results={"window_enumeration": {"status": "real_host_passed", "count": 1}},
    )
    current = payload["matrix"]["currentPlatform"]
    checks = {item["key"]: item for item in payload["matrix"]["platforms"][current]["checks"]}

    assert checks["window_enumeration"]["status"] == "real_host_passed"
    assert checks["click"]["status"] == "blocked_by_permission"
    assert checks["type_text"]["blockingReason"] == "allow_input_required"


def test_merge_latest_real_host_matrix_without_existing_latest(monkeypatch, tmp_path: Path):
    import runtimes.computer_use.real_host_matrix as matrix_module

    monkeypatch.setattr(matrix_module, "MATRIX_ROOT", tmp_path)
    monkeypatch.setattr(matrix_module, "LATEST_PATH", tmp_path / "latest.json")
    merged = merge_latest_real_host_matrix({"platforms": {}, "currentPlatform": "windows"})

    assert merged["latestRealHostMatrix"]["exists"] is False
