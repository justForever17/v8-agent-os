from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.database import DatabaseManager
from core.tools.native import browser as native
from erc.runtime_context import bind_runtime_context
from runtimes.computer_use import browser_session_service as session_module


class Provider:
    def __init__(self):
        self.calls = []
        self.targets = []
        self.closed = []
    def configure(self, _config): pass
    def open_agent_page(self, *, url):
        target = f"target-{len(self.targets) + 1}"
        self.targets.append({"targetId": target, "url": url})
        return {"targetId": target, "targetPort": 19001, "managedHeadless": True}
    def workbench_request_json(self, method, path, **_kwargs):
        assert method == "GET" and path == "/targets", "Agent cannot borrow user control for observation/input"
        return {"targets": self.targets}
    def agent_request_json(self, method, path, *, params, body, **_kwargs):
        self.calls.append((method, path, params, body))
        if path == "/agent/observe":
            return {"observationId": "observation-fixture", "url": "https://example.test", "title": "Fixture", "accessibility": "- heading Fixture\n  - button Save", "dom": [], "errors": []}
        if body.get("action") == "inspect":
            return {"target": {"tag": "button", "text": "Save", "attributes": {}}, "url": "https://example.test"}
        if path == "/agent/close":
            self.closed.append(params["target"])
            return {"closed": True}
        return {"applied": True, "url": "https://example.test"}
    def close_tab(self, *, target_id, **_kwargs):
        self.closed.append(target_id)
        return {"closed": True}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "browser-test.db")
    database.create_or_update_session("s1", "Fixture", user_id="u1")
    database.create_or_update_session("s2", "Other", user_id="u2")
    service = session_module.BrowserSessionService()
    provider = Provider()
    monkeypatch.setattr(native, "db", database)
    monkeypatch.setattr(native, "browser_session_service", service)
    monkeypatch.setattr(native, "agent_browser_automation", provider)
    monkeypatch.setattr(session_module, "emit_workbench_document_event", lambda *_a, **_k: {})
    monkeypatch.setattr(native, "_enforce_safety_decision", lambda *_a, **_k: (True, None))
    from core import tool_surface
    monkeypatch.setattr(tool_surface, "record_raw_observation", lambda **_kwargs: "toolobs://browser")
    return provider, service


async def invoke(**kwargs):
    result = await native.browser_broker.ainvoke({"type": "tool_call", "id": "call-browser", "name": "browser_broker", "args": kwargs})
    return result.content


def opened(service, provider, session="s1"):
    return service.register_existing_target(session_id=session, provider=provider, opened=provider.open_agent_page(url="https://example.test"))


def test_real_tool_schema_has_no_eval_or_credential_escape_hatches():
    from langchain_core.utils.function_calling import convert_to_openai_tool
    schema = convert_to_openai_tool(native.browser_broker)["function"]["parameters"]["properties"]
    assert schema["action"]["enum"] == ["open", "observe", "click", "fill", "press", "scroll", "close", "media"]
    assert {"selector", "role", "name", "observation_id", "browser_session_id", "page_id"} <= set(schema)
    assert not {"javascript", "eval", "cookies", "headers", "force", "target_port", "session_id"} & set(schema)


def test_open_observe_action_close_reuses_canonical_service(environment):
    provider, service = environment
    with bind_runtime_context(session_id="s1", user_id="u1"):
        result = asyncio.run(invoke(action="open", url="https://example.test"))
        assert "Accessibility tree" in result and "observation-fixture" in result
        browser_id = next(iter(service._sessions))
        page_id = service.public_status(browser_id, refresh=False)["currentPageId"]
        acted = asyncio.run(invoke(action="click", browser_session_id=browser_id, page_id=page_id, observation_id="observation-fixture", role="button", name="Save"))
        assert "Action applied" in acted
        closed = asyncio.run(invoke(action="close", browser_session_id=browser_id, page_id=page_id, observation_id="observation-fixture"))
        assert "completed" in closed and provider.closed == ["target-1"]
    assert [call[1] for call in provider.calls] == ["/agent/observe", "/agent/action", "/agent/action", "/agent/close"]


def test_cross_session_and_workbench_neighbor_page_are_not_agent_permissions(environment):
    provider, service = environment
    first = opened(service, provider)
    second = opened(service, provider, "s2")
    status = service.public_status(first["browserSessionId"])
    neighbor = next(row["pageId"] for row in status["pages"] if row["pageId"] != first["currentPageId"])
    with bind_runtime_context(session_id="s1", user_id="u1"):
        for browser_id, page_id in [(second["browserSessionId"], ""), (first["browserSessionId"], neighbor)]:
            result = asyncio.run(invoke(action="observe", browser_session_id=browser_id, page_id=page_id))
            assert "scope_mismatch" in result
    assert provider.calls == []


