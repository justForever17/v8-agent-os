from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtimes.rpa.capture_v2 import CaptureBroker
from runtimes.rpa.recording import CaptureVerificationRequired, RPARecordingManager


class _TraceStoreStub:
    def __init__(self) -> None:
        self.steps = []

    def append_step(self, *, run_id, session_id, goal, runtime_kind, step, metadata):  # noqa: ANN001 - mirrors trace store.
        self.steps.append(step.as_dict())
        return {"runId": run_id, "stepCount": len(self.steps), "updatedAt": "now"}


def _manager(tmp_path: Path) -> tuple[RPARecordingManager, _TraceStoreStub]:
    store = _TraceStoreStub()
    return RPARecordingManager(trace_store_instance=store, root_dir=tmp_path), store


def test_capture_broker_windows_unavailable_does_not_append_trace(tmp_path: Path) -> None:
    manager, store = _manager(tmp_path)
    recording = manager.start({"name": "rpa v2", "targetMode": "desktop_window", "appId": "notepad"})
    broker = CaptureBroker(manager, request_root=tmp_path / "inspector")

    result = broker.start_session(
        recording["recordingSessionId"],
        {"platform": "windows", "targetLock": {"appId": "notepad", "mode": "desktop_window"}},
    )

    assert result["ok"] is False
    assert result["status"] == "windows_inspector_sidecar_unavailable"
    assert result["session"]["requestPath"].endswith(".request.json")
    assert store.steps == []


def test_capture_broker_mock_candidate_enters_pool_without_trace(tmp_path: Path) -> None:
    manager, store = _manager(tmp_path)
    recording = manager.start({"name": "rpa v2", "targetMode": "desktop_window", "appId": "notepad"})
    broker = CaptureBroker(manager, request_root=tmp_path / "inspector")

    result = broker.start_session(
        recording["recordingSessionId"],
        {
            "platform": "windows",
            "sidecarReady": True,
            "stepId": "step_click_save",
            "mockCandidates": [
                {
                    "label": "Save",
                    "selector": {"automationId": "saveButton", "name": "Save"},
                    "targetWindow": {"title": "Notepad"},
                    "uniqueness": {"count": 1, "source": "mock_sidecar"},
                }
            ],
        },
    )
    updated = manager.get(recording["recordingSessionId"])

    assert result["ok"] is True
    assert len(updated["capturePool"]) == 1
    item = updated["capturePool"][0]
    assert item["locatorBundle"]["primaryLocator"]["automationId"] == "saveButton"
    assert item["anchorBundle"]["window"]["title"] == "Notepad"
    assert item["proof"]["status"] == "unverified"
    assert store.steps == []


def test_replay_verifier_requires_unique_locator_before_save(tmp_path: Path) -> None:
    manager, _store = _manager(tmp_path)
    recording = manager.start({"name": "rpa v2", "targetMode": "desktop_window", "appId": "notepad"})
    broker = CaptureBroker(manager, request_root=tmp_path / "inspector")
    broker.start_session(
        recording["recordingSessionId"],
        {
            "platform": "windows",
            "sidecarReady": True,
            "mockCandidates": [
                {
                    "label": "Save",
                    "selector": {"automationId": "saveButton", "name": "Save"},
                    "uniqueness": {"count": 1, "source": "mock_sidecar"},
                }
            ],
        },
    )
    item = manager.get(recording["recordingSessionId"])["capturePool"][0]

    with pytest.raises(CaptureVerificationRequired):
        manager.save_capture_pool_item(recording["recordingSessionId"], item["tempElementId"], name="Save")

    verified = broker.verifier.verify(recording["recordingSessionId"], item["tempElementId"], {})
    saved = manager.save_capture_pool_item(recording["recordingSessionId"], item["tempElementId"], name="Save")

    assert verified["ok"] is True
    assert verified["proof"]["status"] == "verified"
    assert saved["element"]["proof"]["status"] == "verified"
    assert saved["element"]["locatorBundle"]["uniqueness"]["count"] == 1


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"findCount": 0}, "locator_unresolved"),
        ({"findCount": 2}, "locator_ambiguous"),
        ({"findCount": 1, "highlightOk": False}, "highlight_failed"),
    ],
)
def test_replay_verifier_failure_states(tmp_path: Path, payload: dict, expected: str) -> None:
    manager, _store = _manager(tmp_path)
    recording = manager.start({"name": "rpa v2", "targetMode": "desktop_window", "appId": "notepad"})
    pool_recording = manager.add_capture_pool_item(
        recording["recordingSessionId"],
        {
            "tempElementId": "temp_el_1",
            "locatorBundle": {"platform": "windows", "primaryLocator": {"automationId": "saveButton"}},
            "proof": {"status": "unverified"},
        },
    )
    assert pool_recording["capturePool"][0]["tempElementId"] == "temp_el_1"

    result = CaptureBroker(manager, request_root=tmp_path / "inspector").verifier.verify(recording["recordingSessionId"], "temp_el_1", payload)

    assert result["ok"] is False
    assert result["status"] == expected


