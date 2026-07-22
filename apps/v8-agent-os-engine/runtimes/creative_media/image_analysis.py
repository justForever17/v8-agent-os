from __future__ import annotations

import hashlib
import math
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from core.runtime.feature_packs import load_feature_pack_asset_manifest, resolve_feature_pack_asset


ANALYZER_VERSION = "1.0.1"
FEATURE_PACK_ID = "creative_media_image_analysis"
MODEL_ASSET_ID = "isnet_general_use"
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".psd"}

QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "transparent_cutout": {
        "requireAlpha": True,
        "areaRatio": (0.03, 0.92),
        "maxTouchedEdges": 0,
        "maxComponents": 6,
        "minMaskConfidence": 0.52,
    },
    "character_reference": {
        "requireAlpha": False,
        "areaRatio": (0.18, 0.88),
        "maxTouchedEdges": 1,
        "maxComponents": 8,
        "minMaskConfidence": 0.48,
        "maxReferenceAreaDelta": 0.18,
        "maxReferenceCenterShift": 0.12,
    },
    "ui_icon": {
        "requireAlpha": True,
        "areaRatio": (0.10, 0.82),
        "maxTouchedEdges": 0,
        "maxComponents": 8,
        "minMaskConfidence": 0.58,
    },
    "product_packshot": {
        "requireAlpha": False,
        "areaRatio": (0.12, 0.82),
        "maxTouchedEdges": 0,
        "maxComponents": 5,
        "minMaskConfidence": 0.52,
        "maxReferenceAreaDelta": 0.15,
        "maxReferenceCenterShift": 0.10,
    },
    "storyboard_frame": {
        "requireAlpha": False,
        "areaRatio": (0.01, 0.99),
        "maxTouchedEdges": 4,
        "maxComponents": 64,
        "minMaskConfidence": 0.0,
    },
}


def _open_image(path: Path) -> Image.Image:
    if path.suffix.lower() == ".psd":
        try:
            from psd_tools import PSDImage  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"psd-tools is required to inspect PSD files: {exc}") from exc
        return PSDImage.open(str(path)).composite().convert("RGBA")
    return Image.open(path).convert("RGBA")


