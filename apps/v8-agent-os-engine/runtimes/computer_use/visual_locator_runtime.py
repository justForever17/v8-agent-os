from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.system_base import get_desktop_tools_config
from core.workspace_guard import ensure_workspace_auto_create_allowed
from core.workspace_resolution import workspace_resolution_service
from core.v8_agent_os_paths import ensure_v8_agent_os_tmp_path
from erc.runtime_context import get_runtime_context
from PIL import Image, ImageOps

ENGINE_ROOT = Path(__file__).resolve().parents[2]


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return int(default)


def _normalize_confidence(confidence: Any) -> float | None:
    if confidence in (None, ""):
        return None
    try:
        numeric = float(confidence)
    except Exception:
        return None
    if numeric <= 1.0:
        numeric *= 100.0
    return max(1.0, min(100.0, numeric))


def _resolve_image_locator_path(raw_value: str) -> str:
    token = str(raw_value or "").strip()
    if not token:
        return token
    candidate = Path(token).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    workspace_probe_root: Path | None = None
    try:
        workspace_probe_root = _resolve_workspace_root()
    except Exception:
        workspace_probe_root = None
    probe_paths = [
        (workspace_probe_root / candidate) if workspace_probe_root else None,
        Path.cwd() / candidate,
        ENGINE_ROOT / candidate,
        ENGINE_ROOT / "scripts" / candidate,
    ]
    for probe in probe_paths:
        if probe is None:
            continue
        if probe.exists():
            return str(probe.resolve())
    if str(candidate).startswith("assets") and workspace_probe_root is not None:
        preferred_probe = workspace_probe_root / candidate
    else:
        preferred_probe = ENGINE_ROOT / "scripts" / candidate if str(candidate).startswith("assets") else ENGINE_ROOT / candidate
    return str(preferred_probe.resolve())


def _resolve_workspace_root() -> Path:
    runtime_context = get_runtime_context()
    resolved = workspace_resolution_service.resolve_workspace_path(
        runtime_kind="computer_use",
        session_id=str(runtime_context.get("session_id") or "") or None,
        explicit_workspace_id=str(runtime_context.get("workspace_id") or "") or None,
        explicit_project_id=str(runtime_context.get("project_id") or "") or None,
        explicit_workspace_path=str(runtime_context.get("workspace_path") or "") or None,
    )
    return ensure_workspace_auto_create_allowed(
        Path(resolved).expanduser(),
        source="computer_use.visual_locator_runtime._resolve_workspace_root",
        allow_missing=True,
    )


@lru_cache(maxsize=1)
def _preferred_tessdata_dir() -> Path | None:
    workspace_tessdata = _resolve_workspace_root() / ".v8-agent-os-ocr" / "tessdata"
    if workspace_tessdata.exists():
        return workspace_tessdata
    system_candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tessdata"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tessdata"),
    ]
    env_value = str((get_desktop_tools_config().get("tessdataPrefix") or "")).strip()
    if env_value:
        env_path = Path(env_value).expanduser()
        if env_path.exists():
            system_candidates.insert(0, env_path)
    for candidate in system_candidates:
        if candidate.exists():
            return candidate
    return None


