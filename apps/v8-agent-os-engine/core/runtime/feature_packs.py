from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import platform
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from core.v8_agent_os_paths import RUNTIME_DATA_HOME, V8_AGENT_OS_HOME


FEATURE_PACK_STATUS_VALUES = {"installed", "not_installed", "installing", "failed"}
PACKED_RUNTIME_FAMILIES = {"computer_use", "desktop_live", "rpa"}
FEATURE_PACK_PYTHON_ROOT = RUNTIME_DATA_HOME / "feature-packs"
FEATURE_PACK_LOG_ROOT = V8_AGENT_OS_HOME / "logs" / "feature-packs"
_FEATURE_PACK_BOOTSTRAP_COMPLETE = False
_FEATURE_PACKS_READY_AT_BOOT: set[str] = set()


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
    lock_file_prefix: str | None = None
    asset_manifest_file: str | None = None
    python_path_priority: str = "fallback"
    enabled: bool = True


FEATURE_PACK_DEFINITIONS: tuple[FeaturePackDefinition, ...] = (
    FeaturePackDefinition(
        id="document_ingestion",
        product_name="文档读取能力包",
        short_name="文档读取",
        description="启用 DOCX、XLS/XLSX、PPTX 和 PDF 的本地解析。",
        hover="安装后 read_native_file 与记忆入库可读取现代 Office 文档和 PDF；中文环境自动优先可信镜像。",
        recommended_order=1,
        runtime_families=(),
        requirements_file="document-ingestion.txt",
        probe_modules=("pandas", "openpyxl", "xlrd", "docx", "pptx", "fitz", "tabulate"),
    ),
    FeaturePackDefinition(
        id="computer_use_desktop",
        product_name="桌面操作能力包",
        short_name="桌面操作",
        description="启用桌面截图、窗口识别、点击输入和桌面直播采集。",
        hover="安装后接入桌面操作与 Desktop Live；适合需要 V8OS 观察并操作本机应用的场景。",
        recommended_order=2,
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
        recommended_order=3,
        runtime_families=("rpa",),
        requirements_file="rpa-automation.txt",
        probe_modules=("robot", "RPA", "RPA.Browser.Selenium", "RPA.Excel.Files"),
        lock_file_prefix="rpa-automation-cp311",
    ),
    FeaturePackDefinition(
        id="local_asr_ocr",
        product_name="可选本地识别包",
        short_name="本地识别",
        description="为高性能本机提供本地语音转写、OCR 和媒体理解增强。",
        hover="适合电脑性能较高且不想依赖云供应商的用户；安装后按需接入本地语音转写、OCR、媒体/附件理解增强。",
        recommended_order=4,
        runtime_families=(),
        requirements_file="local-asr-ocr.txt",
        probe_modules=("faster_whisper", "paddleocr"),
        enabled=False,
    ),
    FeaturePackDefinition(
        id="creative_media_image_analysis",
        product_name="图像分析增强包",
        short_name="图像分析",
        description="为多媒体创作提供本地主体分割、透明度核验和跨图构图比较。",
        hover="安装后可离线复用已验签的 IS-Net 模型；仅在复杂不透明背景需要主体分割时使用。",
        recommended_order=5,
        runtime_families=(),
        requirements_file="creative-media-image-analysis.txt",
        probe_modules=("onnxruntime",),
        lock_file_prefix="creative-media-image-analysis-cp311",
        asset_manifest_file="creative-media-image-analysis.manifest.json",
    ),
    FeaturePackDefinition(
        id="creative_media_motion_capture",
        product_name="动作采集能力包",
        short_name="动作采集",
        description="为多媒体创作提供单人视频动作提取、骨架预览和动作质量核验。",
        hover="安装后可离线使用已验签的 MediaPipe Holistic 模型；首版仅支持单人视频或摄像头录制文件。",
        recommended_order=6,
        runtime_families=(),
        requirements_file="creative-media-motion-capture.txt",
        probe_modules=("mediapipe", "cv2"),
        asset_manifest_file="creative-media-motion-capture.manifest.json",
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


def feature_pack_lock_path(pack_id: str) -> Path | None:
    definition = FEATURE_PACK_BY_ID[str(pack_id)]
    if not definition.lock_file_prefix:
        return None
    if _python_minor(platform.python_version()) != (3, 11):
        return None
    platform_name = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(
        _normalized_platform(sys.platform)
    )
    architecture = {"amd64": "x64", "arm64": "arm64"}.get(
        _normalized_architecture(platform.machine() or platform.architecture()[0])
    )
    if not platform_name or not architecture:
        return None
    return (
        Path(__file__).resolve().parents[2]
        / "requirements"
        / "feature-packs"
        / "locks"
        / f"{definition.lock_file_prefix}-{platform_name}-{architecture}.txt"
    )


def _hashed_lock_file_is_valid(path: Path | None) -> bool:
    if path is None or not path.is_file():
        return False
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError):
        return False
    names: set[str] = set()
    for line in lines:
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s]+) --hash=sha256:([0-9a-f]{64})", line)
        if not match:
            return False
        normalized = _normalized_package_name(match.group(1))
        if normalized in names:
            return False
        names.add(normalized)
    return bool(lines)


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
        "x64": "amd64",
        "x8664": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    return aliases.get(normalized, normalized)


