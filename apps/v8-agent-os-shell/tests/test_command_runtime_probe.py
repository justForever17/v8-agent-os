from __future__ import annotations

import importlib.util
from pathlib import Path


SHELL_ROOT = Path(__file__).resolve().parents[1]
PROBE = SHELL_ROOT / "scripts" / "command_runtime_probe.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("v8os_command_runtime_probe", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_result_requires_all_real_command_backend_proofs() -> None:
    probe = _load_probe()
    result = {
        "ordinary": {
            "backend": "pipe",
            "completed": True,
            "exitCodeObserved": True,
            "outputObserved": True,
        },
        "failure": {
            "backend": "pipe",
            "completed": True,
            "exitCodeObserved": True,
            "failureClassified": True,
        },
        "timeout": {
            "backend": "pipe",
            "completed": True,
            "timedOut": True,
            "deadlineClassified": True,
            "processTreeStopped": True,
        },
        "interactive": {
            "backendExpected": True,
            "usesTty": True,
            "roundTrip": True,
            "processTreeStopped": True,
        },
        "interactiveExit": {
            "backendExpected": True,
            "completed": True,
            "exitCodeObserved": True,
            "failureClassified": True,
            "timedOut": False,
        },
    }

    assert probe._result_ok(result) is True
    result["interactive"]["roundTrip"] = False
    assert probe._result_ok(result) is False
    result["interactive"]["roundTrip"] = True
    result["interactiveExit"]["completed"] = False
    assert probe._result_ok(result) is False