@lru_cache(maxsize=1)
def _resolve_tesseract_executable() -> str | None:
    detected = shutil.which("tesseract")
    if detected:
        return detected
    env_value = str((get_desktop_tools_config().get("tesseractPath") or "")).strip()
    candidates: List[Path] = []
    if env_value:
        candidates.append(Path(env_value).expanduser())
    candidates.extend(
        [
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


@lru_cache(maxsize=1)
def _resolve_tesseract_install_root() -> Path | None:
    executable = _resolve_tesseract_executable()
    if not executable:
        return None
    try:
        return Path(executable).resolve().parent
    except Exception:
        return None


@lru_cache(maxsize=8)
def _resolve_tesseract_config(name: str) -> str | None:
    token = str(name or "").strip()
    if not token:
        return None
    install_root = _resolve_tesseract_install_root()
    if install_root is None:
        return None
    candidate = install_root / "tessdata" / "configs" / token
    if candidate.exists():
        return str(candidate)
    return None


@lru_cache(maxsize=1)
def _available_tesseract_languages() -> List[str]:
    tessdata_dir = _preferred_tessdata_dir()
    if not _resolve_tesseract_executable():
        return []
    languages: List[str] = []
    if tessdata_dir and tessdata_dir.exists():
        for item in tessdata_dir.glob("*.traineddata"):
            name = item.stem.strip()
            if name:
                languages.append(name)
    return sorted(dict.fromkeys(languages))


def _preferred_tesseract_lang() -> str:
    languages = set(_available_tesseract_languages())
    ordered: List[str] = []
    if "chi_sim" in languages:
        ordered.append("chi_sim")
    if "eng" in languages:
        ordered.append("eng")
    if not ordered:
        ordered.append("eng")
    return "+".join(ordered)


def resolve_visual_locator_asset_path(locator: Any) -> Path | None:
    normalized = _normalize_locator(locator)
    if not normalized or not normalized.startswith("image:"):
        return None
    return Path(normalized.split(":", 1)[1]).expanduser()


def _normalize_locator(locator: Any) -> str:
    raw = str(locator or "").strip()
    if not raw:
        return raw
    if ":" not in raw:
        return raw
    scheme, value = raw.split(":", 1)
    normalized_scheme = scheme.strip().lower()
    normalized_value = value.strip()
    if normalized_scheme == "image":
        normalized_value = _resolve_image_locator_path(normalized_value)
    return f"{normalized_scheme}:{normalized_value}"


def _normalize_ocr_query(locator: str) -> str | None:
    raw = str(locator or "").strip()
    if not raw or ":" not in raw:
        return None
    scheme, value = raw.split(":", 1)
    if scheme.strip().lower() not in {"ocr", "text"}:
        return None
    query = str(value or "").strip()
    return query or None


def _compact_ocr_text(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _geometry_to_match(geometry: Any) -> Dict[str, Any]:
    left = _to_int(getattr(geometry, "left", 0))
    top = _to_int(getattr(geometry, "top", 0))
    right = _to_int(getattr(geometry, "right", left))
    bottom = _to_int(getattr(geometry, "bottom", top))
    width = max(0, right - left)
    height = max(0, bottom - top)
    center = [left + width // 2, top + height // 2]
    return {
        "bbox": [left, top, right, bottom],
        "width": width,
        "height": height,
        "center": center,
    }


def _normalize_bounds(value: Any) -> List[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = [_to_int(item) for item in value]
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _offset_match_to_global(match: Dict[str, Any], *, left: int, top: int) -> Dict[str, Any]:
    payload = dict(match or {})
    bbox = list(payload.get("bbox") or [])
    center = list(payload.get("center") or [])
    if len(bbox) == 4:
        payload["bbox"] = [
            int(bbox[0]) + left,
            int(bbox[1]) + top,
            int(bbox[2]) + left,
            int(bbox[3]) + top,
        ]
    if len(center) == 2:
        payload["center"] = [int(center[0]) + left, int(center[1]) + top]
    return payload


def _run_tesseract_tsv(*, image_path: Path, tessdata_dir: Path | None, psm: int = 11) -> List[Dict[str, Any]]:
    tesseract_executable = _resolve_tesseract_executable()
    if not tesseract_executable:
        raise RuntimeError("当前环境未检测到 tesseract 可执行文件。")
    with tempfile.TemporaryDirectory(
        prefix="v8chat-tesseract-tsv-",
        dir=str(ensure_v8_agent_os_tmp_path(scope="computer_use")),
    ) as temp_dir:
        output_base = Path(temp_dir) / "ocr"
        tsv_config = _resolve_tesseract_config("tsv")
        command = [
            tesseract_executable,
            str(image_path),
            str(output_base),
            "--tessdata-dir",
            str(tessdata_dir) if tessdata_dir is not None else str((_preferred_tessdata_dir() or Path()).resolve()),
            "--psm",
            str(max(3, int(psm or 11))),
            "-l",
            _preferred_tesseract_lang(),
        ]
        if tsv_config:
            command.append(tsv_config)
        else:
            command.append("tsv")
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        tsv_path = output_base.with_suffix(".tsv")
        if not tsv_path.exists():
            return []
        lines = [line for line in tsv_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        return []
    headers = lines[0].split("\t")
    rows: List[Dict[str, Any]] = []
    for line in lines[1:]:
        values = line.split("\t")
        if len(values) < len(headers):
            values.extend([""] * (len(headers) - len(values)))
        row = dict(zip(headers, values))
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        try:
            left = _to_int(row.get("left"), 0)
            top = _to_int(row.get("top"), 0)
            width = max(0, _to_int(row.get("width"), 0))
            height = max(0, _to_int(row.get("height"), 0))
            confidence = float(str(row.get("conf") or "-1").strip() or "-1")
        except Exception:
            continue
        rows.append(
            {
                "text": text,
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "width": width,
                "height": height,
                "confidence": confidence,
                "line_num": str(row.get("line_num") or ""),
                "block_num": str(row.get("block_num") or ""),
                "par_num": str(row.get("par_num") or ""),
            }
        )
    return rows


def _image_size(image_path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(image_path) as source:
            return source.size
    except Exception:
        return None


def _bounds_fit_image(bounds: List[int] | None, *, image_size: tuple[int, int] | None) -> bool:
    if bounds is None or image_size is None or len(bounds) != 4:
        return False
    left, top, right, bottom = [int(v) for v in bounds]
    width, height = image_size
    return 0 <= left < right <= int(width) and 0 <= top < bottom <= int(height)


def _ocr_read_region(
    *,
    image_path: Path,
    bbox: List[int] | None = None,
    psm: int | None = None,
    variant: str = "default",
) -> Dict[str, Any]:
    tessdata_dir = _preferred_tessdata_dir()
    working_image_path = image_path
    temp_path: Path | None = None
    scale_factor = 2.0
    try:
        with Image.open(image_path) as source:
            prepared = source
            if bbox is not None and len(bbox) == 4:
                left, top, right, bottom = [int(v) for v in bbox]
                prepared = source.crop((left, top, right, bottom))
            prepared = ImageOps.grayscale(prepared)
            prepared = ImageOps.autocontrast(prepared)
            normalized_variant = str(variant or "default").strip().lower() or "default"
            if normalized_variant in {"invert", "invert_threshold"}:
                prepared = ImageOps.invert(prepared)
            if normalized_variant in {"threshold", "invert_threshold"}:
                prepared = prepared.point(lambda pixel: 255 if int(pixel) >= 150 else 0)
            prepared = prepared.resize((max(1, prepared.width * int(scale_factor)), max(1, prepared.height * int(scale_factor))))
            fd, temp_name = tempfile.mkstemp(
                prefix="v8chat-ocr-",
                suffix=".png",
                dir=str(ensure_v8_agent_os_tmp_path(scope="computer_use")),
            )
            os.close(fd)
            temp_path = Path(temp_name)
            prepared.save(temp_path)
        working_image_path = temp_path
        resolved_psm = int(psm) if psm is not None else (7 if bbox is not None else 11)
        rows = _run_tesseract_tsv(
            image_path=working_image_path,
            tessdata_dir=tessdata_dir,
            psm=resolved_psm,
        )
        normalized_rows: List[Dict[str, Any]] = []
        for row in rows:
            normalized_row = dict(row)
            for key in ("left", "top", "right", "bottom", "width", "height"):
                if key in normalized_row:
                    normalized_row[key] = int(round(float(normalized_row[key]) / scale_factor))
            normalized_rows.append(normalized_row)
        text = "".join(item["text"] for item in normalized_rows).strip()
        return {
            "text": text,
            "rows": normalized_rows,
            "languages": _preferred_tesseract_lang(),
            "tessdataDir": str(tessdata_dir) if tessdata_dir is not None else None,
            "variant": str(variant or "default").strip().lower() or "default",
            "psm": resolved_psm,
        }
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


def _locate_ocr_query_in_image(
    *,
    image_path: Path,
    query: str,
    search_bounds: List[int] | None = None,
) -> Dict[str, Any]:
    image_size = _image_size(image_path)
    local_bbox = list(search_bounds) if _bounds_fit_image(search_bounds, image_size=image_size) else None
    normalized_query = _compact_ocr_text(query)
    pass_plan: List[tuple[str, int | None]] = []
    if local_bbox is not None:
        pass_plan.extend(
            [
                ("default", 7),
                ("invert", 7),
                ("threshold", 7),
                ("invert_threshold", 7),
                ("default", 11),
                ("invert", 11),
            ]
        )
    else:
        pass_plan.extend(
            [
                ("default", 11),
                ("invert", 11),
                ("threshold", 11),
            ]
        )

    matched_segments: List[Dict[str, Any]] = []
    ocr_passes: List[Dict[str, Any]] = []
    best_payload: Dict[str, Any] | None = None
    best_row_count = -1

    for variant_name, variant_psm in pass_plan:
        ocr_payload = _ocr_read_region(
            image_path=image_path,
            bbox=local_bbox,
            psm=variant_psm,
            variant=variant_name,
        )
        rows = list(ocr_payload.get("rows") or [])
        ocr_passes.append(
            {
                "variant": ocr_payload.get("variant"),
                "psm": ocr_payload.get("psm"),
                "rowCount": len(rows),
                "text": str(ocr_payload.get("text") or ""),
            }
        )
        if len(rows) > best_row_count:
            best_payload = dict(ocr_payload)
            best_row_count = len(rows)

        grouped_rows: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            row_text = str(row.get("text") or "").strip()
            if not row_text:
                continue
            key = (
                str(row.get("block_num") or ""),
                str(row.get("par_num") or ""),
                str(row.get("line_num") or ""),
            )
            grouped_rows.setdefault(key, []).append(row)
        for line_rows in grouped_rows.values():
            ordered = sorted(
                line_rows,
                key=lambda item: (int(item.get("left") or 0), int(item.get("top") or 0)),
            )
            for start_index in range(len(ordered)):
                combined_text = ""
                left = top = right = bottom = None
                confidences: List[float] = []
                for end_index in range(start_index, len(ordered)):
                    item = ordered[end_index]
                    text = str(item.get("text") or "").strip()
                    if not text:
                        continue
                    combined_text += text
                    left = int(item["left"]) if left is None else min(left, int(item["left"]))
                    top = int(item["top"]) if top is None else min(top, int(item["top"]))
                    right = int(item["right"]) if right is None else max(right, int(item["right"]))
                    bottom = int(item["bottom"]) if bottom is None else max(bottom, int(item["bottom"]))
                    confidences.append(float(item.get("confidence") or 0.0))
                    normalized_text = _compact_ocr_text(combined_text)
                    if normalized_query and normalized_query in normalized_text:
                        matched_segments.append(
                            {
                                "text": combined_text,
                                "left": left,
                                "top": top,
                                "right": right,
                                "bottom": bottom,
                                "width": max(0, int(right) - int(left)),
                                "height": max(0, int(bottom) - int(top)),
                                "center": [
                                    int(left) + max(0, int(right) - int(left)) // 2,
                                    int(top) + max(0, int(bottom) - int(top)) // 2,
                                ],
                                "confidence": max(confidences) if confidences else 0.0,
                                "variant": str(ocr_payload.get("variant") or variant_name),
                                "psm": int(ocr_payload.get("psm") or (variant_psm or 0)),
                            }
                        )
    ocr_payload = best_payload or {
        "text": "",
        "rows": [],
        "variant": None,
        "psm": None,
        "languages": _preferred_tesseract_lang(),
        "tessdataDir": str(_preferred_tessdata_dir()) if _preferred_tessdata_dir() is not None else None,
    }
    rows = list(ocr_payload.get("rows") or [])
    deduped_segments: List[Dict[str, Any]] = []
    seen_segments: set[tuple[str, int, int, int, int]] = set()
    for segment in matched_segments:
        key = (
            str(segment.get("text") or "").strip(),
            int(segment.get("left") or 0),
            int(segment.get("top") or 0),
            int(segment.get("right") or 0),
            int(segment.get("bottom") or 0),
        )
        if key in seen_segments:
            continue
        seen_segments.add(key)
        deduped_segments.append(segment)
    matched_segments = deduped_segments
    if not matched_segments:
        return {
            "providerId": "rpa_desktop_visual_locator",
            "status": "resolved",
            "locator": f"ocr:{query}",
            "matchCount": 0,
            "matches": [],
            "readText": str(ocr_payload.get("text") or ""),
            "readTextError": None,
            "ocrRows": rows,
            "ocrPasses": ocr_passes,
            "searchImagePath": str(image_path),
            "searchBounds": list(search_bounds or []),
            "searchMode": "captured_window_region_ocr" if local_bbox is not None else "captured_window_ocr",
        }
    matched_segments.sort(
        key=lambda item: (
            0 if str(item.get("text") or "").strip() == str(query or "").strip() else 1,
            abs(len(str(item.get("text") or "")) - len(str(query or ""))),
            -float(item.get("confidence") or 0.0),
        )
    )
    matches: List[Dict[str, Any]] = []
    for segment in matched_segments:
        match = {
            "bbox": [int(segment["left"]), int(segment["top"]), int(segment["right"]), int(segment["bottom"])],
            "width": int(segment["width"]),
            "height": int(segment["height"]),
            "center": [int(segment["center"][0]), int(segment["center"][1])],
            "text": str(segment.get("text") or "").strip(),
            "confidence": float(segment.get("confidence") or 0.0),
        }
        if isinstance(search_bounds, list) and len(search_bounds) == 4:
            offset_left, offset_top, _, _ = [int(v) for v in search_bounds]
            match = _offset_match_to_global(match, left=offset_left, top=offset_top)
        matches.append(match)
    return {
        "providerId": "rpa_desktop_visual_locator",
        "status": "resolved",
        "locator": f"ocr:{query}",
        "matchCount": len(matches),
        "matches": matches,
        "readText": str(ocr_payload.get("text") or ""),
        "readTextError": None,
        "ocrRows": rows,
        "ocrPasses": ocr_passes,
        "searchImagePath": str(image_path),
        "searchBounds": list(search_bounds or []),
        "searchMode": "captured_window_region_ocr" if local_bbox is not None else "captured_window_ocr",
    }


def _rows_are_close(left: Dict[str, Any], right: Dict[str, Any], *, gap_x: int = 80, gap_y: int = 56) -> bool:
    left_left = int(left.get("left") or 0)
    left_top = int(left.get("top") or 0)
    left_right = int(left.get("right") or left_left)
    left_bottom = int(left.get("bottom") or left_top)
    right_left = int(right.get("left") or 0)
    right_top = int(right.get("top") or 0)
    right_right = int(right.get("right") or right_left)
    right_bottom = int(right.get("bottom") or right_top)
    horizontal_overlap = min(left_right, right_right) - max(left_left, right_left)
    vertical_overlap = min(left_bottom, right_bottom) - max(left_top, right_top)
    horizontal_distance = max(0, max(left_left, right_left) - min(left_right, right_right))
    vertical_distance = max(0, max(left_top, right_top) - min(left_bottom, right_bottom))
    return (
        vertical_overlap >= -gap_y and horizontal_distance <= gap_x
    ) or (
        horizontal_overlap >= -gap_x and vertical_distance <= gap_y
    )


def _cluster_ocr_rows(rows: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    ordered = sorted(
        [dict(row) for row in rows if isinstance(row, dict)],
        key=lambda item: (int(item.get("top") or 0), int(item.get("left") or 0)),
    )
    if not ordered:
        return []
    remaining = list(range(len(ordered)))
    clusters: List[List[Dict[str, Any]]] = []
    while remaining:
        seed_index = remaining.pop(0)
        queue = [seed_index]
        current_cluster: List[Dict[str, Any]] = []
        while queue:
            index = queue.pop(0)
            current = ordered[index]
            current_cluster.append(current)
            attached: List[int] = []
            for candidate_index in list(remaining):
                if _rows_are_close(current, ordered[candidate_index]):
                    attached.append(candidate_index)
                    remaining.remove(candidate_index)
            queue.extend(attached)
        clusters.append(current_cluster)
    return clusters


def _cluster_bounds(cluster: List[Dict[str, Any]]) -> List[int] | None:
    if not cluster:
        return None
    try:
        left = min(int(item.get("left") or 0) for item in cluster)
        top = min(int(item.get("top") or 0) for item in cluster)
        right = max(int(item.get("right") or 0) for item in cluster)
        bottom = max(int(item.get("bottom") or 0) for item in cluster)
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return [left, top, right, bottom]


def _derive_centered_ocr_search_bounds(*, rows: List[Dict[str, Any]], image_path: Path) -> List[int] | None:
    if not rows:
        return None
    try:
        with Image.open(image_path) as source:
            image_width, image_height = source.size
    except Exception:
        return None
    clusters = _cluster_ocr_rows(rows)
    if not clusters:
        return None
    image_center_x = image_width / 2.0
    image_center_y = image_height / 2.0
    best_score: tuple[float, float, float] | None = None
    best_bounds: List[int] | None = None
    for cluster in clusters:
        bounds = _cluster_bounds(cluster)
        if bounds is None:
            continue
        left, top, right, bottom = bounds
        width = right - left
        height = bottom - top
        if width < 120 or height < 80:
            continue
        if width > int(image_width * 0.9) or height > int(image_height * 0.9):
            continue
        center_x = left + width / 2.0
        center_y = top + height / 2.0
        center_distance = abs(center_x - image_center_x) + abs(center_y - image_center_y)
        cluster_area = width * height
        row_count = len(cluster)
        score = (
            center_distance,
            -float(row_count),
            -float(cluster_area),
        )
        if best_score is None or score < best_score:
            best_score = score
            padding_x = max(24, int(width * 0.08))
            padding_y = max(20, int(height * 0.12))
            best_bounds = [
                max(0, left - padding_x),
                max(0, top - padding_y),
                min(image_width, right + padding_x),
                min(image_height, bottom + padding_y),
            ]
    return best_bounds


class RPADesktopVisualLocatorRuntime:
    provider_id = "rpa_desktop_visual_locator"

    def _desktop_class(self):
        module = importlib.import_module("RPA.Desktop")
        desktop_cls = getattr(module, "Desktop", None)
        if desktop_cls is None:
            raise RuntimeError("RPA.Desktop 模块缺少 Desktop 类。")
        return desktop_cls

    def _recognition_templates_module(self):
        if importlib.util.find_spec("RPA.recognition") is None:
            return None
        return importlib.import_module("RPA.recognition.templates")

    def availability_summary(self) -> Dict[str, Any]:
        notes: List[str] = []
        status = "ready"
        runtime_available = False
        recognition_available = bool(importlib.util.find_spec("RPA.recognition"))
        tesseract_available = bool(_resolve_tesseract_executable())
        ocr_languages = _available_tesseract_languages() if tesseract_available else []
        try:
            self._desktop_class()
            runtime_available = True
        except Exception as exc:
            status = "missing_dependency"
            notes.append(f"RPA.Desktop 导入失败: {exc.__class__.__name__}: {exc}")
        supports_image_locator = runtime_available and recognition_available
        supports_ocr_locator = tesseract_available
        if runtime_available and not recognition_available:
            status = "partial_ready"
            notes.append("未检测到 RPA.recognition；image/ocr 定位不可用，但 point/region 仍可使用。")
        elif runtime_available and recognition_available and not tesseract_available:
            notes.append("未检测到系统 Tesseract；image locator 可用，但 OCR/read_text 暂不可用。")
        elif tesseract_available and "chi_sim" not in ocr_languages:
            notes.append("已检测到 Tesseract，但中文语言包缺失；中文 OCR 结果可能不稳定。")
        if runtime_available:
            notes.append("在线统一视觉定位层已接线，当前仅负责找位与读位，不直接执行点击输入。")
        return {
            "providerId": self.provider_id,
            "status": status,
            "runtimeAvailable": runtime_available,
            "recognitionAvailable": recognition_available,
            "tesseractAvailable": tesseract_available,
            "supportsImageLocator": supports_image_locator,
            "supportsOcrLocator": supports_ocr_locator,
            "supportsPointLocator": runtime_available,
            "supportsRegionLocator": runtime_available,
            "supportsReadText": supports_ocr_locator,
            "ocrLanguages": ocr_languages,
            "preferredOcrLanguage": _preferred_tesseract_lang() if tesseract_available else None,
            "mode": "online_locator_only",
            "notes": notes,
        }

    def is_available(self) -> bool:
        return bool(self.availability_summary().get("runtimeAvailable"))

    def observe_text(
        self,
        *,
        search_image_path: str,
        search_bounds: List[int] | None = None,
        psm: int | None = None,
    ) -> Dict[str, Any]:
        search_image_file = Path(str(search_image_path or "").strip()).expanduser()
        if not search_image_file.exists():
            raise FileNotFoundError(f"未找到 visual observer 搜索截图：{search_image_file}")
        normalized_search_bounds = _normalize_bounds(search_bounds)
        observation = _ocr_read_region(
            image_path=search_image_file,
            bbox=normalized_search_bounds if _bounds_fit_image(normalized_search_bounds, image_size=_image_size(search_image_file)) else None,
            psm=psm,
        )
        rows = list(observation.get("rows") or [])
        suggested_bounds = _derive_centered_ocr_search_bounds(
            rows=rows,
            image_path=search_image_file,
        )
        return {
            "providerId": self.provider_id,
            "status": "observed",
            "searchImagePath": str(search_image_file),
            "searchBounds": list(normalized_search_bounds or []),
            "searchMode": "captured_window_region_ocr" if normalized_search_bounds is not None else "captured_window_ocr",
            "text": str(observation.get("text") or ""),
            "rows": rows,
            "rowCount": len(rows),
            "suggestedDialogBounds": list(suggested_bounds or []) if isinstance(suggested_bounds, list) else None,
            "languages": observation.get("languages"),
            "tessdataDir": observation.get("tessdataDir"),
        }

    def locate(
        self,
        *,
        locator: str,
        timeout_ms: int = 2500,
        confidence: float | None = None,
        multiple: bool = False,
        read_text: bool = False,
        search_image_path: str | None = None,
        search_bounds: List[int] | None = None,
    ) -> Dict[str, Any]:
        normalized_locator = _normalize_locator(locator)
        if not normalized_locator:
            raise ValueError("visual locator 不能为空。")
        ocr_query = _normalize_ocr_query(normalized_locator)
        if normalized_locator.startswith("image:"):
            image_path = Path(normalized_locator.split(":", 1)[1]).expanduser()
            if not image_path.exists():
                raise FileNotFoundError(f"未找到 visual locator 图片资产：{image_path}")
        else:
            image_path = None

        normalized_search_bounds = _normalize_bounds(search_bounds)
        normalized_search_image_path = str(search_image_path or "").strip() or None
        if normalized_search_image_path:
            search_image_file = Path(normalized_search_image_path).expanduser()
            if not search_image_file.exists():
                raise FileNotFoundError(f"未找到 visual locator 搜索截图：{search_image_file}")
        else:
            search_image_file = None

        normalized_timeout_s = max(0.2, float(timeout_ms) / 1000.0)
        normalized_confidence = _normalize_confidence(confidence)
        if ocr_query and search_image_file is not None:
            started = time.perf_counter()
            resolved = _locate_ocr_query_in_image(
                image_path=search_image_file,
                query=ocr_query,
                search_bounds=normalized_search_bounds,
            )
            if normalized_search_bounds is None and int(resolved.get("matchCount") or 0) > 1:
                centered_region_bounds = _derive_centered_ocr_search_bounds(
                    rows=list(resolved.get("ocrRows") or []),
                    image_path=search_image_file,
                )
                if centered_region_bounds is not None:
                    region_resolved = _locate_ocr_query_in_image(
                        image_path=search_image_file,
                        query=ocr_query,
                        search_bounds=centered_region_bounds,
                    )
                    if int(region_resolved.get("matchCount") or 0) > 0:
                        region_resolved["searchMode"] = "centered_region_ocr"
                        region_resolved["regionHint"] = {
                            "strategy": "centered_ocr_cluster",
                            "bounds": list(centered_region_bounds),
                        }
                        resolved = region_resolved
            resolved["latencyMs"] = int(round((time.perf_counter() - started) * 1000.0))
            resolved["usedTimeoutMs"] = int(round(normalized_timeout_s * 1000.0))
            resolved["usedConfidence"] = normalized_confidence
            return resolved
        if image_path is not None and search_image_file is not None:
            templates_module = self._recognition_templates_module()
            if templates_module is None:
                raise RuntimeError("当前环境缺少 RPA.recognition，无法在指定窗口截图内执行图片定位。")
            started = time.perf_counter()
            limit = None if multiple else 1
            raw_matches = list(
                templates_module.find(
                    image=search_image_file,
                    template=image_path,
                    confidence=normalized_confidence or getattr(templates_module, "DEFAULT_CONFIDENCE", 80.0),
                    limit=limit,
                )
            )
            latency_ms = int(round((time.perf_counter() - started) * 1000.0))
            matches = [_geometry_to_match(item) for item in raw_matches]
            if normalized_search_bounds is not None:
                left, top, _, _ = normalized_search_bounds
                matches = [_offset_match_to_global(item, left=left, top=top) for item in matches]
            read_text_payload = None
            read_text_error = None
            if read_text:
                try:
                    primary_bbox = list((matches[0] or {}).get("bbox") or [])
                    local_bbox = None
                    if len(primary_bbox) == 4 and normalized_search_bounds is not None:
                        offset_left, offset_top, _, _ = normalized_search_bounds
                        local_bbox = [
                            int(primary_bbox[0]) - int(offset_left),
                            int(primary_bbox[1]) - int(offset_top),
                            int(primary_bbox[2]) - int(offset_left),
                            int(primary_bbox[3]) - int(offset_top),
                        ]
                    elif len(primary_bbox) == 4:
                        local_bbox = [int(v) for v in primary_bbox]
                    read_text_payload = str(_ocr_read_region(image_path=search_image_file, bbox=local_bbox).get("text") or "")
                except Exception as exc:
                    read_text_error = f"{exc.__class__.__name__}: {exc}"
            return {
                "providerId": self.provider_id,
                "status": "resolved",
                "locator": normalized_locator,
                "matchCount": len(matches),
                "matches": matches,
                "latencyMs": latency_ms,
                "usedTimeoutMs": int(round(normalized_timeout_s * 1000.0)),
                "usedConfidence": normalized_confidence,
                "readText": read_text_payload,
                "readTextError": read_text_error,
                "searchImagePath": str(search_image_file),
                "searchBounds": list(normalized_search_bounds or []),
                "searchMode": "captured_window_image",
            }

        desktop = self._desktop_class()()
        if hasattr(desktop, "set_default_timeout"):
            desktop.set_default_timeout(normalized_timeout_s)
        if normalized_confidence is not None and hasattr(desktop, "set_default_confidence"):
            desktop.set_default_confidence(normalized_confidence)

        started = time.perf_counter()
        if multiple and hasattr(desktop, "find_elements"):
            raw_matches = list(desktop.find_elements(normalized_locator) or [])
        elif hasattr(desktop, "wait_for_element"):
            raw_matches = [
                desktop.wait_for_element(
                    normalized_locator,
                    timeout=normalized_timeout_s,
                    interval=min(0.5, max(0.1, normalized_timeout_s / 5.0)),
                )
            ]
        elif hasattr(desktop, "find_element"):
            raw_matches = [desktop.find_element(normalized_locator)]
        else:
            raise RuntimeError("RPA.Desktop 当前实例缺少 find_element / wait_for_element 能力。")
        latency_ms = int(round((time.perf_counter() - started) * 1000.0))

        matches = [_geometry_to_match(item) for item in raw_matches]
        read_text_payload = None
        read_text_error = None
        if read_text and hasattr(desktop, "read_text"):
            if not _resolve_tesseract_executable():
                read_text_error = "tesseract_not_available"
            else:
                try:
                    read_text_payload = str(desktop.read_text(normalized_locator) or "")
                except Exception as exc:
                    read_text_error = f"{exc.__class__.__name__}: {exc}"

        return {
            "providerId": self.provider_id,
            "status": "resolved",
            "locator": normalized_locator,
            "matchCount": len(matches),
            "matches": matches,
            "latencyMs": latency_ms,
            "usedTimeoutMs": int(round(normalized_timeout_s * 1000.0)),
            "usedConfidence": normalized_confidence,
            "readText": read_text_payload,
            "readTextError": read_text_error,
            "searchImagePath": str(search_image_file) if search_image_file is not None else None,
            "searchBounds": list(normalized_search_bounds or []),
            "searchMode": "desktop_runtime",
        }