def _normalized_platform(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "windows": "win32",
        "win32": "win32",
        "mac": "darwin",
        "macos": "darwin",
        "darwin": "darwin",
        "linux": "linux",
    }
    return aliases.get(normalized, normalized)


def _normalized_package_name(value: Any) -> str:
    return re.sub(r"[-_.]+", "-", str(value or "").strip().lower())


def _resolved_packages_sha256(packages: list[str]) -> str:
    return hashlib.sha256("\n".join(packages).encode("utf-8")).hexdigest()


def _resolved_packages_snapshot(target_dir: Path) -> tuple[list[str], str] | None:
    try:
        packages: set[str] = set()
        for distribution in importlib_metadata.distributions(path=[str(target_dir)]):
            name = _normalized_package_name(distribution.metadata.get("Name"))
            version = str(distribution.version or "").strip()
            if not name or not version:
                return None
            packages.add(f"{name}=={version}")
    except Exception:
        return None
    normalized = sorted(packages)
    if not normalized:
        return None
    return normalized, _resolved_packages_sha256(normalized)


def _receipt_resolved_packages(receipt: dict[str, Any]) -> tuple[list[str], str] | None:
    resolved = receipt.get("resolvedPackages")
    if not isinstance(resolved, dict):
        return None
    raw_packages = resolved.get("packages")
    raw_sha256 = str(resolved.get("sha256") or "").strip().lower()
    if (
        not isinstance(raw_packages, list)
        or not raw_packages
        or not re.fullmatch(r"[0-9a-f]{64}", raw_sha256)
        or not all(isinstance(item, str) and item and item == item.strip() for item in raw_packages)
    ):
        return None
    packages = list(raw_packages)
    return packages, raw_sha256


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


@lru_cache(maxsize=64)
def _sha256_file_identity(path_text: str, size: int, modified_ns: int) -> str | None:
    del size, modified_ns
    return _sha256_file(Path(path_text))


def _asset_manifest_files_are_valid(asset_root: Path, manifest: dict[str, Any]) -> bool:
    assets = [item for item in list(manifest.get("assets") or []) if isinstance(item, dict)]
    if not assets:
        return False
    resolved_root = asset_root.resolve(strict=False)
    for item in assets:
        relative_target = str(item.get("target") or "").strip()
        expected_sha256 = str(item.get("sha256") or "").strip().lower()
        try:
            expected_size = int(item.get("size"))
        except (TypeError, ValueError):
            return False
        if not relative_target or expected_size <= 0 or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            return False
        candidate = (resolved_root / relative_target).resolve(strict=False)
        try:
            candidate.relative_to(resolved_root)
            stat = candidate.stat()
        except (OSError, ValueError):
            return False
        if not candidate.is_file() or stat.st_size != expected_size:
            return False
        actual_sha256 = _sha256_file_identity(str(candidate), stat.st_size, stat.st_mtime_ns)
        if actual_sha256 is None or actual_sha256.lower() != expected_sha256:
            return False
    return True