def test_user_takeover_and_reobserve_fence_are_retained(environment):
    provider, service = environment
    first = opened(service, provider)
    browser_id = first["browserSessionId"]
    service.take_control(browser_id, "user-client")
    with bind_runtime_context(session_id="s1", user_id="u1"):
        arguments = dict(action="click", browser_session_id=browser_id, observation_id="observation-fixture", selector="button")
        assert "browser_user_control_active" in asyncio.run(invoke(**arguments))
        service.release_control(browser_id, "user-client")
        assert "browser_reobserve_required" in asyncio.run(invoke(**arguments))
        assert provider.calls == []
        asyncio.run(invoke(action="observe", browser_session_id=browser_id))
        assert "Action applied" in asyncio.run(invoke(**arguments))


def test_safety_denial_keeps_inspection_but_prevents_input(environment, monkeypatch):
    provider, service = environment
    first = opened(service, provider)
    monkeypatch.setattr(native, "_enforce_safety_decision", lambda *_a, **_k: (False, "denied"))
    with bind_runtime_context(session_id="s1", user_id="u1"):
        result = asyncio.run(invoke(action="click", browser_session_id=first["browserSessionId"], observation_id="observation-fixture", selector="button"))
    assert "browser_action_blocked" in result
    assert [call[3]["action"] for call in provider.calls] == ["inspect"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///private", "https://user:secret@example.test"])
def test_open_rejects_unsafe_url_before_browser_start(environment, url):
    with bind_runtime_context(session_id="s1", user_id="u1"):
        assert "invalid_browser_url" in asyncio.run(invoke(action="open", url=url))
    assert environment[0].targets == []


def test_cancel_during_start_closes_only_the_late_new_page(environment, monkeypatch):
    provider, _service = environment
    started, proceed = threading.Event(), threading.Event()
    original = provider.open_agent_page
    def delayed_open(**kwargs):
        started.set()
        assert proceed.wait(5)
        return original(**kwargs)
    monkeypatch.setattr(provider, "open_agent_page", delayed_open)
    async def scenario():
        task = asyncio.create_task(invoke(action="open", url="https://example.test"))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        proceed.set()
    with bind_runtime_context(session_id="s1", user_id="u1"):
        asyncio.run(scenario())
    assert provider.closed == ["target-1"] and provider.calls == []


def test_cancel_while_queued_prevents_late_dispatch(environment):
    provider, service = environment
    first = opened(service, provider)
    def cancelled(): raise session_module.BrowserSessionError("browser_operation_cancelled", "cancelled")
    with pytest.raises(session_module.BrowserSessionError):
        service.agent_request(session_id="s1", browser_session_id=first["browserSessionId"], action="click", check_cancelled=cancelled)
    assert provider.calls == []


def test_session_input_lock_does_not_block_other_session_or_observation(environment):
    provider, service = environment
    first = opened(service, provider)
    second = opened(service, provider, "s2")
    item = service._session(first["browserSessionId"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        with item.agent_operation_lock:
            other = pool.submit(service.agent_request, session_id="s2", browser_session_id=second["browserSessionId"], action="click", body={"action": "click"})
            observed = pool.submit(service.agent_request, session_id="s1", browser_session_id=first["browserSessionId"], action="observe")
            assert other.result(timeout=2)["applied"] is True
            assert observed.result(timeout=2)["observationId"] == "observation-fixture"


def test_cancel_after_waiting_for_session_lock_does_not_issue_late_request(environment):
    provider, service = environment
    first = opened(service, provider)
    item = service._session(first["browserSessionId"])
    cancelled = threading.Event()
    def check():
        if cancelled.is_set(): raise session_module.BrowserSessionError("browser_operation_cancelled", "cancelled")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with item.agent_operation_lock:
            pending = pool.submit(service.agent_request, session_id="s1", browser_session_id=first["browserSessionId"],
                                  action="click", body={"action": "click"}, check_cancelled=check)
            cancelled.set()
        with pytest.raises(session_module.BrowserSessionError):
            pending.result(timeout=2)
    assert provider.calls == []


def test_proxy_error_is_visible_and_never_claims_success(environment, monkeypatch):
    provider, service = environment
    first = opened(service, provider)
    def failure(*_args, **_kwargs):
        raise session_module.BrowserSessionError("browser_proxy_error", "locator_ambiguous")
    monkeypatch.setattr(provider, "agent_request_json", failure)
    with bind_runtime_context(session_id="s1", user_id="u1"):
        result = asyncio.run(invoke(action="click", browser_session_id=first["browserSessionId"], observation_id="old", selector="button"))
    assert "Browser click: failed" in result and "locator_ambiguous" in result
    assert "Action applied" not in result
