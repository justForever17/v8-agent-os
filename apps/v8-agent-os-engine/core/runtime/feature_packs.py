from __future__ import annotations

import importlib.util
import json
import platform
import re
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
    asset_manifest_file: str | None = None
    python_path_priority: str = "prepend"


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
        product_name="可选本地识别包",
        short_name="本地识别",
        description="为高性能本机提供本地语音转写、OCR 和媒体理解增强。",
        hover="适合电脑性能较高且不想依赖云供应商的用户；安装后按需接入本地语音转写、OCR、媒体/附件理解增强。",
        recommended_order=3,
        runtime_families=(),
        requirements_file="local-asr-ocr.txt",
        probe_modules=("faster_whisper", "paddleocr"),
    ),
    FeaturePackDefinition(
        id="creative_media_image_analysis",
        product_name="图像分析增强包",
        short_name="图像分析",
        description="为多媒体创作提供本地主体分割、透明度核验和跨图构图比较。",
        hover="安装后可离线复用已验签的 IS-Net 模型；仅在复杂不透明背景需要主体分割时使用。",
        recommended_order=4,
        runtime_families=(),
        requirements_file="creative-media-image-analysis.txt",
        probe_modules=("onnxruntime",),
        asset_manifest_file="creative-media-image-analysis.manifest.json",
        python_path_priority="fallback",
    ),
    FeaturePackDefinition(
        id="creative_media_motion_capture",
        product_name="动作采集能力包",
        short_name="动作采集",
        description="为多媒体创作提供单人视频动作提取、骨架预览和动作质量核验。",
        hover="安装后可离线使用已验签的 MediaPipe Holistic 模型；首版仅支持单人视频或摄像头录制文件。",
        recommended_order=5,
        runtime_families=(),
        requirements_file="creative-media-motion-capture.txt",
        probe_modules=("mediapipe", "cv2"),
        asset_manifest_file="creative-media-motion-capture.manifest.json",
        python_path_priority="fallback",
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


def feature_pack_asset_manifest_path(pack_id: str) -> Path | None:
    definition = FEATURE_PACK_BY_ID[str(pack_id)]
    if not definition.asset_manifest_file:
        return None
    return Path(__file__).resolve().parents[2] / "requirements" / "feature-packs" / definition.asset_manifest_file


def load_feature_pack_asset_manifest(pack_id: str) -> dict[str, Any] | None:
    path = feature_pack_asset_manifest_path(pack_id)
    if path is None or not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, dict) else None


def feature_pack_asset_root(pack_id: str, target_dir: str | Path | None = None) -> Path:
    target = Path(target_dir or feature_pack_target_dir(pack_id)).expanduser()
    return target.parent / "models"


def resolve_feature_pack_asset(pack_id: str, asset_id: str) -> Path | None:
    from core.storage import storage

    registry = storage.get_runtime_registry_config()
    configured = normalize_feature_pack_config(registry.get("featurePacks")).get(str(pack_id)) or {}
    if configured.get("status") != "installed":
        return None
    definition = FEATURE_PACK_BY_ID.get(str(pack_id))
    if definition and definition.asset_manifest_file:
        compatible, _, _, _ = _feature_pack_receipt_runtime_compatibility(pack_id, registry)
        if not compatible:
            return None
    manifest = load_feature_pack_asset_manifest(pack_id) or {}
    asset = next(
        (item for item in list(manifest.get("assets") or []) if str(item.get("id") or "") == str(asset_id)),
        None,
    )
    if not isinstance(asset, dict):
        return None
    relative_target = str(asset.get("target") or "").strip()
    if not relative_target:
        return None
    root = Path(str(configured.get("assetRoot") or feature_pack_asset_root(pack_id, configured.get("targetDir")))).expanduser()
    resolved = (root / relative_target).resolve(strict=False)
    try:
        resolved.relative_to(root.resolve(strict=False))
    except ValueError:
        return None
    return resolved if resolved.is_file() else None