def _receipt_manifest_version(receipt: dict[str, Any]) -> str:
    manifest = receipt.get("manifest")
    if isinstance(manifest, dict):
        nested = str(manifest.get("version") or "").strip()
        if nested:
            return nested
    return str(receipt.get("manifestVersion") or receipt.get("packVersion") or "").strip()


def _python_minor(value: Any) -> tuple[int, int] | None:
    match = re.match(r"^\s*(\d+)\.(\d+)", str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _feature_pack_receipt_runtime_compatibility(
    pack_id: str,
    runtime_registry: dict[str, Any] | None = None,
) -> tuple[bool, str | None, str | None, dict[str, Any]]:
    if isinstance(runtime_registry, dict):
        registry = runtime_registry
    else:
        from core.storage import storage

        registry = storage.get_runtime_registry_config()
    definition = FEATURE_PACK_BY_ID.get(str(pack_id))
    receipt = load_feature_pack_receipt(pack_id, registry)
    if not isinstance(receipt, dict):
        return False, "receipt_missing", "能力包缺少有效安装回执，请重新安装。", {}
    environment = dict(receipt.get("environment") or {})
    receipt_platform = _normalized_platform(environment.get("platform"))
    runtime_platform = _normalized_platform(sys.platform)
    if not receipt_platform or receipt_platform != runtime_platform:
        return (
            False,
            "platform_mismatch",
            "能力包安装平台与当前 Engine 平台不一致，请重新安装能力包。",
            receipt,
        )
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
    requirements = receipt.get("requirements")
    receipt_requirements_sha = (
        str(requirements.get("sha256") or "").strip().lower()
        if isinstance(requirements, dict)
        else ""
    )
    current_requirements_sha = _sha256_file(feature_pack_requirements_path(pack_id))
    if (
        not receipt_requirements_sha
        or current_requirements_sha is None
        or receipt_requirements_sha != current_requirements_sha
    ):
        return (
            False,
            "requirements_mismatch",
            "能力包安装回执与当前依赖清单不一致，请重新安装能力包。",
            receipt,
        )
    if definition and definition.lock_file_prefix:
        current_lock = feature_pack_lock_path(pack_id)
        receipt_lock_file = str(requirements.get("lockFile") or "").strip()
        receipt_lock_sha = str(requirements.get("lockSha256") or "").strip().lower()
        current_lock_sha = _sha256_file(current_lock) if current_lock is not None else None
        if (
            not _hashed_lock_file_is_valid(current_lock)
            or receipt_lock_file != current_lock.name
            or not receipt_lock_sha
            or current_lock_sha != receipt_lock_sha
        ):
            return (
                False,
                "requirements_lock_mismatch",
                "能力包安装回执与当前平台依赖锁不一致，请重新安装能力包。",
                receipt,
            )
    if definition and definition.asset_manifest_file:
        manifest = load_feature_pack_asset_manifest(pack_id)
        expected_manifest_version = str((manifest or {}).get("version") or "").strip()
        if not expected_manifest_version or _receipt_manifest_version(receipt) != expected_manifest_version:
            return (
                False,
                "asset_manifest_mismatch",
                "能力包安装回执与当前资产清单版本不一致，请重新安装能力包。",
                receipt,
            )
    receipt_packages = _receipt_resolved_packages(receipt)
    if receipt_packages is None:
        return (
            False,
            "resolved_packages_missing",
            "能力包安装回执缺少已解析依赖清单，请重新安装能力包。",
            receipt,
        )
    expected_packages, expected_packages_sha256 = receipt_packages
    if _resolved_packages_sha256(expected_packages) != expected_packages_sha256:
        return (
            False,
            "resolved_packages_mismatch",
            "能力包已解析依赖清单与安装回执摘要不一致，请重新安装能力包。",
            receipt,
        )
    configured = normalize_feature_pack_config(registry.get("featurePacks")).get(str(pack_id)) or {}
    target_dir = Path(str(configured.get("targetDir") or feature_pack_target_dir(pack_id))).expanduser()
    if target_dir.exists():
        actual_packages = _resolved_packages_snapshot(target_dir)
        if actual_packages != (expected_packages, expected_packages_sha256):
            return (
                False,
                "resolved_packages_mismatch",
                "能力包已解析依赖与当前安装目录不一致，请重新安装能力包。",
                receipt,
            )
    return True, None, None, receipt


def preferred_feature_pack_execution_provider(
    pack_id: str,
    runtime_registry: dict[str, Any] | None = None,
) -> str:
    compatible, _, _, receipt = _feature_pack_receipt_runtime_compatibility(pack_id, runtime_registry)
    if not compatible:
        return "CPU"
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
            "operationId": str(raw_payload.get("operationId") or "").strip() or None,
            "startedAt": str(raw_payload.get("startedAt") or "").strip() or None,
        }
    return result