def _source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resize_mask(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    image = Image.fromarray(np.clip(mask * 255.0, 0, 255).astype(np.uint8), mode="L")
    return np.asarray(image.resize(size, Image.Resampling.LANCZOS), dtype=np.float32) / 255.0


def _edge_values(rgb: np.ndarray) -> np.ndarray:
    if rgb.shape[0] == 1 or rgb.shape[1] == 1:
        return rgb.reshape(-1, 3)
    return np.concatenate((rgb[0], rgb[-1], rgb[1:-1, 0], rgb[1:-1, -1]), axis=0)


def _connected_background(candidate: np.ndarray) -> np.ndarray:
    height, width = candidate.shape
    connected = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        if candidate[0, x]:
            connected[0, x] = True
            queue.append((0, x))
        if height > 1 and candidate[height - 1, x] and not connected[height - 1, x]:
            connected[height - 1, x] = True
            queue.append((height - 1, x))
    for y in range(1, height - 1):
        if candidate[y, 0]:
            connected[y, 0] = True
            queue.append((y, 0))
        if width > 1 and candidate[y, width - 1] and not connected[y, width - 1]:
            connected[y, width - 1] = True
            queue.append((y, width - 1))
    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < height and 0 <= nx < width and candidate[ny, nx] and not connected[ny, nx]:
                connected[ny, nx] = True
                queue.append((ny, nx))
    return connected


def _border_subject_mask(image: Image.Image) -> tuple[np.ndarray | None, float, dict[str, Any]]:
    rgb_image = image.convert("RGB")
    scale = min(1.0, 512.0 / max(rgb_image.size))
    if scale < 1.0:
        rgb_image = rgb_image.resize(
            (max(1, round(rgb_image.width * scale)), max(1, round(rgb_image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    rgb = np.asarray(rgb_image, dtype=np.float32)
    edges = _edge_values(rgb)
    median = np.median(edges, axis=0)
    distances = np.linalg.norm(edges - median, axis=1)
    edge_spread = float(np.percentile(distances, 90))
    threshold = max(16.0, min(52.0, edge_spread * 1.7 + 8.0))
    candidate = np.linalg.norm(rgb - median, axis=2) <= threshold
    background = _connected_background(candidate)
    subject = (~background).astype(np.float32)
    area_ratio = float(subject.mean())
    usable = edge_spread <= 34.0 and 0.01 <= area_ratio <= 0.97
    confidence = max(0.0, min(1.0, 1.0 - edge_spread / 68.0)) if usable else 0.0
    diagnostics = {
        "edgeSpread": round(edge_spread, 4),
        "backgroundColor": [int(round(value)) for value in median.tolist()],
        "backgroundThreshold": round(threshold, 4),
        "candidateAreaRatio": round(area_ratio, 6),
    }
    if not usable:
        return None, confidence, diagnostics
    return _resize_mask(subject, image.size), confidence, diagnostics


def _expected_model_digest() -> str:
    manifest = load_feature_pack_asset_manifest(FEATURE_PACK_ID) or {}
    for asset in list(manifest.get("assets") or []):
        if str(asset.get("id") or "") == MODEL_ASSET_ID:
            return str(asset.get("sha256") or "").lower()
    return ""


@lru_cache(maxsize=2)
def _load_onnx_session(model_path: str, modified_ns: int, size: int):
    expected = _expected_model_digest()
    actual = _source_fingerprint(Path(model_path))
    if not expected or actual.lower() != expected:
        raise RuntimeError("creative media image analysis model failed SHA-256 verification")
    try:
        import onnxruntime as ort  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"onnxruntime is unavailable: {exc}") from exc
    return ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])


def _onnx_subject_mask(image: Image.Image) -> tuple[np.ndarray | None, float, str | None]:
    model_path = resolve_feature_pack_asset(FEATURE_PACK_ID, MODEL_ASSET_ID)
    if model_path is None:
        return None, 0.0, "feature_pack_not_installed"
    try:
        stat = model_path.stat()
        session = _load_onnx_session(str(model_path), stat.st_mtime_ns, stat.st_size)
        rgb = image.convert("RGB").resize((1024, 1024), Image.Resampling.LANCZOS)
        values = np.asarray(rgb, dtype=np.float32)
        values = values / max(float(values.max()), 1e-6)
        values = (values - np.asarray((0.5, 0.5, 0.5), dtype=np.float32)).transpose((2, 0, 1))
        input_name = session.get_inputs()[0].name
        prediction = session.run(None, {input_name: np.expand_dims(values, 0).astype(np.float32)})[0]
        mask = np.asarray(prediction, dtype=np.float32).squeeze()
        minimum = float(mask.min())
        maximum = float(mask.max())
        if maximum - minimum <= 1e-8:
            return None, 0.0, "model_returned_constant_mask"
        mask = (mask - minimum) / (maximum - minimum)
        confidence = float(np.mean(np.abs(mask - 0.5)) * 2.0)
        return _resize_mask(mask, image.size), max(0.0, min(1.0, confidence)), None
    except Exception as exc:
        return None, 0.0, str(exc)


def _component_count(mask: np.ndarray) -> int:
    binary_image = Image.fromarray((mask >= 0.5).astype(np.uint8) * 255, mode="L")
    scale = min(1.0, 320.0 / max(binary_image.size))
    if scale < 1.0:
        binary_image = binary_image.resize(
            (max(1, round(binary_image.width * scale)), max(1, round(binary_image.height * scale))),
            Image.Resampling.NEAREST,
        )
    binary = np.asarray(binary_image, dtype=np.uint8) > 0
    height, width = binary.shape
    visited = np.zeros_like(binary)
    minimum_component = max(2, int(binary.size * 0.0002))
    count = 0
    for y in range(height):
        for x in range(width):
            if not binary[y, x] or visited[y, x]:
                continue
            visited[y, x] = True
            queue = deque([(y, x)])
            size = 0
            while queue:
                cy, cx = queue.popleft()
                size += 1
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and binary[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((ny, nx))
            if size >= minimum_component:
                count += 1
    return count


def _mask_metrics(mask: np.ndarray) -> dict[str, Any]:
    binary = mask >= 0.5
    height, width = binary.shape
    coordinates = np.argwhere(binary)
    if not coordinates.size:
        return {
            "areaRatio": 0.0,
            "bbox": None,
            "centroid": None,
            "margins": None,
            "touchesEdges": [],
            "componentCount": 0,
        }
    y0, x0 = coordinates.min(axis=0)
    y1, x1 = coordinates.max(axis=0)
    weights = np.clip(mask, 0.0, 1.0)
    total = float(weights.sum())
    x_weights = weights.sum(axis=0, dtype=np.float64)
    y_weights = weights.sum(axis=1, dtype=np.float64)
    centroid_x = float(np.dot(x_weights, np.arange(width, dtype=np.float64)) / max(total, 1e-8)) / max(width - 1, 1)
    centroid_y = float(np.dot(y_weights, np.arange(height, dtype=np.float64)) / max(total, 1e-8)) / max(height - 1, 1)
    touches = []
    if y0 == 0:
        touches.append("top")
    if y1 == height - 1:
        touches.append("bottom")
    if x0 == 0:
        touches.append("left")
    if x1 == width - 1:
        touches.append("right")
    return {
        "areaRatio": round(float(binary.mean()), 6),
        "bbox": {
            "x": round(float(x0) / max(width, 1), 6),
            "y": round(float(y0) / max(height, 1), 6),
            "width": round(float(x1 - x0 + 1) / max(width, 1), 6),
            "height": round(float(y1 - y0 + 1) / max(height, 1), 6),
        },
        "centroid": {"x": round(centroid_x, 6), "y": round(centroid_y, 6)},
        "margins": {
            "top": round(float(y0) / max(height, 1), 6),
            "right": round(float(width - x1 - 1) / max(width, 1), 6),
            "bottom": round(float(height - y1 - 1) / max(height, 1), 6),
            "left": round(float(x0) / max(width, 1), 6),
        },
        "touchesEdges": touches,
        "componentCount": _component_count(mask),
    }


def _analyze(path: str | Path, *, allow_onnx: bool = True) -> tuple[dict[str, Any], np.ndarray | None]:
    source = Path(path).expanduser().resolve(strict=False)
    if source.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError(f"unsupported image format: {source.suffix or 'unknown'}")
    if not source.is_file():
        raise FileNotFoundError(str(source))
    image = _open_image(source)
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    total = max(1, alpha.size)
    transparent = int(np.count_nonzero(alpha == 0))
    translucent = int(np.count_nonzero((alpha > 0) & (alpha < 255)))
    alpha_fraction = (transparent + translucent) / total
    meaningful_alpha = transparent + translucent >= max(16, int(total * 0.0005))
    false_alpha = (transparent + translucent) > 0 and not meaningful_alpha
    diagnostics: dict[str, Any] = {}
    required_feature_pack = None
    model_error = None
    if meaningful_alpha:
        mask = alpha.astype(np.float32) / 255.0
        mask_source = "alpha"
        confidence = 1.0
    else:
        mask, confidence, border_diagnostics = _border_subject_mask(image)
        diagnostics["border"] = border_diagnostics
        mask_source = "border_connected" if mask is not None else "none"
        if mask is None and allow_onnx:
            mask, confidence, model_error = _onnx_subject_mask(image)
            if mask is not None:
                mask_source = "onnx_isnet"
            elif model_error == "feature_pack_not_installed":
                required_feature_pack = FEATURE_PACK_ID
    metrics = _mask_metrics(mask) if mask is not None else _mask_metrics(np.zeros((image.height, image.width), dtype=np.float32))
    edge_alpha = alpha[(alpha > 0) & (alpha < 255)]
    contamination_ratio = float(edge_alpha.size) / max(1, int(np.count_nonzero(alpha > 0)))
    status = "analyzed" if mask is not None else "review_required"
    report = {
        "version": 1,
        "analyzerVersion": ANALYZER_VERSION,
        "status": status,
        "sourcePath": str(source),
        "sourceFingerprint": _source_fingerprint(source),
        "format": source.suffix.lower().lstrip("."),
        "width": image.width,
        "height": image.height,
        "alpha": {
            "status": "true_alpha" if meaningful_alpha else "false_alpha" if false_alpha else "opaque",
            "transparentPixels": transparent,
            "translucentPixels": translucent,
            "coverageRatio": round(alpha_fraction, 6),
            "edgeContaminationRatio": round(contamination_ratio, 6),
        },
        "subject": {
            "maskSource": mask_source,
            "maskConfidence": round(float(confidence), 6),
            **metrics,
        },
        "requiredFeaturePackId": required_feature_pack,
        "modelError": model_error if model_error not in {None, "feature_pack_not_installed"} else None,
        "diagnostics": diagnostics,
    }
    return report, mask


def analyze_image(path: str | Path, *, allow_onnx: bool = True) -> dict[str, Any]:
    report, _ = _analyze(path, allow_onnx=allow_onnx)
    return report


def _bbox_iou(left: dict[str, Any] | None, right: dict[str, Any] | None) -> float | None:
    if not left or not right:
        return None
    lx0, ly0 = float(left["x"]), float(left["y"])
    lx1, ly1 = lx0 + float(left["width"]), ly0 + float(left["height"])
    rx0, ry0 = float(right["x"]), float(right["y"])
    rx1, ry1 = rx0 + float(right["width"]), ry0 + float(right["height"])
    intersection = max(0.0, min(lx1, rx1) - max(lx0, rx0)) * max(0.0, min(ly1, ry1) - max(ly0, ry0))
    union = float(left["width"]) * float(left["height"]) + float(right["width"]) * float(right["height"]) - intersection
    return round(intersection / max(union, 1e-8), 6)


def compare_image_analyses(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    left = dict(reference.get("subject") or {})
    right = dict(candidate.get("subject") or {})
    left_center = dict(left.get("centroid") or {})
    right_center = dict(right.get("centroid") or {})
    center_shift = None
    if left_center and right_center:
        center_shift = math.dist(
            (float(left_center.get("x") or 0), float(left_center.get("y") or 0)),
            (float(right_center.get("x") or 0), float(right_center.get("y") or 0)),
        )
    margin_delta = {}
    for key in ("top", "right", "bottom", "left"):
        left_value = (left.get("margins") or {}).get(key)
        right_value = (right.get("margins") or {}).get(key)
        if left_value is not None and right_value is not None:
            margin_delta[key] = round(float(right_value) - float(left_value), 6)
    return {
        "version": 1,
        "analyzerVersion": ANALYZER_VERSION,
        "referenceFingerprint": reference.get("sourceFingerprint"),
        "candidateFingerprint": candidate.get("sourceFingerprint"),
        "referenceStatus": reference.get("status"),
        "candidateStatus": candidate.get("status"),
        "requiredFeaturePackId": reference.get("requiredFeaturePackId") or candidate.get("requiredFeaturePackId"),
        "areaRatioDelta": round(float(right.get("areaRatio") or 0) - float(left.get("areaRatio") or 0), 6),
        "bboxIoU": _bbox_iou(left.get("bbox"), right.get("bbox")),
        "centerShift": round(center_shift, 6) if center_shift is not None else None,
        "marginDelta": margin_delta,
        "clippingChange": {
            "before": list(left.get("touchesEdges") or []),
            "after": list(right.get("touchesEdges") or []),
        },
        "alphaCoverageDelta": round(
            float((candidate.get("alpha") or {}).get("coverageRatio") or 0)
            - float((reference.get("alpha") or {}).get("coverageRatio") or 0),
            6,
        ),
    }


def compare_images(reference_path: str | Path, candidate_path: str | Path, *, allow_onnx: bool = True) -> dict[str, Any]:
    reference = analyze_image(reference_path, allow_onnx=allow_onnx)
    candidate = analyze_image(candidate_path, allow_onnx=allow_onnx)
    return {
        "reference": reference,
        "candidate": candidate,
        "comparison": compare_image_analyses(reference, candidate),
    }


def evaluate_quality_profile(
    report: dict[str, Any],
    profile: str,
    *,
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile_id = profile if profile in QUALITY_PROFILES else "storyboard_frame"
    rules = QUALITY_PROFILES[profile_id]
    subject = dict(report.get("subject") or {})
    alpha = dict(report.get("alpha") or {})
    violations: list[str] = []
    warnings: list[str] = []
    if report.get("status") == "review_required":
        return {
            "status": "review_required",
            "profile": profile_id,
            "violations": ["subject_mask_unavailable"],
            "warnings": [],
            "requiredFeaturePackId": report.get("requiredFeaturePackId"),
        }
    if comparison and comparison.get("referenceStatus") == "review_required":
        return {
            "status": "review_required",
            "profile": profile_id,
            "violations": ["reference_subject_mask_unavailable"],
            "warnings": [],
            "requiredFeaturePackId": comparison.get("requiredFeaturePackId"),
        }
    if rules["requireAlpha"] and alpha.get("status") != "true_alpha":
        violations.append("real_alpha_required")
    area = float(subject.get("areaRatio") or 0)
    minimum_area, maximum_area = rules["areaRatio"]
    if area < minimum_area:
        violations.append("subject_too_small")
    if area > maximum_area:
        violations.append("subject_too_large")
    if len(list(subject.get("touchesEdges") or [])) > int(rules["maxTouchedEdges"]):
        violations.append("subject_clipped")
    if int(subject.get("componentCount") or 0) > int(rules["maxComponents"]):
        warnings.append("too_many_subject_components")
    if float(subject.get("maskConfidence") or 0) < float(rules["minMaskConfidence"]):
        warnings.append("low_mask_confidence")
    if comparison:
        if abs(float(comparison.get("areaRatioDelta") or 0)) > float(rules.get("maxReferenceAreaDelta", 1.0)):
            violations.append("reference_subject_scale_drift")
        if float(comparison.get("centerShift") or 0) > float(rules.get("maxReferenceCenterShift", 1.0)):
            violations.append("reference_subject_position_drift")
    repairable = violations and set(violations).issubset({"real_alpha_required"}) and subject.get("maskSource") != "none"
    return {
        "status": "repairable" if repairable else "failed" if violations else "review_required" if warnings else "passed",
        "profile": profile_id,
        "violations": violations,
        "warnings": warnings,
        "requiredFeaturePackId": report.get("requiredFeaturePackId"),
    }


def create_transparent_derivative(source_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve(strict=False)
    report, mask = _analyze(source, allow_onnx=True)
    if mask is None:
        raise RuntimeError("subject mask is unavailable; install the image analysis feature pack or review manually")
    image = _open_image(source).convert("RGBA")
    alpha = Image.fromarray(np.clip(mask * 255.0, 0, 255).astype(np.uint8), mode="L")
    image.putalpha(alpha)
    target = Path(output_path).expanduser().resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return {"path": str(target), "sourceReport": report, "report": analyze_image(target, allow_onnx=False)}


__all__ = [
    "ANALYZER_VERSION",
    "FEATURE_PACK_ID",
    "QUALITY_PROFILES",
    "SUPPORTED_IMAGE_SUFFIXES",
    "analyze_image",
    "compare_image_analyses",
    "compare_images",
    "create_transparent_derivative",
    "evaluate_quality_profile",
]