def load_feature_pack_receipt(
    pack_id: str,
    runtime_registry: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if isinstance(runtime_registry, dict):
        registry = runtime_registry
    else:
        from core.storage import storage

        registry = storage.get_runtime_registry_config()
    configured = normalize_feature_pack_config(registry.get("featurePacks")).get(str(pack_id)) or {}
    if configured.get("status") != "installed":
        return None
    receipt_ref = str(configured.get("receiptRef") or "").strip()
    receipt_path = (
        Path(receipt_ref).expanduser()
        if receipt_ref
        else Path(str(configured.get("targetDir") or feature_pack_target_dir(pack_id))).expanduser().parent / "receipt.json"
    )
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or str(payload.get("packId") or "") != str(pack_id):
        return None
    return dict(payload)


def _normalized_architecture(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())
    aliases = {
        "x8664": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    return aliases.get(normalized, normalized)


def _python_minor(value: Any) -> tuple[int, int] | None:
    match = re.match(r"^\s*(\d+)\.(\d+)", str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _feature_pack_receipt_runtime_compatibility(
    pack_id: str,
    runtime_registry: dict[str, Any] | None = None,
) -> tuple[bool, str | None, str | None, dict[str, Any]]:
    receipt = load_feature_pack_receipt(pack_id, runtime_registry)
    if not isinstance(receipt, dict):
        return False, "receipt_missing", "能力包缺少有效安装回执，请重新安装。", {}
    environment = dict(receipt.get("environment") or {})
    receipt_version = str(environment.get("pythonVersion") or "").strip()
    runtime_version = platform.python_version()
    if _python_minor(receipt_version) != _python_minor(runtime_version):
        return (
            False,
            "python_abi_mismatch",
            f"能力包由 Python {receipt_version or 'unknown'} 安装，当前 Engine 使用 Python {runtime_version}；请重新安装能力包。",
            receipt,
        )
    receipt_implementation = str(environment.get("pythonImplementation") or "").strip().lower()
    runtime_implementation = platform.python_implementation().strip().lower()
    if not receipt_implementation or receipt_implementation != runtime_implementation:
        return (
            False,
            "python_implementation_mismatch",
            "能力包安装解释器与当前 Engine 的 Python 实现不一致，请重新安装能力包。",
            receipt,
        )
    receipt_architecture = _normalized_architecture(environment.get("architecture"))
    runtime_architecture = _normalized_architecture(platform.machine() or platform.architecture()[0])
    if not receipt_architecture or receipt_architecture != runtime_architecture:
        return (
            False,
            "python_architecture_mismatch",
            "能力包安装架构与当前 Engine 架构不一致，请重新安装能力包。",
            receipt,
        )
    return True, None, None, receipt


def preferred_feature_pack_execution_provider(
    pack_id: str,
    runtime_registry: dict[str, Any] | None = None,
) -> str:
    receipt = load_feature_pack_receipt(pack_id, runtime_registry)
    smoke_check = dict(receipt.get("smokeCheck") or {}) if isinstance(receipt, dict) else {}
    selected = str(smoke_check.get("selectedExecutionProvider") or "").strip()
    return selected if selected else "CPU"


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
            "version": str(raw_payload.get("version") or "").strip() or None,
            "assetRoot": str(raw_payload.get("assetRoot") or "").strip() or None,
            "receiptRef": str(raw_payload.get("receiptRef") or "").strip() or None,
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
    definitions = {definition.id: definition for definition in FEATURE_PACK_DEFINITIONS}
    for pack_id, pack_state in feature_packs.items():
        if str(pack_state.get("status") or "") != "installed":
            continue
        definition = definitions.get(pack_id)
        if definition and definition.asset_manifest_file:
            compatible, _, _, _ = _feature_pack_receipt_runtime_compatibility(pack_id, registry)
            if not compatible:
                continue
        target = Path(str(pack_state.get("targetDir") or feature_pack_target_dir(pack_id))).expanduser()
        if not target.exists():
            continue
        target_text = str(target)
        if target_text not in sys.path:
            if definition and definition.python_path_priority == "fallback":
                sys.path.append(target_text)
            else:
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
        probe_match = _has_probe_modules(definition) if not definition.asset_manifest_file else False
        legacy_runtime_verified = legacy_runtime_match and probe_match
        legacy_runtime_unverified = legacy_runtime_match and not probe_match
        target_exists = target_dir.exists()
        asset_manifest = load_feature_pack_asset_manifest(definition.id)
        receipt_compatible = True
        receipt_reason = None
        receipt_error = None
        receipt: dict[str, Any] = {}
        if asset_manifest and configured_status == "installed":
            receipt_compatible, receipt_reason, receipt_error, receipt = _feature_pack_receipt_runtime_compatibility(
                definition.id,
                registry,
            )
        asset_root = Path(str(configured.get("assetRoot") or feature_pack_asset_root(definition.id, target_dir))).expanduser()
        assets_exist = True
        if asset_manifest:
            assets_exist = all(
                (asset_root / str(item.get("target") or "")).is_file()
                for item in list(asset_manifest.get("assets") or [])
                if isinstance(item, dict)
            )
        installed = (
            (configured_status == "installed" and target_exists and assets_exist and receipt_compatible)
            or legacy_runtime_verified
            or probe_match
        )
        if installed:
            status = "installed"
        elif configured_status == "installed" and not receipt_compatible:
            status = "failed"
        elif configured_status in {"installing", "failed"}:
            status = configured_status
        else:
            status = "not_installed"
        missing_reason = None
        if status == "not_installed":
            missing_reason = "legacy_runtime_unverified" if legacy_runtime_unverified else "not_installed"
        elif status == "failed":
            missing_reason = receipt_reason or configured.get("lastError") or "install_failed"
        receipt_environment = dict(receipt.get("environment") or {})
        last_error = receipt_error or configured.get("lastError")
        restart_required = (
            False
            if status == "installed" and asset_manifest and receipt_compatible
            else bool(configured.get("restartRequired", status == "installed"))
        )
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
                "assetManifestFile": str(feature_pack_asset_manifest_path(definition.id) or "") or None,
                "targetDir": str(target_dir),
                "assetRoot": str(configured.get("assetRoot") or asset_root) if asset_manifest else None,
                "version": configured.get("version") or (asset_manifest or {}).get("version"),
                "receiptRef": configured.get("receiptRef"),
                "status": status,
                "installed": status == "installed",
                "installPlatform": install_platform,
                "restartRequired": restart_required,
                "logRef": configured.get("logRef"),
                "lastError": last_error,
                "updatedAt": configured.get("updatedAt"),
                "missingReason": missing_reason,
                "receiptPythonVersion": receipt_environment.get("pythonVersion"),
                "runtimePythonVersion": platform.python_version(),
                "source": "feature_pack"
                if configured_status == "installed" and target_exists
                else "legacy_runtime_families_verified"
                if legacy_runtime_verified
                else "legacy_runtime_families_unverified"
                if legacy_runtime_unverified
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
