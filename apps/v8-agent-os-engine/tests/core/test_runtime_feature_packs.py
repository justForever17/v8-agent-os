import hashlib
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.runtime import feature_packs as feature_packs_module
from core.runtime import startup_profile
from core.runtime.feature_packs import (
    FEATURE_PACK_DEFINITIONS,
    apply_feature_pack_python_paths,
    build_feature_pack_statuses,
    feature_pack_lock_path,
    feature_pack_requirements_path,
    installed_runtime_families_from_feature_packs,
    load_feature_pack_asset_manifest,
    load_feature_pack_receipt,
    normalize_feature_pack_config,
    preferred_feature_pack_execution_provider,
)


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS_ROOT = ENGINE_ROOT / "requirements"
TEST_DISTRIBUTION_NAME = "V8_Feature.Pack-Test"
TEST_DISTRIBUTION_VERSION = "1.0.0"
TEST_RESOLVED_PACKAGE = "v8-feature-pack-test==1.0.0"


class _ManagedRuntimePlatform:
    def __init__(self, real_platform):
        self._real_platform = real_platform

    def python_version(self):
        return "3.11.9"

    def python_version_tuple(self):
        return ("3", "11", "9")

    def python_implementation(self):
        return "CPython"

    def machine(self):
        return "AMD64"

    def __getattr__(self, name):
        return getattr(self._real_platform, name)


@pytest.fixture(autouse=True)
def _use_managed_runtime_contract(monkeypatch):
    if tuple(platform.python_version_tuple()[:2]) == ("3", "12"):
        monkeypatch.setattr(
            feature_packs_module,
            "platform",
            _ManagedRuntimePlatform(feature_packs_module.platform),
        )


def _requirements_text(name: str) -> str:
    return (REQUIREMENTS_ROOT / name).read_text(encoding="utf-8")


def _assert_requirement_absent(text: str, package_name: str) -> None:
    assert not re.search(rf"^\s*{re.escape(package_name)}(?:\[|[<=>~!;\s]|$)", text, flags=re.IGNORECASE | re.MULTILINE)


def _assert_requirement_present(text: str, package_name: str) -> None:
    assert re.search(rf"^\s*{re.escape(package_name)}(?:\[|[<=>~!;\s]|$)", text, flags=re.IGNORECASE | re.MULTILINE)


@pytest.mark.parametrize(
    ("platform_name", "architecture", "expected_count"),
    [
        ("windows", "x64", 49),
        ("windows", "arm64", 49),
        ("linux", "x64", 39),
        ("linux", "arm64", 39),
        ("macos", "x64", 39),
        ("macos", "arm64", 39),
    ],
)
def test_rpa_hashed_lock_contract(platform_name, architecture, expected_count):
    lock_path = (
        ENGINE_ROOT
        / "requirements"
        / "feature-packs"
        / "locks"
        / f"rpa-automation-cp311-{platform_name}-{architecture}.txt"
    )
    lines = [line.strip() for line in lock_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == expected_count
    assert all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*==[^\s]+ --hash=sha256:[0-9a-f]{64}", line) for line in lines)
    normalized_names = [re.split(r"==", line, maxsplit=1)[0].lower().replace("_", "-").replace(".", "-") for line in lines]
    assert len(normalized_names) == len(set(normalized_names))
    assert "robotframework" in normalized_names
    assert "rpaframework" in normalized_names
    assert "robotframework-seleniumlibrary" in normalized_names
    assert "rpaframework-recognition" not in normalized_names
    assert "robotframework-sapguilibrary" not in normalized_names
    if platform_name == "windows":
        assert "rpaframework-windows" in normalized_names
    else:
        assert "rpaframework-windows" not in normalized_names


def _resolved_packages_payload(packages: list[str] | None = None) -> dict:
    normalized = sorted(set(packages or [TEST_RESOLVED_PACKAGE]))
    return {
        "packages": normalized,
        "sha256": hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest(),
    }


def _materialize_test_distribution(
    target: Path,
    *,
    name: str = TEST_DISTRIBUTION_NAME,
    version: str = TEST_DISTRIBUTION_VERSION,
) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    normalized_name = re.sub(r"[-_.]+", "_", name).strip("_")
    dist_info = target / f"{normalized_name}-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        encoding="utf-8",
    )
    return dist_info


