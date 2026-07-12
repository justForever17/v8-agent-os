from __future__ import annotations

import pytest

from runtimes.computer_use import browser_session_service as module
from runtimes.computer_use.browser_automation import BrowserAutomationProvider
from runtimes.computer_use.runtime import ComputerUseRuntime


class _Provider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict | None]] = []
        self.open_calls = 0
        self.targets = [
            {"targetId": "cdp-target-1", "title": "Example", "url": "https://example.com"},
        ]

    def open_workbench_browser(self, **_kwargs):
        self.open_calls += 1
        return {
            "targetId": "cdp-target-1",
            "targetPort": 9222,
            "browserKind": "chrome",
            "managedHeadless": True,
            "externalWindow": False,
        }

    def workbench_request_json(self, method, path, *, params=None, body=None, **_kwargs):
        self.calls.append((method, path, dict(params or {}), dict(body or {}) if isinstance(body, dict) else None))
        if path == "/targets":
            return {"targets": list(self.targets)}
        if path == "/new":
            self.targets.append({"targetId": "cdp-target-2", "title": "New", "url": params["url"]})
            return self.targets[-1]
        if path == "/close":
            target_id = params["target"]
            self.targets = [item for item in self.targets if item["targetId"] != target_id]
            return {"closed": True}
        if path == "/dispatch":
            return {"ok": True}
        return {}


@pytest.fixture()
def service(monkeypatch: pytest.MonkeyPatch):
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        module,
        "emit_workbench_document_event",
        lambda topic, **kwargs: emitted.append((topic, kwargs)) or {"topic": topic},
    )
    instance = module.BrowserSessionService()
    return instance, emitted


def _create(service: module.BrowserSessionService, provider: _Provider):
    return service.create_session(
        session_id="session-1",
        provider=provider,
        url="https://example.com",
    )


def test_public_status_uses_opaque_ids_and_emits_workbench_document(service):
    instance, emitted = service
    provider = _Provider()

    status = _create(instance, provider)

    assert status["browserSessionId"].startswith("browser_")
    assert status["currentPageId"].startswith("page_")
    assert status["pages"][0]["pageId"].startswith("page_")
    assert "cdp-target-1" not in repr(status)
    assert "9222" not in repr(status)
    assert emitted[0][0] == "workbench.document.opened"
    document = emitted[0][1]["document"]
    assert document["subjectRef"]["browserSessionId"] == status["browserSessionId"]
    assert document["lifecycle"] == "runtime"


def test_create_session_reuses_ready_browser_for_same_v8_session(service):
    instance, emitted = service
    provider = _Provider()

    first = _create(instance, provider)
    second = _create(instance, provider)

    assert second["browserSessionId"] == first["browserSessionId"]
    assert second["reused"] is True
    assert provider.open_calls == 1
    assert len(emitted) == 1


def test_ws_ticket_is_one_time_and_bound_to_browser_session(service):
    instance, _emitted = service
    status = _create(instance, _Provider())
    browser_session_id = status["browserSessionId"]
    ticket = instance.issue_ws_ticket(browser_session_id)

    consumed = instance.consume_ws_ticket(browser_session_id, ticket["ticket"])
    assert consumed.client_id == ticket["clientId"]
    with pytest.raises(module.BrowserSessionError, match="invalid or expired"):
        instance.consume_ws_ticket(browser_session_id, ticket["ticket"])


def test_user_control_blocks_agent_until_agent_reobserves(service):
    instance, _emitted = service
    status = _create(instance, _Provider())
    browser_session_id = status["browserSessionId"]

    instance.take_control(browser_session_id, "client-1")
    with pytest.raises(module.BrowserSessionError) as active:
        instance.assert_agent_control_available_for_target("cdp-target-1")
    assert active.value.code == "browser_user_control_active"

    instance.release_control(browser_session_id, "client-1")
    with pytest.raises(module.BrowserSessionError) as stale:
        instance.assert_agent_control_available_for_target("cdp-target-1")
    assert stale.value.code == "browser_reobserve_required"

    instance.note_agent_observation("cdp-target-1")
    instance.assert_agent_control_available_for_target("cdp-target-1")


def test_structured_commands_require_control_and_reject_unsafe_input(service):
    instance, _emitted = service
    provider = _Provider()
    status = _create(instance, provider)
    browser_session_id = status["browserSessionId"]
    page_id = status["currentPageId"]

    blocked = instance.handle_command(
        browser_session_id,
        "client-1",
        {"action": "navigate", "pageId": page_id, "url": "https://example.org"},
    )
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "control_required"

    instance.take_control(browser_session_id, "client-1")
    unsafe = instance.handle_command(
        browser_session_id,
        "client-1",
        {"action": "navigate", "pageId": page_id, "url": "javascript:alert(1)"},
    )
    assert unsafe["ok"] is False
    assert unsafe["error"]["code"] == "invalid_url"

    too_long = instance.handle_command(
        browser_session_id,
        "client-1",
        {"action": "insertText", "pageId": page_id, "text": "x" * (module.MAX_TEXT_INPUT_CHARS + 1)},
    )
    assert too_long["ok"] is False
    assert too_long["error"]["code"] == "invalid_input"

    valid = instance.handle_command(
        browser_session_id,
        "client-1",
        {"action": "insertText", "pageId": page_id, "text": "中文输入"},
    )
    assert valid["ok"] is True
    assert "中文输入" not in repr(valid)
    dispatch_calls = [call for call in provider.calls if call[1] == "/dispatch"]
    assert dispatch_calls[-1][3] == {"action": "insertText", "text": "中文输入"}