def test_browser_inspector_requires_attach_context(tmp_path: Path) -> None:
    manager, _store = _manager(tmp_path)
    recording = manager.start({"name": "browser", "targetMode": "agent_browser", "appId": "browser"})
    broker = CaptureBroker(manager, request_root=tmp_path / "inspector")

    result = broker.start_session(recording["recordingSessionId"], {"platform": "browser"})

    assert result["ok"] is False
    assert result["status"] == "agent_browser_not_open"


def test_browser_inspector_uses_agent_browser_attach_resolver(tmp_path: Path) -> None:
    manager, _store = _manager(tmp_path)
    recording = manager.start({"name": "browser", "targetMode": "agent_browser", "appId": "browser"})
    seen_payloads = []

    def resolver(payload: dict) -> dict:
        seen_payloads.append(payload)
        return {
            "ok": True,
            "browserAttach": {
                "cdpEndpoint": "http://127.0.0.1:9222",
                "targetPort": 9222,
                "proxyPort": 3456,
                "targetId": "page-1",
                "profileMode": "dedicated_debug_profile",
                "browserKind": "chrome",
                "url": "https://example.test/",
            },
        }

    broker = CaptureBroker(manager, request_root=tmp_path / "inspector", browser_attach_resolver=resolver)

    result = broker.start_session(
        recording["recordingSessionId"],
        {"platform": "browser", "browserProfilePolicy": "agent_browser_only", "openMode": "reuse_current_tab"},
    )

    assert result["ok"] is True
    assert result["status"] == "waiting_sidecar"
    assert seen_payloads[0]["browserProfilePolicy"] == "agent_browser_only"
    assert result["session"]["browserAttach"]["targetId"] == "page-1"
    assert result["session"]["browserAttach"]["profileMode"] == "dedicated_debug_profile"
    request_payload = json.loads((tmp_path / "inspector" / f"{result['session']['sessionId']}.request.json").read_text(encoding="utf-8"))
    assert "cdpEndpoint" in request_payload["browserAttach"]
    assert "oneTimeToken" in request_payload
    assert request_payload["captureMode"] == "next_click"


def test_browser_inspector_user_browser_policy_requires_explicit_resolver_success(tmp_path: Path) -> None:
    manager, _store = _manager(tmp_path)
    recording = manager.start({"name": "browser", "targetMode": "agent_browser", "appId": "browser"})

    def resolver(payload: dict) -> dict:
        assert payload["browserProfilePolicy"] == "user_browser_explicit"
        assert payload.get("allowUserBrowser") is False
        return {
            "ok": False,
            "status": "user_browser_attach_requires_explicit_request",
            "reason": "explicit user browser attach is required",
        }

    broker = CaptureBroker(manager, request_root=tmp_path / "inspector", browser_attach_resolver=resolver)

    result = broker.start_session(
        recording["recordingSessionId"],
        {"platform": "browser", "browserProfilePolicy": "user_browser_explicit", "allowUserBrowser": False},
    )

    assert result["ok"] is False
    assert result["status"] == "user_browser_attach_requires_explicit_request"


def test_browser_sidecar_script_does_not_launch_new_browser_profile() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "rpa_playwright_inspector_sidecar.mjs"
    source = script.read_text(encoding="utf-8")

    assert "connectOverCDP" in source
    assert "captureMode" in source
    assert "next_click" in source
    assert "launchPersistentContext" not in source
    assert ".launch(" not in source


def test_inspector_event_token_mismatch_is_rejected(tmp_path: Path) -> None:
    manager, _store = _manager(tmp_path)
    recording = manager.start({"name": "rpa v2", "targetMode": "desktop_window", "appId": "notepad"})
    broker = CaptureBroker(manager, request_root=tmp_path / "inspector")
    started = broker.start_session(recording["recordingSessionId"], {"platform": "windows", "sidecarReady": True})

    with pytest.raises(PermissionError):
        broker.ingest_event(
            recording["recordingSessionId"],
            started["session"]["sessionId"],
            {"type": "candidate", "oneTimeToken": "wrong", "candidate": {"selector": {"automationId": "saveButton"}}},
        )
