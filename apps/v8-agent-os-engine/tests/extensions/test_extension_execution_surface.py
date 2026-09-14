from __future__ import annotations

from types import SimpleNamespace
import pytest

import runtimes.extensions.runtime as extensions_runtime_module
from runtimes.extensions.runtime import ExtensionsRuntimeService


@pytest.mark.parametrize("surface", ["runtime_route_compiler", "runtime_route_compiler_correction", ""])
def test_internal_compiler_event_preserves_tool_status_without_prose_preview(surface) -> None:
    emitted = []
    service = object.__new__(ExtensionsRuntimeService)
    service._emit = lambda topic, payload, *, node: emitted.append(payload)
    response = SimpleNamespace(
        content="internal route explanation" if surface else "visible explanation",
        additional_kwargs={"v8_internal_model_surface": surface},
        tool_calls=[{"name": "runtime_broker", "args": {"mode": "route"}}],
    )
    service.emit_execution_completed(response=response)
    payload = emitted[0]
    assert payload["toolNames"] == ["runtime_broker"]
    assert payload["toolResultPending"] is True
    if surface:
        assert "messagePreview" not in payload
        assert payload["activitySummary"]
        assert response.content == "internal route explanation"
    else:
        assert payload["messagePreview"] == "visible explanation"


def test_tool_call_only_extension_event_is_not_reported_as_empty_message_preview() -> None:
    emitted: list[tuple[str, dict, str]] = []
    service = object.__new__(ExtensionsRuntimeService)
    service._emit = lambda topic, payload, *, node: emitted.append((topic, payload, node))  # type: ignore[method-assign]

    service.emit_execution_completed(
        response=SimpleNamespace(
            content="",
            tool_calls=[{"name": "research_broker", "args": {"mode": "run"}}],
        )
    )

    topic, payload, node = emitted[0]
    assert topic == "extension.execution.completed"
    assert node == "execution_completed"
    assert payload["activityKind"] == "tool_calls_issued"
    assert payload["toolResultPending"] is True
    assert payload["toolNames"] == ["research_broker"]
    assert "messagePreview" not in payload
    assert "agent-visible knowledge arrives in the matching tool result" in payload["activitySummary"]


def test_cancelled_run_suppresses_late_extension_progress(monkeypatch) -> None:
    service = object.__new__(ExtensionsRuntimeService)
    service._resolve_event_context = lambda: {  # type: ignore[method-assign]
        "session_id": "session-cancelled",
        "run_id": "run-cancelled",
    }
    monkeypatch.setattr(
        extensions_runtime_module.db,
        "get_run_record",
        lambda run_id: {"id": run_id, "status": "cancelled"},
    )
    monkeypatch.setattr(
        extensions_runtime_module.event_bus,
        "create_emitter",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("cancelled run must not emit extension progress")),
    )

    service._emit("extension.execution.completed", {"activityKind": "model_text"}, node="execution_completed")


def test_health_projection_does_not_require_removed_plugin_host_silk_state() -> None:
    service = object.__new__(ExtensionsRuntimeService)
    runtime_state = {
        "phase": "ready",
        "startupState": "ready",
        "snapshotFreshness": "live",
        "lastRefreshAt": "2026-07-11T00:00:00Z",
        "lastRefreshError": None,
        "skillsStartupState": "ready",
        "mcpStartupState": "ready",
        "catalogSummary": {},
        "healthSummary": {},
        "blockedReasons": [],
        "degradedReasons": [],
        "controls": {},
    }
    service._build_runtime_state = lambda: runtime_state  # type: ignore[method-assign]

    payload = service._decorate_health({"ok": True})

    assert payload["runtime"] is runtime_state
    assert "silk" not in payload