def test_page_ids_remain_stable_across_refresh_and_new_tab(service):
    instance, _emitted = service
    provider = _Provider()
    status = _create(instance, provider)
    browser_session_id = status["browserSessionId"]
    first_page_id = status["currentPageId"]

    refreshed = instance.public_status(browser_session_id)
    assert refreshed["pages"][0]["pageId"] == first_page_id

    instance.take_control(browser_session_id, "client-1")
    created = instance.handle_command(
        browser_session_id,
        "client-1",
        {"action": "new_tab", "url": "https://example.org"},
    )
    assert created["ok"] is True
    assert len(created["result"]["pages"]) == 2
    assert all("cdp-target" not in item["pageId"] for item in created["result"]["pages"])


def test_adaptive_viewport_is_bounded_and_forwarded_as_typed_command(service):
    instance, _emitted = service
    provider = _Provider()
    status = _create(instance, provider)
    browser_session_id = status["browserSessionId"]
    page_id = status["currentPageId"]
    instance.take_control(browser_session_id, "client-1")

    resized = instance.handle_command(
        browser_session_id,
        "client-1",
        {"action": "set_viewport", "pageId": page_id, "width": 412.7, "height": 860.2},
    )

    assert resized["ok"] is True
    dispatch_calls = [call for call in provider.calls if call[1] == "/dispatch"]
    assert dispatch_calls[-1][3] == {"action": "set_viewport", "width": 412, "height": 860}

    rejected = instance.handle_command(
        browser_session_id,
        "client-1",
        {"action": "set_viewport", "pageId": page_id, "width": 120, "height": 200},
    )
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "invalid_input"


def test_delete_marks_document_unavailable_without_closing_page(service):
    instance, emitted = service
    provider = _Provider()
    status = _create(instance, provider)

    deleted = instance.delete_session(status["browserSessionId"])

    assert deleted["status"] == "unavailable"
    assert emitted[-1][0] == "workbench.document.unavailable"
    assert not [call for call in provider.calls if call[1] == "/close"]


def test_existing_computer_use_target_registers_once_in_background(service):
    instance, emitted = service
    provider = _Provider()
    opened = provider.open_workbench_browser()

    first = instance.register_existing_target(
        session_id="session-1",
        run_id="run-1",
        provider=provider,
        opened=opened,
    )
    second = instance.register_existing_target(
        session_id="session-1",
        run_id="run-1",
        provider=provider,
        opened=opened,
    )

    assert first["browserSessionId"] == second["browserSessionId"]
    assert len(emitted) == 1
    assert emitted[0][1]["focus_requested"] is False
    assert emitted[0][1]["user_initiated"] is False


def test_browser_automation_cannot_fall_through_user_control_boundary(monkeypatch: pytest.MonkeyPatch):
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        module,
        "emit_workbench_document_event",
        lambda topic, **kwargs: emitted.append((topic, kwargs)) or {"topic": topic},
    )
    module.browser_session_service.reset_for_tests()
    provider = _Provider()
    status = module.browser_session_service.register_existing_target(
        session_id="session-1",
        provider=provider,
        opened=provider.open_workbench_browser(),
    )
    module.browser_session_service.take_control(status["browserSessionId"], "client-1")

    automation = BrowserAutomationProvider()
    with pytest.raises(module.BrowserSessionError) as blocked:
        automation._request_json(
            "POST",
            "/dispatch",
            params={"target": "cdp-target-1"},
            body={"action": "reload"},
        )
    assert blocked.value.code == "browser_user_control_active"

    module.browser_session_service.release_control(status["browserSessionId"], "client-1")
    with pytest.raises(module.BrowserSessionError) as stale:
        automation._request_json(
            "POST",
            "/dispatch",
            params={"target": "cdp-target-1"},
            body={"action": "reload"},
        )
    assert stale.value.code == "browser_reobserve_required"
    module.browser_session_service.reset_for_tests()


def test_workbench_browser_provider_refreshes_config_before_first_launch():
    configured: list[dict] = []

    class _BrowserProvider:
        def configure(self, config):
            configured.append(dict(config or {}))

    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)
    runtime._runtime_ready = True
    runtime.browser_automation = _BrowserProvider()
    runtime._computer_use_config = lambda: {"browserLane": {"enabled": True}}

    provider = runtime.workbench_browser_provider()

    assert provider is runtime.browser_automation
    assert configured == [{"browserLane": {"enabled": True}}]