def _module_importable(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (AttributeError, ImportError, ValueError):
        return False


def _module_identity_in_target(target_dir: Path, module_name: str) -> tuple[Any, ...] | None:
    search_locations = [str(target_dir)]
    parts = [part for part in module_name.split(".") if part]
    if not parts:
        return None
    final_spec = None
    for index in range(len(parts)):
        qualified_name = ".".join(parts[: index + 1])
        try:
            spec = importlib.machinery.PathFinder.find_spec(qualified_name, search_locations)
        except (AttributeError, ImportError, OSError, ValueError):
            return None
        if spec is None:
            return None
        final_spec = spec
        if index < len(parts) - 1:
            if not spec.submodule_search_locations:
                return None
            search_locations = [str(location) for location in spec.submodule_search_locations]
    if final_spec is None:
        return None
    candidates = []
    if final_spec.origin and final_spec.origin not in {"built-in", "frozen"}:
        candidates.append(Path(final_spec.origin))
    candidates.extend(Path(location) for location in list(final_spec.submodule_search_locations or []))
    resolved_root = target_dir.resolve(strict=False)
    identities: list[tuple[str, int, int]] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(resolved_root)
            stat = resolved.stat()
        except (OSError, ValueError):
            continue
        identities.append((str(resolved), stat.st_size, stat.st_mtime_ns))
    return (module_name, *identities) if identities else None


def _has_probe_modules(definition: FeaturePackDefinition, target_dir: Path | None = None) -> bool:
    probe_modules = list(definition.probe_modules)
    if definition.id == "computer_use_desktop" and sys.platform == "win32":
        probe_modules.extend(["pywinauto", "pycaw"])
    if definition.id == "rpa_automation" and sys.platform == "win32":
        probe_modules.append("RPA.Windows")
    if not probe_modules:
        return False
    if target_dir is not None:
        return all(_module_identity_in_target(target_dir, module_name) is not None for module_name in probe_modules)
    return all(_module_importable(module_name) for module_name in probe_modules)


def apply_feature_pack_python_paths(runtime_registry: dict[str, Any] | None = None) -> list[str]:
    global _FEATURE_PACK_BOOTSTRAP_COMPLETE

    registry = runtime_registry if isinstance(runtime_registry, dict) else {}
    feature_packs = normalize_feature_pack_config(registry.get("featurePacks"))
    added: list[str] = []
    definitions = {definition.id: definition for definition in FEATURE_PACK_DEFINITIONS}
    capture_boot_readiness = not _FEATURE_PACK_BOOTSTRAP_COMPLETE
    for pack_id, pack_state in feature_packs.items():
        if str(pack_state.get("status") or "") != "installed":
            continue
        definition = definitions.get(pack_id)
        if definition is None or not definition.enabled:
            continue
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
        if capture_boot_readiness and definition and _has_probe_modules(definition, target):
            _FEATURE_PACKS_READY_AT_BOOT.add(pack_id)
    if capture_boot_readiness:
        _FEATURE_PACK_BOOTSTRAP_COMPLETE = True
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
        definition_enabled = definition.enabled
        target_dir = Path(str(configured.get("targetDir") or feature_pack_target_dir(definition.id))).expanduser()
        configured_status = str(configured.get("status") or "not_installed")
        legacy_runtime_match = bool(set(definition.runtime_families) & legacy_families)
        receipt_governed = configured_status == "installed"
        ambient_probe_match = (
            _has_probe_modules(definition)
            if definition_enabled
            and configured_status == "not_installed"
            and not definition.asset_manifest_file
            and not configured.get("receiptRef")
            else False
        )
        legacy_runtime_unverified = legacy_runtime_match
        target_exists = target_dir.exists()
        asset_manifest = load_feature_pack_asset_manifest(definition.id)
        lock_available = _hashed_lock_file_is_valid(feature_pack_lock_path(definition.id)) if definition.lock_file_prefix else True
        receipt_compatible = True
        receipt_reason = None
        receipt_error = None
        receipt: dict[str, Any] = {}
        if receipt_governed and configured_status == "installed":
            receipt_compatible, receipt_reason, receipt_error, receipt = _feature_pack_receipt_runtime_compatibility(
                definition.id,
                registry,
            )
        asset_root = Path(str(configured.get("assetRoot") or feature_pack_asset_root(definition.id, target_dir))).expanduser()
        assets_valid = True
        if asset_manifest:
            assets_valid = _asset_manifest_files_are_valid(asset_root, asset_manifest)
        installed_probe_match = target_exists and _has_probe_modules(definition, target_dir)
        installed = definition_enabled and (
            configured_status == "installed"
            and target_exists
            and assets_valid
            and receipt_compatible
            and installed_probe_match
        )
        if not definition_enabled:
            status = "not_installed"
        elif installed:
            status = "installed"
        elif configured_status == "installed":
            status = "failed"
        elif configured_status in {"installing", "failed"}:
            status = configured_status
        else:
            status = "not_installed"
        missing_reason = None
        if status == "not_installed":
            missing_reason = (
                "disabled"
                if not definition_enabled
                else "requirements_lock_unavailable"
                if not lock_available
                else "legacy_runtime_unverified"
                if legacy_runtime_unverified
                else "ambient_runtime_unverified"
                if ambient_probe_match
                else "not_installed"
            )
        elif status == "failed":
            missing_reason = (
                receipt_reason
                or ("target_missing" if not target_exists else None)
                or ("assets_invalid" if not assets_valid else None)
                or ("runtime_probe_failed" if not installed_probe_match else None)
                or configured.get("lastError")
                or "install_failed"
            )
        receipt_environment = dict(receipt.get("environment") or {})
        last_error = (
            receipt_error
            or ("能力包安装目录不存在，请重新安装能力包。" if status == "failed" and not target_exists else None)
            or ("能力包资产文件缺失或校验失败，请重新安装能力包。" if status == "failed" and not assets_valid else None)
            or ("能力包运行模块不可用，请重新安装能力包。" if status == "failed" and not installed_probe_match else None)
            or configured.get("lastError")
        )
        if not definition_enabled or status == "not_installed":
            restart_required = False
        elif (
            status == "installed"
            and receipt_compatible
            and definition.id in _FEATURE_PACKS_READY_AT_BOOT
        ):
            restart_required = False
        else:
            restart_required = bool(configured.get("restartRequired", status == "installed"))
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
                 "installable": definition_enabled and lock_available,
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
                else "ambient_import_probe_unverified"
                if ambient_probe_match
                else "legacy_runtime_families_unverified"
                if legacy_runtime_unverified
                else "config",
            }
        )
    return statuses


def installed_runtime_families_from_feature_packs(feature_pack_statuses: list[dict[str, Any]]) -> list[str]:
    families: list[str] = []
    for pack in feature_pack_statuses:
        if str(pack.get("status") or "") != "installed" or bool(pack.get("restartRequired")):
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
