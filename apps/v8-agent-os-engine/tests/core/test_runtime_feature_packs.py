import re
from pathlib import Path

from core.runtime.feature_packs import (
    FEATURE_PACK_DEFINITIONS,
    build_feature_pack_statuses,
    installed_runtime_families_from_feature_packs,
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
    ]
    assert definitions[0].runtime_families == ("computer_use", "desktop_live")
    assert definitions[1].runtime_families == ("rpa",)
    assert definitions[2].runtime_families == ()


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


def test_requirements_move_heavy_runtime_dependencies_into_feature_packs():
    desktop_common = _requirements_text("desktop-common.txt")
    desktop_preview = _requirements_text("desktop-preview.txt")
    platform_windows = _requirements_text("platform-windows.txt")
    computer_pack = _requirements_text("feature-packs/computer-use-desktop.txt")
    rpa_pack = _requirements_text("feature-packs/rpa-automation.txt")
    local_pack = _requirements_text("feature-packs/local-asr-ocr.txt")

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
