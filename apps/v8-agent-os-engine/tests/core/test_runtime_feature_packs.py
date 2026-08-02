import json
import re
import sys
from pathlib import Path

from core.runtime.feature_packs import (
    FEATURE_PACK_DEFINITIONS,
    apply_feature_pack_python_paths,
    build_feature_pack_statuses,
    installed_runtime_families_from_feature_packs,
    load_feature_pack_asset_manifest,
    load_feature_pack_receipt,
    preferred_feature_pack_execution_provider,
)


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS_ROOT = ENGINE_ROOT / "requirements"


def _requirements_text(name: str) -> str:
    return (REQUIREMENTS_ROOT / name).read_text(encoding="utf-8")


def _assert_requirement_absent(text: str, package_name: str) -> None:
    assert not re.search(rf"^\s*{re.escape(package_name)}(?:\[|[<=>~!;\s]|$)", text, flags=re.IGNORECASE | re.MULTILINE)


def _assert_requirement_present(text: str, package_name: str) -> None:
    assert re.search(rf"^\s*{re.escape(package_name)}(?:\[|[<=>~!;\s]|$)", text, flags=re.IGNORECASE | re.MULTILINE)


def test_feature_pack_contract_order_and_runtime_mapping():
    definitions = list(FEATURE_PACK_DEFINITIONS)

    assert [definition.id for definition in definitions] == [
        "computer_use_desktop",
        "rpa_automation",
        "local_asr_ocr",
        "creative_media_image_analysis",
        "creative_media_motion_capture",
    ]
    assert definitions[0].runtime_families == ("computer_use", "desktop_live")
    assert definitions[1].runtime_families == ("rpa",)
    assert definitions[2].runtime_families == ()
    assert definitions[2].product_name == "可选本地识别包"
    assert definitions[3].runtime_families == ()
    assert definitions[3].asset_manifest_file == "creative-media-image-analysis.manifest.json"
    assert definitions[3].python_path_priority == "fallback"
    assert definitions[4].runtime_families == ()
    assert definitions[4].asset_manifest_file == "creative-media-motion-capture.manifest.json"
    assert definitions[4].python_path_priority == "fallback"


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
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "packId": "creative_media_motion_capture",
                "environment": {"gpuAdapters": ["Test GPU"]},
                "smokeCheck": {"selectedExecutionProvider": "GPU"},
            }
        ),
        encoding="utf-8",
    )
    registry = {
        "featurePacks": {
            "creative_media_motion_capture": {
                "status": "installed",
                "targetDir": str(tmp_path / "python"),
                "receiptRef": str(receipt),
            }
        }
    }

    assert load_feature_pack_receipt("creative_media_motion_capture", registry)["packId"] == "creative_media_motion_capture"
    assert preferred_feature_pack_execution_provider("creative_media_motion_capture", registry) == "GPU"
    receipt.write_text(json.dumps({"packId": "another_pack"}), encoding="utf-8")
    assert load_feature_pack_receipt("creative_media_motion_capture", registry) is None
    assert preferred_feature_pack_execution_provider("creative_media_motion_capture", registry) == "CPU"


def test_feature_pack_status_uses_config_and_legacy_runtime_families(tmp_path):
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

    statuses = build_feature_pack_statuses(registry, install_platform="windows")
    by_id = {item["id"]: item for item in statuses}

    assert by_id["computer_use_desktop"]["status"] == "installed"
    assert by_id["rpa_automation"]["status"] == "installed"
    assert by_id["local_asr_ocr"]["status"] == "installed"
    assert by_id["local_asr_ocr"]["runtimeFamilies"] == []
    assert installed_runtime_families_from_feature_packs(statuses) == [
        "computer_use",
        "desktop_live",
        "rpa",
    ]


def test_image_analysis_pack_fills_missing_modules_without_shadowing_engine_dependencies(monkeypatch, tmp_path):
    desktop_target = tmp_path / "desktop"
    analysis_target = tmp_path / "analysis"
    desktop_target.mkdir()
    analysis_target.mkdir()
    monkeypatch.setattr(sys, "path", ["engine-site-packages"])

    added = apply_feature_pack_python_paths(
        {
            "featurePacks": {
                "computer_use_desktop": {"status": "installed", "targetDir": str(desktop_target)},
                "creative_media_image_analysis": {"status": "installed", "targetDir": str(analysis_target)},
            }
        }
    )

    assert added == [str(desktop_target), str(analysis_target)]
    assert sys.path == [str(desktop_target), "engine-site-packages", str(analysis_target)]


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