def _compatible_receipt_payload(
    pack_id: str,
    *,
    python_version: str | None = None,
    receipt_platform: str | None = None,
    requirements_sha256: str | None = None,
) -> dict:
    requirements_path = feature_pack_requirements_path(pack_id)
    requirements = {
        "file": requirements_path.name,
        "sha256": requirements_sha256 or hashlib.sha256(requirements_path.read_bytes()).hexdigest(),
    }
    definition = feature_packs_module.FEATURE_PACK_BY_ID[pack_id]
    lock_path = feature_pack_lock_path(pack_id)
    if definition.lock_file_prefix and lock_path and lock_path.is_file():
        requirements.update(
            {
                "lockFile": lock_path.name,
                "lockSha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            }
        )
    payload = {
        "version": 1,
        "packId": pack_id,
        "environment": {
            "platform": receipt_platform or sys.platform,
            "pythonVersion": python_version or feature_packs_module.platform.python_version(),
            "pythonImplementation": feature_packs_module.platform.python_implementation(),
            "architecture": feature_packs_module.platform.machine() or feature_packs_module.platform.architecture()[0],
        },
        "requirements": requirements,
        "resolvedPackages": _resolved_packages_payload(),
    }
    manifest = load_feature_pack_asset_manifest(pack_id)
    if manifest:
        payload["packVersion"] = manifest["version"]
    return payload


def _materialize_rpa_probe_modules(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "robot.py").write_text("VERSION = 'test'\n", encoding="utf-8")
    rpa_package = target / "RPA"
    rpa_package.mkdir(parents=True, exist_ok=True)
    (rpa_package / "__init__.py").write_text("", encoding="utf-8")
    (rpa_package / "Windows.py").write_text("", encoding="utf-8")
    browser_package = rpa_package / "Browser"
    browser_package.mkdir(parents=True, exist_ok=True)
    (browser_package / "__init__.py").write_text("", encoding="utf-8")
    (browser_package / "Selenium.py").write_text("", encoding="utf-8")
    excel_package = rpa_package / "Excel"
    excel_package.mkdir(parents=True, exist_ok=True)
    (excel_package / "__init__.py").write_text("", encoding="utf-8")
    (excel_package / "Files.py").write_text("", encoding="utf-8")


def _materialize_pack_probe_modules(target: Path, pack_id: str) -> None:
    definition = next(item for item in FEATURE_PACK_DEFINITIONS if item.id == pack_id)
    if pack_id == "rpa_automation":
        _materialize_rpa_probe_modules(target)
        return
    modules = list(definition.probe_modules)
    if pack_id == "computer_use_desktop" and sys.platform == "win32":
        modules.extend(["pywinauto", "pycaw"])
    target.mkdir(parents=True, exist_ok=True)
    for module_name in modules:
        assert "." not in module_name
        (target / f"{module_name}.py").write_text("", encoding="utf-8")


def test_feature_pack_contract_order_and_runtime_mapping():
    definitions = list(FEATURE_PACK_DEFINITIONS)

    assert [definition.id for definition in definitions] == [
        "document_ingestion",
        "computer_use_desktop",
        "rpa_automation",
        "local_asr_ocr",
        "creative_media_image_analysis",
        "creative_media_motion_capture",
    ]
    assert definitions[0].requirements_file == "document-ingestion.txt"
    assert definitions[0].probe_modules == ("openpyxl", "xlrd", "docx", "pptx", "pymupdf", "tabulate")
    assert definitions[0].runtime_families == ()
    assert definitions[0].python_path_priority == "fallback"
    assert definitions[1].runtime_families == ("computer_use", "desktop_live")
    assert definitions[1].python_path_priority == "fallback"
    assert definitions[2].runtime_families == ("rpa",)
    assert definitions[2].python_path_priority == "fallback"
    assert definitions[3].runtime_families == ()
    assert definitions[3].product_name == "可选本地识别包"
    assert definitions[3].enabled is False
    assert definitions[4].runtime_families == ()
    assert definitions[4].asset_manifest_file == "creative-media-image-analysis.manifest.json"
    assert definitions[4].lock_file_prefix == "creative-media-image-analysis-cp311"
    assert definitions[4].python_path_priority == "fallback"
    assert definitions[5].runtime_families == ()
    assert definitions[5].asset_manifest_file == "creative-media-motion-capture.manifest.json"
    assert definitions[5].python_path_priority == "fallback"


@pytest.mark.parametrize("platform_name,architecture", [
    ("windows", "x64"),
    ("windows", "arm64"),
    ("linux", "x64"),
    ("linux", "arm64"),
    ("macos", "arm64"),
])
def test_image_analysis_hashed_lock_contract(platform_name, architecture):
    lock_path = (
        ENGINE_ROOT
        / "requirements"
        / "feature-packs"
        / "locks"
        / f"creative-media-image-analysis-cp311-{platform_name}-{architecture}.txt"
    )
    lines = [line.strip() for line in lock_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 5
    assert all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*==[^\s]+ --hash=sha256:[0-9a-f]{64}", line) for line in lines)
    names = [re.split(r"==", line, maxsplit=1)[0].lower().replace("_", "-").replace(".", "-") for line in lines]
    assert names == ["flatbuffers", "numpy", "onnxruntime", "packaging", "protobuf"]
    assert not (ENGINE_ROOT / "requirements" / "feature-packs" / "locks" / "creative-media-image-analysis-cp311-macos-x64.txt").exists()


def test_disabled_local_recognition_pack_never_uses_ambient_imports(monkeypatch):
    monkeypatch.setattr(feature_packs_module, "_module_importable", lambda _module: True)

    status = next(
        item for item in build_feature_pack_statuses({"installedRuntimeFamilies": []})
        if item["id"] == "local_asr_ocr"
    )

    assert status["status"] == "not_installed"
    assert status["installed"] is False
    assert status["installable"] is False
    assert status["missingReason"] == "disabled"


def test_ambient_import_probe_never_promotes_an_unreceipted_pack_across_boots(monkeypatch, tmp_path):
    registry = {
        "installedRuntimeFamilies": ["rpa"],
        "featurePacks": {
            "rpa_automation": {
                "status": "not_installed",
                "targetDir": str(tmp_path / "ambient-only"),
                "restartRequired": True,
            }
        },
    }
    monkeypatch.setattr(feature_packs_module, "_has_probe_modules", lambda _definition, _target=None: True)
    monkeypatch.setattr(sys, "path", ["engine-site-packages"])

    for _boot in range(2):
        monkeypatch.setattr(feature_packs_module, "_FEATURE_PACK_BOOTSTRAP_COMPLETE", False)
        monkeypatch.setattr(feature_packs_module, "_FEATURE_PACKS_READY_AT_BOOT", set())

        assert apply_feature_pack_python_paths(registry) == []
        status = next(
            item for item in build_feature_pack_statuses(registry)
            if item["id"] == "rpa_automation"
        )

        assert status["status"] == "not_installed"
        assert status["installed"] is False
        assert status["installable"] is True
        assert status["restartRequired"] is False
        assert status["missingReason"] == "legacy_runtime_unverified"
        assert status["source"] == "ambient_import_probe_unverified"
        assert installed_runtime_families_from_feature_packs([status]) == []
        assert sys.path == ["engine-site-packages"]


def test_feature_pack_normalization_preserves_active_install_lease():
    normalized = normalize_feature_pack_config(
        {
            "rpa_automation": {
                "status": "installing",
                "operationId": "operation-123",
                "startedAt": "2026-08-10T00:00:00Z",
            }
        }
    )["rpa_automation"]

    assert normalized["status"] == "installing"
    assert normalized["operationId"] == "operation-123"
    assert normalized["startedAt"] == "2026-08-10T00:00:00Z"


def test_rpa_pack_requires_restart_until_current_process_bootstraps_it(monkeypatch, tmp_path):
    target = tmp_path / "rpa" / "python"
    target.mkdir(parents=True)
    _materialize_rpa_probe_modules(target)
    _materialize_test_distribution(target)
    receipt = tmp_path / "rpa" / "receipt.json"
    receipt.write_text(json.dumps(_compatible_receipt_payload("rpa_automation")), encoding="utf-8")
    registry = {
        "featurePacks": {
            "rpa_automation": {
                "status": "installed",
                "targetDir": str(target),
                "receiptRef": str(receipt),
                "restartRequired": True,
            }
        }
    }
    monkeypatch.setattr(sys, "path", ["engine-site-packages"])
    monkeypatch.setattr(feature_packs_module, "_FEATURE_PACK_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(feature_packs_module, "_FEATURE_PACKS_READY_AT_BOOT", set())

    apply_feature_pack_python_paths(registry)
    before_restart = next(item for item in build_feature_pack_statuses(registry) if item["id"] == "rpa_automation")

    assert before_restart["status"] == "installed"
    assert before_restart["restartRequired"] is True
    assert installed_runtime_families_from_feature_packs([before_restart]) == []

    monkeypatch.setattr(sys, "path", ["engine-site-packages"])
    monkeypatch.setattr(feature_packs_module, "_FEATURE_PACK_BOOTSTRAP_COMPLETE", False)
    monkeypatch.setattr(feature_packs_module, "_FEATURE_PACKS_READY_AT_BOOT", set())
    apply_feature_pack_python_paths(registry)
    after_restart = next(item for item in build_feature_pack_statuses(registry) if item["id"] == "rpa_automation")

    assert after_restart["status"] == "installed"
    assert after_restart["restartRequired"] is False
    assert installed_runtime_families_from_feature_packs([after_restart]) == ["rpa"]


def test_creative_media_image_analysis_pack_has_pinned_verified_asset():
    manifest = load_feature_pack_asset_manifest("creative_media_image_analysis")

    assert manifest is not None
    assert manifest["version"] == "1.0.0"
    assert manifest["license"]["name"] == "Apache-2.0"
    assert manifest["smokeCheck"] == {"kind": "onnx", "preferGpu": True}
    assert manifest["assets"] == [
        {
            "id": "isnet_general_use",
            "target": "isnet-general-use.onnx",
            "url": "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx",
            "size": 178648008,
            "sha256": "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a",
        }
    ]


def test_creative_media_motion_capture_pack_has_pinned_verified_asset():
    manifest = load_feature_pack_asset_manifest("creative_media_motion_capture")

    assert manifest is not None
    assert manifest["version"] == "1.0.0"
    assert manifest["license"]["name"] == "Apache-2.0"
    assert manifest["smokeCheck"] == {
        "kind": "mediapipe_task",
        "task": "holistic_landmarker",
        "preferGpu": True,
    }
    assert manifest["assets"] == [
        {
            "id": "holistic_landmarker",
            "target": "holistic-landmarker-float16-v1.task",
            "url": "https://storage.googleapis.com/mediapipe-models/holistic_landmarker/holistic_landmarker/float16/1/holistic_landmarker.task",
            "size": 13683609,
            "sha256": "e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8",
        }
    ]


def test_feature_pack_receipt_projects_only_the_validated_execution_provider(tmp_path):
    target = tmp_path / "python"
    _materialize_test_distribution(target)
    receipt = tmp_path / "receipt.json"
    payload = _compatible_receipt_payload("creative_media_motion_capture")
    payload["environment"]["gpuAdapters"] = ["Test GPU"]
    payload["smokeCheck"] = {"selectedExecutionProvider": "GPU"}
    receipt.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    registry = {
        "featurePacks": {
            "creative_media_motion_capture": {
                "status": "installed",
                "targetDir": str(target),
                "receiptRef": str(receipt),
            }
        }
    }

    assert load_feature_pack_receipt("creative_media_motion_capture", registry)["packId"] == "creative_media_motion_capture"
    assert preferred_feature_pack_execution_provider("creative_media_motion_capture", registry) == "GPU"
    receipt.write_text(json.dumps({"packId": "another_pack"}), encoding="utf-8")
    assert load_feature_pack_receipt("creative_media_motion_capture", registry) is None
    assert preferred_feature_pack_execution_provider("creative_media_motion_capture", registry) == "CPU"


def test_feature_pack_status_does_not_trust_unverified_legacy_runtime_families(tmp_path, monkeypatch):
    registry = {
        "installedRuntimeFamilies": ["rpa"],
        "featurePacks": {
            "computer_use_desktop": {
                "status": "installed",
                "targetDir": str(tmp_path / "computer-use"),
            },
            "local_asr_ocr": {
                "status": "installed",
                "targetDir": str(tmp_path / "local-asr-ocr"),
            },
        },
    }
    (tmp_path / "computer-use").mkdir()
    (tmp_path / "local-asr-ocr").mkdir()
    monkeypatch.setattr(feature_packs_module, "_module_importable", lambda _module: False)

    statuses = build_feature_pack_statuses(registry, install_platform="windows")
    by_id = {item["id"]: item for item in statuses}

    assert by_id["computer_use_desktop"]["status"] == "failed"
    assert by_id["computer_use_desktop"]["missingReason"] == "receipt_missing"
    assert by_id["rpa_automation"]["status"] == "not_installed"
    assert by_id["rpa_automation"]["missingReason"] == "legacy_runtime_unverified"
    assert by_id["rpa_automation"]["source"] == "legacy_runtime_families_unverified"
    assert by_id["local_asr_ocr"]["status"] == "not_installed"
    assert by_id["local_asr_ocr"]["installed"] is False
    assert by_id["local_asr_ocr"]["installable"] is False
    assert by_id["local_asr_ocr"]["missingReason"] == "disabled"
    assert by_id["local_asr_ocr"]["runtimeFamilies"] == []
    assert installed_runtime_families_from_feature_packs(statuses) == []


def test_runtime_registry_filters_unverified_legacy_packed_families(monkeypatch):
    payload = {
        "installProfile": "desktop",
        "installPlatform": "linux",
        "installedRuntimeFamilies": ["chat", "computer_use", "rpa"],
    }
    monkeypatch.setattr(startup_profile, "ensure_runtime_registry_installation_state", lambda: None)
    monkeypatch.setattr(startup_profile.storage, "get_runtime_registry_config", lambda: payload)
    monkeypatch.setattr(startup_profile, "apply_feature_pack_python_paths", lambda _payload: [])
    monkeypatch.setattr(
        startup_profile,
        "build_feature_pack_statuses",
        lambda _payload, **_kwargs: [
            {"id": "computer_use_desktop", "status": "installed", "runtimeFamilies": ["computer_use"]},
            {"id": "rpa_automation", "status": "not_installed", "runtimeFamilies": ["rpa"]},
        ],
    )

    state = startup_profile.get_runtime_registry_state()

    assert "computer_use" in state["installedRuntimeFamilies"]
    assert "rpa" not in state["installedRuntimeFamilies"]


def test_runtime_registry_keeps_a_legacy_computer_use_family_only_when_its_platform_probe_passes(monkeypatch):
    payload = {
        "installProfile": "desktop",
        "installPlatform": "linux",
        "installedRuntimeFamilies": ["computer_use", "rpa"],
    }
    monkeypatch.setattr(startup_profile, "ensure_runtime_registry_installation_state", lambda: None)
    monkeypatch.setattr(startup_profile.storage, "get_runtime_registry_config", lambda: payload)
    monkeypatch.setattr(startup_profile, "apply_feature_pack_python_paths", lambda _payload: [])
    monkeypatch.setattr(
        startup_profile,
        "build_feature_pack_statuses",
        lambda _payload, **_kwargs: [
            {"id": "computer_use_desktop", "status": "not_installed", "runtimeFamilies": ["computer_use", "desktop_live"]},
            {"id": "rpa_automation", "status": "not_installed", "runtimeFamilies": ["rpa"]},
        ],
    )
    monkeypatch.setattr(
        startup_profile,
        "_detect_installed_runtime_families",
        lambda _platform: ["chat", "computer_use"],
    )

    state = startup_profile.get_runtime_registry_state()

    assert "computer_use" in state["installedRuntimeFamilies"]
    assert "rpa" not in state["installedRuntimeFamilies"]


def test_image_analysis_pack_fills_missing_modules_without_shadowing_engine_dependencies(monkeypatch, tmp_path):
    desktop_target = tmp_path / "desktop"
    analysis_target = tmp_path / "analysis"
    desktop_receipt = tmp_path / "desktop-receipt.json"
    analysis_receipt = tmp_path / "analysis-receipt.json"
    desktop_target.mkdir()
    analysis_target.mkdir()
    _materialize_test_distribution(desktop_target)
    _materialize_test_distribution(analysis_target)
    desktop_receipt.write_text(json.dumps(_compatible_receipt_payload("computer_use_desktop")), encoding="utf-8")
    analysis_receipt.write_text(
        json.dumps(_compatible_receipt_payload("creative_media_image_analysis")),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "path", ["engine-site-packages"])

    added = apply_feature_pack_python_paths(
        {
            "featurePacks": {
                "computer_use_desktop": {
                    "status": "installed",
                    "targetDir": str(desktop_target),
                    "receiptRef": str(desktop_receipt),
                },
                "creative_media_image_analysis": {
                    "status": "installed",
                    "targetDir": str(analysis_target),
                    "receiptRef": str(analysis_receipt),
                },
            }
        }
    )

    assert added == [str(desktop_target), str(analysis_target)]
    assert sys.path == ["engine-site-packages", str(desktop_target), str(analysis_target)]


def test_asset_pack_rejects_a_receipt_from_an_incompatible_python_abi(monkeypatch, tmp_path):
    target = tmp_path / "motion" / "python"
    model_root = tmp_path / "motion" / "models"
    target.mkdir(parents=True)
    model_root.mkdir(parents=True)
    (model_root / "holistic-landmarker-float16-v1.task").write_bytes(b"model")
    receipt = tmp_path / "motion" / "receipt.json"
    current_minor = feature_packs_module.platform.python_version_tuple()[:2]
    incompatible_minor = "3.12" if current_minor != ("3", "12") else "3.11"
    receipt.write_text(
        json.dumps(
            _compatible_receipt_payload(
                "creative_media_motion_capture",
                python_version=f"{incompatible_minor}.0",
            )
        ),
        encoding="utf-8",
    )
    registry = {
        "featurePacks": {
            "creative_media_motion_capture": {
                "status": "installed",
                "targetDir": str(target),
                "assetRoot": str(model_root),
                "receiptRef": str(receipt),
                "restartRequired": True,
            }
        }
    }
    monkeypatch.setattr(sys, "path", ["engine-site-packages"])

    assert apply_feature_pack_python_paths(registry) == []
    status = next(item for item in build_feature_pack_statuses(registry) if item["id"] == "creative_media_motion_capture")
    assert status["status"] == "failed"
    assert status["installed"] is False
    assert status["missingReason"] == "python_abi_mismatch"
    assert status["receiptPythonVersion"] == f"{incompatible_minor}.0"
    assert status["runtimePythonVersion"] == feature_packs_module.platform.python_version()
    assert "重新安装" in status["lastError"]


def test_non_asset_pack_receipt_is_also_bound_to_the_current_python_abi(monkeypatch, tmp_path):
    target = tmp_path / "rpa" / "python"
    target.mkdir(parents=True)
    receipt = tmp_path / "rpa" / "receipt.json"
    current_minor = feature_packs_module.platform.python_version_tuple()[:2]
    incompatible_minor = "3.12" if current_minor != ("3", "12") else "3.11"
    payload = _compatible_receipt_payload("rpa_automation", python_version=f"{incompatible_minor}.0")
    payload["smokeCheck"] = {
        "kind": "python_import",
        "modules": ["robot", "RPA", "RPA.Windows"],
    }
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    registry = {
        "featurePacks": {
            "rpa_automation": {
                "status": "installed",
                "targetDir": str(target),
                "receiptRef": str(receipt),
                "restartRequired": True,
            }
        }
    }
    monkeypatch.setattr(sys, "path", ["engine-site-packages"])

    assert apply_feature_pack_python_paths(registry) == []
    status = next(item for item in build_feature_pack_statuses(registry) if item["id"] == "rpa_automation")
    assert status["status"] == "failed"
    assert status["installed"] is False
    assert status["missingReason"] == "python_abi_mismatch"
    assert status["receiptPythonVersion"] == f"{incompatible_minor}.0"


@pytest.mark.parametrize(
    ("pack_id", "payload_mutation", "expected_reason"),
    [
        (
            "rpa_automation",
            lambda payload: payload["environment"].update(
                {"platform": "darwin" if sys.platform != "darwin" else "linux"}
            ),
            "platform_mismatch",
        ),
        (
            "rpa_automation",
            lambda payload: payload["requirements"].update({"sha256": "0" * 64}),
            "requirements_mismatch",
        ),
        (
            "rpa_automation",
            lambda payload: payload["requirements"].update({"lockSha256": "0" * 64}),
            "requirements_lock_mismatch",
        ),
        (
            "creative_media_image_analysis",
            lambda payload: payload.update({"packVersion": "0.0.0"}),
            "asset_manifest_mismatch",
        ),
    ],
)
def test_installed_pack_receipt_rejects_platform_requirements_and_manifest_drift(
    tmp_path,
    pack_id,
    payload_mutation,
    expected_reason,
):
    pack_root = tmp_path / pack_id
    target = pack_root / "python"
    target.mkdir(parents=True)
    receipt = pack_root / "receipt.json"
    payload = _compatible_receipt_payload(pack_id)
    payload_mutation(payload)
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    registry = {
        "featurePacks": {
            pack_id: {
                "status": "installed",
                "targetDir": str(target),
                "receiptRef": str(receipt),
            }
        }
    }

    status = next(item for item in build_feature_pack_statuses(registry) if item["id"] == pack_id)

    assert status["status"] == "failed"
    assert status["installed"] is False
    assert status["missingReason"] == expected_reason
    assert apply_feature_pack_python_paths(registry) == []


@pytest.mark.parametrize(
    ("payload_mutation", "expected_reason"),
    [
        (
            lambda payload: payload.pop("resolvedPackages"),
            "resolved_packages_missing",
        ),
        (
            lambda payload: payload["resolvedPackages"].update({"sha256": "0" * 64}),
            "resolved_packages_mismatch",
        ),
        (
            lambda payload: payload.update(
                {"resolvedPackages": _resolved_packages_payload(["different-package==9.9.9"])}
            ),
            "resolved_packages_mismatch",
        ),
    ],
)
def test_installed_pack_rejects_missing_or_false_resolved_package_truth(
    monkeypatch,
    tmp_path,
    payload_mutation,
    expected_reason,
):
    target = tmp_path / "rpa" / "python"
    _materialize_rpa_probe_modules(target)
    _materialize_test_distribution(target)
    receipt = tmp_path / "rpa" / "receipt.json"
    payload = _compatible_receipt_payload("rpa_automation")
    payload_mutation(payload)
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    registry = {
        "featurePacks": {
            "rpa_automation": {
                "status": "installed",
                "targetDir": str(target),
                "receiptRef": str(receipt),
            }
        }
    }
    monkeypatch.setattr(sys, "path", ["engine-site-packages"])

    assert apply_feature_pack_python_paths(registry) == []
    status = next(item for item in build_feature_pack_statuses(registry) if item["id"] == "rpa_automation")

    assert status["status"] == "failed"
    assert status["installed"] is False
    assert status["installable"] is True
    assert status["missingReason"] == expected_reason
    assert "重新安装" in status["lastError"]
    assert sys.path == ["engine-site-packages"]


def test_installed_pack_detects_tampered_dist_info_without_polluting_sys_path(monkeypatch, tmp_path):
    target = tmp_path / "rpa" / "python"
    _materialize_rpa_probe_modules(target)
    dist_info = _materialize_test_distribution(target)
    receipt = tmp_path / "rpa" / "receipt.json"
    receipt.write_text(json.dumps(_compatible_receipt_payload("rpa_automation")), encoding="utf-8")
    registry = {
        "featurePacks": {
            "rpa_automation": {
                "status": "installed",
                "targetDir": str(target),
                "receiptRef": str(receipt),
            }
        }
    }
    monkeypatch.setattr(sys, "path", ["engine-site-packages"])

    healthy = next(item for item in build_feature_pack_statuses(registry) if item["id"] == "rpa_automation")
    assert healthy["status"] == "installed"
    assert apply_feature_pack_python_paths(registry) == [str(target)]

    monkeypatch.setattr(sys, "path", ["engine-site-packages"])
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {TEST_DISTRIBUTION_NAME}\nVersion: 2.0.0\n",
        encoding="utf-8",
    )

    assert apply_feature_pack_python_paths(registry) == []
    damaged = next(item for item in build_feature_pack_statuses(registry) if item["id"] == "rpa_automation")
    assert damaged["status"] == "failed"
    assert damaged["installed"] is False
    assert damaged["installable"] is True
    assert damaged["missingReason"] == "resolved_packages_mismatch"
    assert "重新安装" in damaged["lastError"]
    assert sys.path == ["engine-site-packages"]


def test_installed_pack_with_valid_receipt_but_missing_target_is_failed(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(_compatible_receipt_payload("rpa_automation")),
        encoding="utf-8",
    )
    registry = {
        "featurePacks": {
            "rpa_automation": {
                "status": "installed",
                "targetDir": str(tmp_path / "missing-python"),
                "receiptRef": str(receipt),
            }
        }
    }

    status = next(item for item in build_feature_pack_statuses(registry) if item["id"] == "rpa_automation")

    assert status["status"] == "failed"
    assert status["missingReason"] == "target_missing"
    assert "重新安装" in status["lastError"]


def test_installed_non_asset_pack_with_missing_probe_module_is_repairable(tmp_path):
    target = tmp_path / "rpa" / "python"
    target.mkdir(parents=True)
    _materialize_rpa_probe_modules(target)
    _materialize_test_distribution(target)
    receipt = tmp_path / "rpa" / "receipt.json"
    receipt.write_text(json.dumps(_compatible_receipt_payload("rpa_automation")), encoding="utf-8")
    registry = {
        "featurePacks": {
            "rpa_automation": {
                "status": "installed",
                "targetDir": str(target),
                "receiptRef": str(receipt),
            }
        }
    }

    healthy = next(item for item in build_feature_pack_statuses(registry) if item["id"] == "rpa_automation")
    assert healthy["status"] == "installed"

    (target / "robot.py").unlink()
    damaged = next(item for item in build_feature_pack_statuses(registry) if item["id"] == "rpa_automation")

    assert damaged["status"] == "failed"
    assert damaged["installed"] is False
    assert damaged["missingReason"] == "runtime_probe_failed"
    assert "重新安装" in damaged["lastError"]


def test_installed_asset_pack_with_corrupt_model_is_repairable(monkeypatch, tmp_path):
    target = tmp_path / "analysis" / "python"
    model_root = tmp_path / "analysis" / "models"
    target.mkdir(parents=True)
    model_root.mkdir(parents=True)
    (target / "onnxruntime.py").write_text("", encoding="utf-8")
    _materialize_test_distribution(target)
    model = model_root / "model.onnx"
    model.write_bytes(b"verified-model")
    manifest = {
        "version": "1.0.0",
        "assets": [{
            "id": "model",
            "target": "model.onnx",
            "size": model.stat().st_size,
            "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        }],
    }
    monkeypatch.setattr(
        feature_packs_module,
        "load_feature_pack_asset_manifest",
        lambda pack_id: manifest if pack_id == "creative_media_image_analysis" else None,
    )
    receipt = tmp_path / "analysis" / "receipt.json"
    receipt.write_text(
        json.dumps(_compatible_receipt_payload("creative_media_image_analysis")),
        encoding="utf-8",
    )
    registry = {
        "featurePacks": {
            "creative_media_image_analysis": {
                "status": "installed",
                "targetDir": str(target),
                "assetRoot": str(model_root),
                "receiptRef": str(receipt),
            }
        }
    }

    healthy = next(
        item for item in build_feature_pack_statuses(registry)
        if item["id"] == "creative_media_image_analysis"
    )
    assert healthy["status"] == "installed"

    model.write_bytes(b"truncated")
    damaged = next(
        item for item in build_feature_pack_statuses(registry)
        if item["id"] == "creative_media_image_analysis"
    )

    assert damaged["status"] == "failed"
    assert damaged["installed"] is False
    assert damaged["missingReason"] == "assets_invalid"
    assert "重新安装" in damaged["lastError"]


def test_windows_feature_pack_probes_include_platform_only_modules(monkeypatch):
    imported: list[str] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        feature_packs_module,
        "_module_importable",
        lambda module_name: imported.append(module_name) is None or True,
    )

    computer = next(item for item in FEATURE_PACK_DEFINITIONS if item.id == "computer_use_desktop")
    rpa = next(item for item in FEATURE_PACK_DEFINITIONS if item.id == "rpa_automation")

    assert feature_packs_module._has_probe_modules(computer) is True
    assert feature_packs_module._has_probe_modules(rpa) is True
    assert "pywinauto" in imported
    assert "RPA.Windows" in imported


@pytest.mark.parametrize("pack_count", [0, 1, 4])
def test_feature_pack_bootstrap_is_bounded_and_never_spawns_import_probes(monkeypatch, tmp_path, pack_count):
    enabled_definitions = [definition for definition in FEATURE_PACK_DEFINITIONS if definition.enabled]
    selected = enabled_definitions[:pack_count]
    registry = {"featurePacks": {}}

    def test_manifest(pack_id: str):
        definition = next(item for item in FEATURE_PACK_DEFINITIONS if item.id == pack_id)
        return {"version": "1.0.0", "assets": []} if definition.asset_manifest_file else None

    monkeypatch.setattr(feature_packs_module, "load_feature_pack_asset_manifest", test_manifest)
    monkeypatch.setattr(feature_packs_module, "_asset_manifest_files_are_valid", lambda _root, _manifest: True)
    for definition in selected:
        pack_root = tmp_path / definition.id
        target = pack_root / "python"
        _materialize_pack_probe_modules(target, definition.id)
        _materialize_test_distribution(target)
        receipt = pack_root / "receipt.json"
        receipt.write_text(json.dumps(_compatible_receipt_payload(definition.id)), encoding="utf-8")
        registry["featurePacks"][definition.id] = {
            "status": "installed",
            "targetDir": str(target),
            "assetRoot": str(pack_root / "models"),
            "receiptRef": str(receipt),
            "restartRequired": True,
        }

    subprocess_calls: list[tuple] = []

    def reject_subprocess(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        raise AssertionError("feature-pack bootstrap must not spawn an import probe")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)
    monkeypatch.setattr(sys, "path", ["engine-site-packages"])
    monkeypatch.setattr(feature_packs_module, "_FEATURE_PACK_BOOTSTRAP_COMPLETE", False)
    monkeypatch.setattr(feature_packs_module, "_FEATURE_PACKS_READY_AT_BOOT", set())

    started = time.perf_counter()
    added = apply_feature_pack_python_paths(registry)
    elapsed = time.perf_counter() - started

    assert added == [str(tmp_path / definition.id / "python") for definition in selected]
    assert elapsed < 1.0
    assert subprocess_calls == []
    assert feature_packs_module._FEATURE_PACKS_READY_AT_BOOT == {definition.id for definition in selected}

    statuses = build_feature_pack_statuses(registry)
    selected_statuses = [item for item in statuses if item["id"] in {definition.id for definition in selected}]
    assert all(item["status"] == "installed" and item["restartRequired"] is False for item in selected_statuses)
    assert installed_runtime_families_from_feature_packs(statuses) == list(dict.fromkeys(
        family for definition in selected for family in definition.runtime_families
    ))
    assert subprocess_calls == []


def test_target_probe_checks_module_origin_without_executing_feature_pack_code(tmp_path):
    target = tmp_path / "python"
    _materialize_rpa_probe_modules(target)
    execution_marker = tmp_path / "module-executed"
    (target / "robot.py").write_text(
        f"from pathlib import Path\nPath({str(execution_marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    definition = next(item for item in FEATURE_PACK_DEFINITIONS if item.id == "rpa_automation")

    assert feature_packs_module._has_probe_modules(definition, target) is True
    assert execution_marker.exists() is False


def test_engine_applies_feature_pack_paths_before_importing_api_routes():
    main_source = (ENGINE_ROOT / "main.py").read_text(encoding="utf-8")

    assert main_source.index("STARTUP_PROFILE = resolve_startup_profile()") < main_source.index("from api import routes")


def test_health_projections_reuse_one_runtime_registry_snapshot(monkeypatch):
    state = {
        "installProfile": "minimal",
        "installPlatform": "windows",
        "installedRuntimeFamilies": ["chat", "memory", "extensions", "automation"],
        "featurePacks": [],
        "featurePackSummary": {},
        "bootstrapManaged": True,
        "lastUpgradeAt": None,
    }
    monkeypatch.setattr(
        startup_profile,
        "get_runtime_registry_state",
        lambda: (_ for _ in ()).throw(AssertionError("projection must reuse the request snapshot")),
    )

    assert startup_profile.build_installation_snapshot(_state=state)["installPlatform"] == "windows"
    assert startup_profile.startup_bundle_summary(_state=state)["audio"] is True
    assert startup_profile.runtime_cluster_summary(_state=state)["chatruntime"] is True
    assert startup_profile.runtime_submode_summary(_state=state)["desktopcluster"] == "off"
    diagnostics = startup_profile.startup_bundle_diagnostics(_state=state)
    assert diagnostics["rpa"]["reason"] == "not_installed"
    assert startup_profile.disabled_reason_summary(_state=state) == diagnostics


def test_requirements_move_heavy_runtime_dependencies_into_feature_packs():
    desktop_common = _requirements_text("desktop-common.txt")
    desktop_preview = _requirements_text("desktop-preview.txt")
    platform_windows = _requirements_text("platform-windows.txt")
    computer_pack = _requirements_text("feature-packs/computer-use-desktop.txt")
    rpa_pack = _requirements_text("feature-packs/rpa-automation.txt")
    local_pack = _requirements_text("feature-packs/local-asr-ocr.txt")
    image_analysis_pack = _requirements_text("feature-packs/creative-media-image-analysis.txt")
    motion_capture_pack = _requirements_text("feature-packs/creative-media-motion-capture.txt")

    for text in (desktop_common, desktop_preview, platform_windows):
        for package_name in ("robotframework", "rpaframework", "rpaframework-windows", "pywinauto", "mss"):
            _assert_requirement_absent(text, package_name)

    _assert_requirement_present(computer_pack, "mss")
    _assert_requirement_present(computer_pack, "pywinauto")
    _assert_requirement_present(computer_pack, "av")
    _assert_requirement_present(computer_pack, "aiortc")
    _assert_requirement_present(rpa_pack, "robotframework")
    _assert_requirement_present(rpa_pack, "rpaframework")
    _assert_requirement_present(rpa_pack, "rpaframework-windows")

    for package_name in ("faster-whisper", "torch", "paddleocr", "paddlepaddle"):
        _assert_requirement_absent(local_pack, package_name)

    _assert_requirement_present(image_analysis_pack, "onnxruntime")
    _assert_requirement_present(motion_capture_pack, "mediapipe")
    _assert_requirement_absent(desktop_common, "mediapipe")
    _assert_requirement_absent(desktop_preview, "mediapipe")
