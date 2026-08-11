"""Offline, read-only feature-pack runtime probe for packaged desktop smoke.

The probe intentionally uses Engine-owned receipt and status contracts rather
than duplicating installation truth in the Shell.  Its JSON result is safe for
CI summaries: it never includes paths, commands, process output, or exception
text.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


RPA_PACK_ID = "rpa_automation"
IMAGE_PACK_ID = "creative_media_image_analysis"


class ProbeInputError(ValueError):
    """Input or environment is unsuitable for a trustworthy offline probe."""


def _safe_error_code(error: BaseException) -> str:
    if isinstance(error, ProbeInputError):
        return str(error) or "probe_input_invalid"
    if isinstance(error, ModuleNotFoundError):
        return "engine_module_unavailable"
    if isinstance(error, ImportError):
        return "engine_module_import_failed"
    if isinstance(error, OSError):
        return "probe_io_failed"
    return "probe_runtime_failed"


def _result(*, ok: bool, mode: str, rpa: dict[str, Any], image: dict[str, Any], error: str | None) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "mode": mode,
        "rpa": rpa,
        "image": image,
        "error": error,
    }


def _read_input() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ProbeInputError("probe_input_invalid") from error
    if not isinstance(payload, dict):
        raise ProbeInputError("probe_input_invalid")
    engine_status = payload.get("engineStatus")
    if engine_status is not None:
        if not isinstance(engine_status, dict) or not isinstance(engine_status.get("featurePacks"), list):
            raise ProbeInputError("engine_status_invalid")
    return payload


def _resolve_context() -> tuple[Path, Path]:
    repo_value = str(os.environ.get("V8_REPO_ROOT") or "").strip()
    state_value = str(os.environ.get("V8_AGENT_OS_HOME") or "").strip()
    if not repo_value:
        raise ProbeInputError("engine_root_missing")
    if not state_value:
        raise ProbeInputError("state_root_missing")
    repo_root = Path(repo_value).expanduser().resolve(strict=False)
    state_root = Path(state_value).expanduser().resolve(strict=False)
    engine_root = repo_root / "apps" / "v8-agent-os-engine"
    if not engine_root.is_dir():
        raise ProbeInputError("engine_root_invalid")
    if not state_root.is_dir():
        raise ProbeInputError("state_root_invalid")
    return engine_root, state_root


def _load_engine_contract(engine_root: Path):
    engine_text = str(engine_root)
    if engine_text not in sys.path:
        sys.path.insert(0, engine_text)
    from core.storage import storage
    from core.runtime.feature_packs import (
        apply_feature_pack_python_paths,
        build_feature_pack_statuses,
        resolve_feature_pack_asset,
    )
    from runtimes.rpa.robot_adapter import RobotFrameworkAdapter

    def probe_onnx_runtime(*args):
        from runtimes.creative_media.image_analysis import _probe_onnx_runtime

        return _probe_onnx_runtime(*args)

    return (
        storage,
        apply_feature_pack_python_paths,
        build_feature_pack_statuses,
        resolve_feature_pack_asset,
        probe_onnx_runtime,
        RobotFrameworkAdapter,
    )


def _status_by_id(statuses: list[dict[str, Any]], pack_id: str) -> dict[str, Any]:
    for status in statuses:
        if isinstance(status, dict) and str(status.get("id") or "") == pack_id:
            return status
    raise ProbeInputError("feature_pack_status_missing")


def _assert_engine_status_agrees(payload: dict[str, Any], local: dict[str, Any], pack_id: str) -> None:
    supplied = payload.get("engineStatus")
    if supplied is None:
        return
    remote = _status_by_id(list(supplied.get("featurePacks") or []), pack_id)
    if (
        str(remote.get("status") or "") != str(local.get("status") or "")
        or bool(remote.get("installed")) != bool(local.get("installed"))
        or bool(remote.get("restartRequired")) != bool(local.get("restartRequired"))
    ):
        raise ProbeInputError("engine_status_mismatch")


def _not_installed_result(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": "not_installed",
        "failClosed": not bool(status.get("installed")),
        "checked": True,
        "error": None,
    }


def _failed_result(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": "failed",
        "failClosed": not bool(status.get("installed")),
        "checked": True,
        "error": "feature_pack_unhealthy",
    }


def _probe_rpa(adapter_class: type, status: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if str(status.get("status") or "") == "not_installed":
        result = _not_installed_result(status)
        return result, bool(result["failClosed"])
    if not bool(status.get("installed")) or bool(status.get("restartRequired")):
        return _failed_result(status), False

    adapter = adapter_class()
    availability = adapter.availability()
    libraries = availability.get("libraries") if isinstance(availability, dict) else {}
    available = bool(availability.get("robotFramework")) and bool(availability.get("rpaFramework"))
    available = available and bool(dict(libraries or {}).get("RPA.Excel.Files"))
    with tempfile.TemporaryDirectory(prefix="v8os-feature-pack-probe-") as temporary:
        root = Path(temporary)
        robot_file = root / "probe.robot"
        robot_file.write_text(
            "*** Settings ***\n"
            "Library    RPA.Excel.Files\n\n"
            "*** Test Cases ***\n"
            "Receipt governed Excel dry run\n"
            "    Create Workbook    fmt=xlsx\n",
            encoding="utf-8",
        )
        validation = adapter.validate_robot_file(robot_file=robot_file, output_dir=root / "dryrun")
    command = list(validation.get("command") or []) if isinstance(validation, dict) else []
    isolated = "-I" in command
    dry_run_passed = bool(validation.get("passed"))
    ok = available and isolated and dry_run_passed
    return (
        {
            "state": "installed",
            "failClosed": False,
            "checked": True,
            "available": available,
            "isolated": isolated,
            "dryRunPassed": dry_run_passed,
            "error": None if ok else "rpa_runtime_validation_failed",
        },
        ok,
    )


def _probe_image(resolve_asset, probe_onnx_runtime, status: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if str(status.get("status") or "") == "not_installed":
        result = _not_installed_result(status)
        return result, bool(result["failClosed"])
    if not bool(status.get("installed")) or bool(status.get("restartRequired")):
        return _failed_result(status), False

    asset = resolve_asset(IMAGE_PACK_ID, "isnet_general_use")
    if asset is None:
        return {
            "state": "installed",
            "failClosed": False,
            "checked": True,
            "assetResolved": False,
            "cpuSessionLoaded": False,
            "isolated": False,
            "moduleOriginsVerified": False,
            "modelShaVerified": False,
            "error": "image_asset_unavailable",
        }, False
    target_dir = str(status.get("targetDir") or "").strip()
    if not target_dir:
        return {
            "state": "installed",
            "failClosed": False,
            "checked": True,
            "assetResolved": True,
            "cpuSessionLoaded": False,
            "isolated": False,
            "moduleOriginsVerified": False,
            "modelShaVerified": False,
            "error": "image_target_unavailable",
        }, False
    try:
        probe = dict(probe_onnx_runtime(asset, target_dir) or {})
    except Exception:
        probe = {}
    loaded = probe.get("cpuSessionLoaded") is True
    isolated = probe.get("isolated") is True
    origins_verified = probe.get("moduleOriginsVerified") is True
    model_verified = probe.get("modelShaVerified") is True
    ok = loaded and isolated and origins_verified and model_verified
    return (
        {
            "state": "installed",
            "failClosed": False,
            "checked": True,
            "assetResolved": True,
            "cpuSessionLoaded": loaded,
            "isolated": isolated,
            "moduleOriginsVerified": origins_verified,
            "modelShaVerified": model_verified,
            "error": None if ok else "onnx_isolated_runtime_validation_failed",
        },
        ok,
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    engine_root, _state_root = _resolve_context()
    storage, apply_paths, build_statuses, resolve_asset, probe_onnx_runtime, adapter_class = _load_engine_contract(engine_root)
    registry = storage.get_runtime_registry_config()
    # Mirror Engine startup ordering: receipt-governed paths are applied before
    # the readiness projection is sampled.  Sampling first would manufacture a
    # restartRequired=true result even when this process can load the pack.
    apply_paths(registry)
    statuses = build_statuses(registry)
    rpa_status = _status_by_id(statuses, RPA_PACK_ID)
    image_status = _status_by_id(statuses, IMAGE_PACK_ID)
    _assert_engine_status_agrees(payload, rpa_status, RPA_PACK_ID)
    _assert_engine_status_agrees(payload, image_status, IMAGE_PACK_ID)

    rpa, rpa_ok = _probe_rpa(adapter_class, rpa_status)
    image, image_ok = _probe_image(resolve_asset, probe_onnx_runtime, image_status)
    ok = rpa_ok and image_ok
    return _result(ok=ok, mode="offline_runtime_probe", rpa=rpa, image=image, error=None if ok else "feature_pack_runtime_unhealthy")


def main() -> int:
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            payload = _read_input()
            response = run(payload)
            exit_code = 0 if response["ok"] else 1
        except ProbeInputError as error:
            response = _result(
                ok=False,
                mode="offline_runtime_probe",
                rpa={"checked": False, "error": None},
                image={"checked": False, "error": None},
                error=_safe_error_code(error),
            )
            exit_code = 2
        except BaseException as error:  # Keep a packaged smoke failure safe and stable.
            response = _result(
                ok=False,
                mode="offline_runtime_probe",
                rpa={"checked": False, "error": None},
                image={"checked": False, "error": None},
                error=_safe_error_code(error),
            )
            exit_code = 1
    sys.__stdout__.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
