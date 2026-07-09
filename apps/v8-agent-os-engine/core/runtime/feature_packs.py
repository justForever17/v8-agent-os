from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.v8_agent_os_paths import RUNTIME_DATA_HOME, V8_AGENT_OS_HOME


FEATURE_PACK_STATUS_VALUES = {"installed", "not_installed", "installing", "failed"}
PACKED_RUNTIME_FAMILIES = {"computer_use", "desktop_live", "rpa"}
FEATURE_PACK_PYTHON_ROOT = RUNTIME_DATA_HOME / "feature-packs"
FEATURE_PACK_LOG_ROOT = V8_AGENT_OS_HOME / "logs" / "feature-packs"


@dataclass(frozen=True)
class FeaturePackDefinition:
    id: str
    product_name: str
    short_name: str
    description: str
    hover: str
    recommended_order: int
    runtime_families: tuple[str, ...]
    requirements_file: str
    probe_modules: tuple[str, ...]


FEATURE_PACK_DEFINITIONS: tuple[FeaturePackDefinition, ...] = (
    FeaturePackDefinition(
        id="computer_use_desktop",
        product_name="桌面操作能力包",
        short_name="桌面操作",
        description="启用桌面截图、窗口识别、点击输入和桌面直播采集。",
        hover="安装后接入桌面操作与 Desktop Live；适合需要 V8OS 观察并操作本机应用的场景。",
        recommended_order=1,
        runtime_families=("computer_use", "desktop_live"),
        requirements_file="computer-use-desktop.txt",
        probe_modules=("mss", "av", "aiortc"),
    ),
    FeaturePackDefinition(
        id="rpa_automation",
        product_name="自动流程能力包",
        short_name="自动流程",
        description="启用 Robot Framework / RPA 流程执行和录制辅助能力。",
        hover="安装后接入自动流程 runtime；适合重复性业务操作、脚本化流程和可复用自动化。",
        recommended_order=2,
        runtime_families=("rpa",),
        requirements_file="rpa-automation.txt",
        probe_modules=("robot", "RPA"),
    ),
    FeaturePackDefinition(
        id="local_asr_ocr",
        product_name="本地识别增强包",
        short_name="本地识别",
        description="为高性能本机提供本地语音转写、OCR 和媒体理解增强。",
        hover="适合电脑性能较高且不想依赖云供应商的用户；安装后按需接入本地语音转写、OCR、媒体/附件理解增强。",
        recommended_order=3,
        runtime_families=(),
        requirements_file="local-asr-ocr.txt",
        probe_modules=("faster_whisper", "paddleocr"),
    ),
)

FEATURE_PACK_BY_ID = {definition.id: definition for definition in FEATURE_PACK_DEFINITIONS}
RUNTIME_FAMILY_TO_FEATURE_PACK = {
    runtime_family: definition.id
    for definition in FEATURE_PACK_DEFINITIONS
    for runtime_family in definition.runtime_families
}


def feature_pack_target_dir(pack_id: str) -> Path:
    return FEATURE_PACK_PYTHON_ROOT / str(pack_id) / "python"


def feature_pack_requirements_path(pack_id: str) -> Path:
    definition = FEATURE_PACK_BY_ID[str(pack_id)]
    return Path(__file__).resolve().parents[2] / "requirements" / "feature-packs" / definition.requirements_file


