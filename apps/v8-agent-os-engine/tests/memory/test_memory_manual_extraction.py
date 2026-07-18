from __future__ import annotations

import sys
from types import SimpleNamespace

from agents import memory_agent as memory_agent_module
from agents.runners.maintenance_runner import memory_agent_runner
from core.memory_extraction_policy import (
    normalize_memory_extraction_config,
    resolve_memory_extraction_mode,
)
import core.memory_extraction_service as extraction_service_module
from core.memory_extraction_service import MemoryExtractionService
import core.terminal_post_run as terminal_post_run_module
from core.terminal_post_run import TerminalPostRunService


def test_memory_extraction_mode_normalizes_legacy_switch() -> None:
    assert resolve_memory_extraction_mode({}) == "auto"
    assert resolve_memory_extraction_mode({"extraction_enabled": True}) == "auto"
    assert resolve_memory_extraction_mode({"extraction_enabled": False}) == "manual"
    assert normalize_memory_extraction_config({"extraction_mode": "manual"}) == {
        "extraction_mode": "manual",
        "extraction_enabled": False,
    }


def test_automatic_runner_skips_manual_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        "agents.runners.maintenance_runner.storage.get_memory_config",
        lambda: {"extraction_mode": "manual", "extraction_enabled": False},
    )

    result = memory_agent_runner.run_session_extraction(
        "session-manual",
        trigger_source="terminal:test",
    )

    assert result["result"]["status"] == "skipped"
    assert result["result"]["reason"] == "manual_mode"


def test_memory_agent_manual_call_bypasses_disabled_automatic_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        memory_agent_module,
        "_load_memory_policy",
        lambda: {"extraction_enabled": False},
    )
    monkeypatch.setattr(memory_agent_module.db, "get_messages", lambda _session_id: [])
    monkeypatch.setattr(
        memory_agent_module,
        "_build_canonical_session_transcript",
        lambda _session_id, _messages: {
            "entries": [],
            "source": "durable_messages",
            "latest_seq": 0,
            "durable_message_count": 0,
            "runtime_event_count": 0,
            "user_message_count": 0,
        },
    )

    result = memory_agent_module.analyze_session_memory(
        "session-manual",
        trigger_source="manual:composer",
        manual=True,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "no_messages"


def test_terminal_scheduler_does_not_start_memory_worker_in_manual_mode(monkeypatch) -> None:
    starts: list[str] = []
    monkeypatch.setattr(
        terminal_post_run_module.storage,
        "get_memory_config",
        lambda: {"extraction_mode": "manual", "extraction_enabled": False},
    )
    monkeypatch.setattr(
        terminal_post_run_module.threading,
        "Thread",
        lambda **kwargs: SimpleNamespace(start=lambda: starts.append(str(kwargs.get("name") or ""))),
    )

    TerminalPostRunService()._schedule_memory_extraction(
        session_id="session-manual",
        run_id="run-terminal",
        source_component="test",
    )

    assert starts == []


def test_manual_scheduler_bypasses_manual_mode_and_emits_terminal_event(monkeypatch) -> None:
    events: list[dict] = []
    calls: list[dict] = []

    class FakeDb:
        @staticmethod
        def list_run_records(*, session_id, run_type=None, limit=20):
            if run_type == "chat":
                return []
            return [{"id": "run-terminal", "status": "completed"}]

        @staticmethod
        def add_runtime_event(event):
            events.append(event)

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    fake_runner = SimpleNamespace(
        run_session_extraction=lambda **kwargs: (
            calls.append(kwargs)
            or {
                "run_id": "run-memory",
                "result": {
                    "status": "completed",
                    "persisted_preference_count": 1,
                    "persisted_knowledge_count": 2,
                },
            }
        )
    )

    monkeypatch.setattr(extraction_service_module, "db", FakeDb())
    monkeypatch.setattr(extraction_service_module.threading, "Thread", ImmediateThread)
    monkeypatch.setitem(
        sys.modules,
        "agents.runners.maintenance_runner",
        SimpleNamespace(memory_agent_runner=fake_runner),
    )

    result = MemoryExtractionService().schedule_manual(
        session_id="session-manual",
        user_id="user@example.com",
    )

    assert result["accepted"] is True
    assert calls[0]["manual"] is True
    assert calls[0]["parent_run_id"] == "run-terminal"
    assert [event["topic"] for event in events] == [
        "memory.extraction.manual.queued",
        "memory.extraction.manual.completed",
    ]


def test_manual_scheduler_rejects_active_chat_run(monkeypatch) -> None:
    class FakeDb:
        @staticmethod
        def list_run_records(*, session_id, run_type=None, limit=20):
            if run_type == "chat":
                return [{"id": "run-active", "status": "running"}]
            return []

    monkeypatch.setattr(extraction_service_module, "db", FakeDb())

    result = MemoryExtractionService().schedule_manual(session_id="session-active")

    assert result == {
        "accepted": False,
        "status": "busy",
        "reason": "chat_run_active",
        "summary": "当前任务仍在运行，请在本轮结束后再整理记忆。",
    }


def test_manual_scheduler_rejects_duplicate_in_process_request(monkeypatch) -> None:
    class FakeDb:
        @staticmethod
        def list_run_records(*, session_id, run_type=None, limit=20):
            return []

        @staticmethod
        def add_runtime_event(_event):
            return None

    class DeferredThread:
        def __init__(self, *, target, **_kwargs):
            self._target = target

        def start(self):
            return None

    monkeypatch.setattr(extraction_service_module, "db", FakeDb())
    monkeypatch.setattr(extraction_service_module.threading, "Thread", DeferredThread)
    service = MemoryExtractionService()

    first = service.schedule_manual(session_id="session-manual")
    second = service.schedule_manual(session_id="session-manual")

    assert first["accepted"] is True
    assert second == {
        "accepted": False,
        "status": "busy",
        "reason": "memory_extraction_active",
        "summary": "当前任务的记忆正在整理，请稍后查看结果。",
    }
