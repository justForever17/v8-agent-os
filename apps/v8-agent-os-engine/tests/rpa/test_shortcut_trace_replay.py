from __future__ import annotations

from pathlib import Path

from runtimes.computer_use.trace_store import ComputerUseTraceStore
from runtimes.rpa.compiler import RPATraceCompiler
from runtimes.rpa.store import RPAScriptStore


def _compiler(tmp_path: Path) -> RPATraceCompiler:
    return RPATraceCompiler(
        trace_store_instance=ComputerUseTraceStore(tmp_path / "traces"),
        script_store=RPAScriptStore(tmp_path / "rpa"),
    )


def _step(*, registered: bool, state_changed: bool) -> dict:
    shortcut_id = "media.play_pause" if registered else None
    sequence = "{SPACE}"
    return {
        "stepId": "shortcut_1",
        "appId": "app_qqmusic",
        "action": "hotkey",
        "intent": "play_pause",
        "phase": "action",
        "params": {
            "sequence": sequence,
            **(
                {
                    "shortcut_resolution": {
                        "id": shortcut_id,
                        "driverSequence": sequence,
                        "stateChangeRequired": True,
                    }
                }
                if registered
                else {}
            ),
        },
        "target": {
            "window": {
                "title": "晴天 - 周杰伦",
                "processName": "QQMusic.exe",
                "windowHandle": 42,
            }
        },
        "verification": {
            "passed": state_changed,
            "status": "registered_shortcut_verified" if state_changed else "registered_shortcut_state_unconfirmed",
            "level": "verified" if state_changed else "review_required",
            "details": {"stateChanged": state_changed},
        },
        "recovery": {"performed": False, "transient": False},
        "risk": {},
        "signals": {
            "binding": {
                "requestedAppId": "app_qqmusic",
                "resolvedAppId": "app_qqmusic",
                "bindingMode": "explicit",
                "bindingConfidence": 1.0,
            },
            "preflight": {
                "focusConfirmed": True,
                "windowBound": True,
                "sceneBound": True,
                "blockerDetected": False,
            },
            "verification": {
                "passed": state_changed,
                "status": "registered_shortcut_verified" if state_changed else "registered_shortcut_state_unconfirmed",
                "level": "verified" if state_changed else "review_required",
            },
            "shortcut": {
                "registered": registered,
                "shortcutId": shortcut_id,
                "guideId": "qqmusic.desktop" if registered else None,
                "platform": "windows",
                "driverSequence": sequence,
                "stateChangeRequired": True,
                "stateChanged": state_changed,
                "preconditionEvidence": {
                    "windowBound": True,
                    "windowFocused": True,
                    "textInputExcluded": True,
                },
            },
        },
        "metadata": {"status": "completed"},
    }


def _assessment(compiler: RPATraceCompiler, step: dict):
    return compiler._assessment_for_step(
        step,
        app_id="app_qqmusic",
        compiled_use="hotkey",
        robot_semantic=compiler._robot_semantic_for_step(
            app_id="app_qqmusic",
            step=step,
            compiled_use="hotkey",
            params=step["params"],
        ),
    )


def test_registered_shortcut_with_state_evidence_is_replay_ready(tmp_path: Path) -> None:
    assessment = _assessment(_compiler(tmp_path), _step(registered=True, state_changed=True))

    assert assessment.signals["shortcutReplayReady"] is True
    assert assessment.review_required is False


def test_unregistered_or_unverified_hotkey_cannot_be_promoted(tmp_path: Path) -> None:
    compiler = _compiler(tmp_path)
    for step in (
        _step(registered=False, state_changed=True),
        _step(registered=True, state_changed=False),
    ):
        assessment = _assessment(compiler, step)

        assert assessment.signals["shortcutReplayReady"] is False
        assert assessment.review_required is True


def test_shortcut_identity_participates_in_fingerprint_and_merge_key(tmp_path: Path) -> None:
    compiler = _compiler(tmp_path)
    first = _step(registered=True, state_changed=True)
    second = _step(registered=True, state_changed=True)
    second["params"]["shortcut_resolution"]["id"] = "media.next"
    second["params"]["shortcut_resolution"]["driverSequence"] = "{MEDIA_NEXT_TRACK}"
    second["params"]["sequence"] = "{MEDIA_NEXT_TRACK}"

    assert compiler._step_merge_key(first) != compiler._step_merge_key(second)
    assert compiler._script_fingerprint(app_id="app_qqmusic", steps=[first]) != compiler._script_fingerprint(
        app_id="app_qqmusic", steps=[second]
    )