def normalize_feature_pack_config(value: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    source = value if isinstance(value, dict) else {}
    for definition in FEATURE_PACK_DEFINITIONS:
        raw = source.get(definition.id)
        raw_payload = dict(raw) if isinstance(raw, dict) else {}
        status = str(raw_payload.get("status") or "").strip()
        if status not in FEATURE_PACK_STATUS_VALUES:
            status = "not_installed"
        target_dir = str(raw_payload.get("targetDir") or feature_pack_target_dir(definition.id)).strip()
        result[definition.id] = {
            "status": status,
            "targetDir": target_dir,
            "logRef": str(raw_payload.get("logRef") or "").strip() or None,
            "lastError": str(raw_payload.get("lastError") or "").strip() or None,
            "updatedAt": str(raw_payload.get("updatedAt") or "").strip() or None,
            "restartRequired": bool(raw_payload.get("restartRequired", status == "installed")),
        }
    return result


def _module_importable(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (AttributeError, ImportError, ValueError):
        return False


def _has_probe_modules(definition: FeaturePackDefinition) -> bool:
    probe_modules = list(definition.probe_modules)
    if definition.id == "computer_use_desktop" and sys.platform == "win32":
        probe_modules.append("pywinauto")
    if not probe_modules:
        return False
    return all(_module_importable(module_name) for module_name in probe_modules)


def apply_feature_pack_python_paths(runtime_registry: dict[str, Any] | None = None) -> list[str]:
    registry = runtime_registry if isinstance(runtime_registry, dict) else {}
    feature_packs = normalize_feature_pack_config(registry.get("featurePacks"))
    added: list[str] = []
    for pack_id, pack_state in feature_packs.items():
        if str(pack_state.get("status") or "") != "installed":
            continue
        target = Path(str(pack_state.get("targetDir") or feature_pack_target_dir(pack_id))).expanduser()
        if not target.exists():
            continue
        target_text = str(target)
        if target_text not in sys.path:
            sys.path.insert(0, target_text)
            added.append(target_text)
    return added


def build_feature_pack_statuses(
    runtime_registry: dict[str, Any] | None = None,
    *,
    install_platform: str | None = None,
) -> list[dict[str, Any]]:
    registry = runtime_registry if isinstance(runtime_registry, dict) else {}
    configured_packs = normalize_feature_pack_config(registry.get("featurePacks"))
    legacy_families = {
        str(item or "").strip()
        for item in list(registry.get("installedRuntimeFamilies") or [])
        if str(item or "").strip()
    }
    statuses: list[dict[str, Any]] = []
    for definition in sorted(FEATURE_PACK_DEFINITIONS, key=lambda item: item.recommended_order):
        configured = configured_packs[definition.id]
        target_dir = Path(str(configured.get("targetDir") or feature_pack_target_dir(definition.id))).expanduser()
        configured_status = str(configured.get("status") or "not_installed")
        legacy_runtime_match = bool(set(definition.runtime_families) & legacy_families)
        probe_match = _has_probe_modules(definition)
        target_exists = target_dir.exists()
        installed = (
            (configured_status == "installed" and target_exists)
            or legacy_runtime_match
            or probe_match
        )
        if installed:
            status = "installed"
        elif configured_status in {"installing", "failed"}:
            status = configured_status
        else:
            status = "not_installed"
        missing_reason = None
        if status == "not_installed":
            missing_reason = "not_installed"
        elif status == "failed":
            missing_reason = configured.get("lastError") or "install_failed"
        statuses.append(
            {
                "id": definition.id,
                "productName": definition.product_name,
                "shortName": definition.short_name,
                "description": definition.description,
                "hover": definition.hover,
                "recommendedOrder": definition.recommended_order,
                "runtimeFamilies": list(definition.runtime_families),
                "requirementsFile": str(feature_pack_requirements_path(definition.id)),
                "targetDir": str(target_dir),
                "status": status,
                "installed": status == "installed",
                "installPlatform": install_platform,
                "restartRequired": bool(configured.get("restartRequired", status == "installed")),
                "logRef": configured.get("logRef"),
                "lastError": configured.get("lastError"),
                "updatedAt": configured.get("updatedAt"),
                "missingReason": missing_reason,
                "source": "feature_pack"
                if configured_status == "installed" and target_exists
                else "legacy_runtime_families"
                if legacy_runtime_match
                else "import_probe"
                if probe_match
                else "config",
            }
        )
    return statuses


def installed_runtime_families_from_feature_packs(feature_pack_statuses: list[dict[str, Any]]) -> list[str]:
    families: list[str] = []
    for pack in feature_pack_statuses:
        if str(pack.get("status") or "") != "installed":
            continue
        for family in list(pack.get("runtimeFamilies") or []):
            normalized = str(family or "").strip()
            if normalized and normalized not in families:
                families.append(normalized)
    return families


def feature_pack_summary(feature_pack_statuses: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(feature_pack_statuses),
        "installed": sum(1 for item in feature_pack_statuses if item.get("status") == "installed"),
        "missing": sum(1 for item in feature_pack_statuses if item.get("status") == "not_installed"),
        "installing": sum(1 for item in feature_pack_statuses if item.get("status") == "installing"),
        "failed": sum(1 for item in feature_pack_statuses if item.get("status") == "failed"),
    }
