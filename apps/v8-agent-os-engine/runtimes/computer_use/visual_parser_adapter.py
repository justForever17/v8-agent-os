from __future__ import annotations

import importlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Protocol, Sequence, runtime_checkable

from PIL import Image

from core.system_base import get_desktop_tools_config
from runtimes.computer_use.visual_benchmark import (
    OfflineVisualParseResult,
    bbox_iou,
    normalize_bbox,
    parse_visual_result,
)


@dataclass(slots=True)
class DesktopVisualParserCapabilities:
    parser_id: str
    mode: str = "offline"
    supports_precomputed_predictions: bool = False
    supports_bbox_grounding: bool = False
    supports_page_identity_candidates: bool = False
    supports_blocker_candidates: bool = False
    supports_hit_zone_candidates: bool = False
    supports_affordance_regions: bool = False
    notes: list[str] = field(default_factory=list)
    installation_status: str = "unknown"
    installation_notes: list[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "parserId": self.parser_id,
            "mode": self.mode,
            "supportsPrecomputedPredictions": bool(self.supports_precomputed_predictions),
            "supportsBboxGrounding": bool(self.supports_bbox_grounding),
            "supportsPageIdentityCandidates": bool(self.supports_page_identity_candidates),
            "supportsBlockerCandidates": bool(self.supports_blocker_candidates),
            "supportsHitZoneCandidates": bool(self.supports_hit_zone_candidates),
            "supportsAffordanceRegions": bool(self.supports_affordance_regions),
            "notes": list(self.notes),
            "installationStatus": self.installation_status,
            "installationNotes": list(self.installation_notes),
        }


@runtime_checkable
class OfflineVisualParserAdapter(Protocol):
    parser_id: str

    def capability_summary(self) -> Dict[str, Any]:
        ...

    def is_available(self) -> bool:
        ...

    def parse_image(
        self,
        *,
        image_path: str | Path,
        context: Optional[Dict[str, Any]] = None,
    ) -> OfflineVisualParseResult:
        ...


class PrecomputedVisualParserAdapter:
    parser_id = "precomputed_visual_parser"

    def __init__(self, *, predictions_dir: str | Path, parser_id: str | None = None) -> None:
        self.predictions_dir = Path(predictions_dir)
        if parser_id:
            self.parser_id = str(parser_id).strip() or self.parser_id

    def capability_summary(self) -> Dict[str, Any]:
        return DesktopVisualParserCapabilities(
            parser_id=self.parser_id,
            mode="offline",
            supports_precomputed_predictions=True,
            supports_bbox_grounding=True,
            supports_page_identity_candidates=True,
            supports_blocker_candidates=True,
            supports_hit_zone_candidates=True,
            supports_affordance_regions=True,
            notes=["使用离线预计算 JSON 预测结果，不接入实时模型。"],
        ).as_dict()

    def is_available(self) -> bool:
        return self.predictions_dir.exists() and self.predictions_dir.is_dir()

    def parse_image(
        self,
        *,
        image_path: str | Path,
        context: Optional[Dict[str, Any]] = None,
    ) -> OfflineVisualParseResult:
        image_name = Path(image_path).name
        prediction_path = self.predictions_dir / f"{image_name}.json"
        if not prediction_path.exists():
            raise FileNotFoundError(f"未找到离线预测结果: {prediction_path}")
        payload = json.loads(prediction_path.read_text(encoding="utf-8"))
        parsed = parse_visual_result(payload, parser_id=self.parser_id)
        parsed.raw_payload.setdefault("context", dict(context or {}))
        return parsed


class NullVisualParserAdapter:
    parser_id = "null_visual_parser"

    def capability_summary(self) -> Dict[str, Any]:
        return DesktopVisualParserCapabilities(
            parser_id=self.parser_id,
            mode="offline",
            notes=["占位适配器，仅用于 benchmark 骨架自检。"],
        ).as_dict()

    def is_available(self) -> bool:
        return True

    def parse_image(
        self,
        *,
        image_path: str | Path,
        context: Optional[Dict[str, Any]] = None,
    ) -> OfflineVisualParseResult:
        return OfflineVisualParseResult(
            parser_id=self.parser_id,
            page_identity_candidates=[],
            blocker_candidates=[],
            affordance_regions=[],
            element_candidates=[],
            candidate_hit_zones=[],
            visual_confidence=0.0,
            latency_ms=0,
            source="offline_null",
            raw_payload={"imagePath": str(image_path), "context": dict(context or {})},
        )


class RPADesktopVisualLocatorAdapter:
    parser_id = "rpa_desktop_visual_locator"

    def __init__(self, *, predictions_dir: str | Path | None = None, parser_id: str | None = None) -> None:
        self.predictions_dir = Path(predictions_dir).expanduser() if predictions_dir else None
        if parser_id:
            self.parser_id = str(parser_id).strip() or self.parser_id

    def installation_status(self) -> Dict[str, Any]:
        notes: list[str] = []
        rpa_desktop_ready = False
        try:
            importlib.import_module("RPA.Desktop")
            rpa_desktop_ready = True
        except Exception as exc:
            notes.append(f"RPA.Desktop 导入失败: {exc.__class__.__name__}: {exc}")
        if self.predictions_dir and self.predictions_dir.exists() and self.predictions_dir.is_dir():
            if rpa_desktop_ready:
                status = "ready"
                notes.append("已检测到 RPA.Desktop 运行时与离线预测目录。")
            else:
                status = "offline_precomputed_only"
                notes.append("将以离线预计算预测结果模式运行 benchmark。")
        else:
            status = "runtime_only_unwired" if rpa_desktop_ready else "missing_dependency"
            notes.append("未配置可用的离线预测目录，当前只完成接口接线。")
        return {
            "status": status,
            "notes": notes,
            "predictionsDir": str(self.predictions_dir) if self.predictions_dir else "",
            "runtimeAvailable": rpa_desktop_ready,
        }

    def capability_summary(self) -> Dict[str, Any]:
        status = self.installation_status()
        return DesktopVisualParserCapabilities(
            parser_id=self.parser_id,
            mode="offline",
            supports_precomputed_predictions=True,
            supports_bbox_grounding=True,
            supports_page_identity_candidates=True,
            supports_blocker_candidates=True,
            supports_hit_zone_candidates=True,
            supports_affordance_regions=True,
            notes=[
                "作为统一视觉定位层的离线接线适配器，当前用于 benchmark 与预计算结果回放。",
                "后续在线主链将由 runtime 决定何时升级到 RPA.Desktop 模板/OCR/图像定位。",
            ],
            installation_status=status.get("status", "unknown"),
            installation_notes=list(status.get("notes") or []),
        ).as_dict()

    def is_available(self) -> bool:
        return bool(self.predictions_dir and self.predictions_dir.exists() and self.predictions_dir.is_dir())

    def parse_image(
        self,
        *,
        image_path: str | Path,
        context: Optional[Dict[str, Any]] = None,
    ) -> OfflineVisualParseResult:
        if not self.predictions_dir:
            raise RuntimeError("未配置 RPA.Desktop 离线预测目录，当前无法执行 benchmark 解析。")
        image_name = Path(image_path).name
        prediction_path = self.predictions_dir / f"{image_name}.json"
        if not prediction_path.exists():
            raise FileNotFoundError(f"未找到 RPA.Desktop 离线预测结果: {prediction_path}")
        payload = json.loads(prediction_path.read_text(encoding="utf-8"))
        parsed = parse_visual_result(payload, parser_id=self.parser_id)
        parsed.raw_payload.setdefault("context", dict(context or {}))
        parsed.raw_payload.setdefault("sourceAdapter", self.parser_id)
        return parsed


def _string(value: Any) -> str:
    return str(value or "").strip()


def _lower_tokens(values: Iterable[str]) -> list[str]:
    return [_string(item).lower() for item in values if _string(item)]


def _collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_string(value)] if _string(value) else []
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_collect_strings(item))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        result: list[str] = []
        for item in value:
            result.extend(_collect_strings(item))
        return result
    return []


def _normalize_bbox_with_image(value: Any, *, image_size: tuple[int, int] | None = None) -> list[float]:
    if isinstance(value, dict):
        for key in ("bbox", "box", "xyxy", "rect", "coords"):
            if key in value:
                return _normalize_bbox_with_image(value.get(key), image_size=image_size)
        if all(key in value for key in ("xmin", "ymin", "xmax", "ymax")):
            return _normalize_bbox_with_image(
                [value.get("xmin"), value.get("ymin"), value.get("xmax"), value.get("ymax")],
                image_size=image_size,
            )
        if all(key in value for key in ("x1", "y1", "x2", "y2")):
            return _normalize_bbox_with_image(
                [value.get("x1"), value.get("y1"), value.get("x2"), value.get("y2")],
                image_size=image_size,
            )
        return [0.0, 0.0, 0.0, 0.0]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)) or len(value) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    numbers = []
    for item in value:
        try:
            numbers.append(float(item))
        except Exception:
            numbers.append(0.0)
    if image_size and max(numbers or [0.0]) > 1.0:
        width = max(1.0, float(image_size[0]))
        height = max(1.0, float(image_size[1]))
        left, top, right, bottom = numbers
        return normalize_bbox([left / width, top / height, right / width, bottom / height])
    return normalize_bbox(numbers)


def _resolve_candidate_bbox(
    *,
    index: int,
    label_coordinates: Any,
    image_size: tuple[int, int] | None = None,
) -> list[float]:
    if isinstance(label_coordinates, dict):
        for key in (index, str(index), index + 1, str(index + 1)):
            if key in label_coordinates:
                return _normalize_bbox_with_image(label_coordinates.get(key), image_size=image_size)
    if isinstance(label_coordinates, Sequence) and not isinstance(label_coordinates, (bytes, bytearray, str)):
        if 0 <= index < len(label_coordinates):
            return _normalize_bbox_with_image(label_coordinates[index], image_size=image_size)
    return [0.0, 0.0, 0.0, 0.0]


def _match_hint_role(*, text_blob: str, bbox: list[float], hints: Iterable[dict[str, Any]]) -> str:
    blob = _string(text_blob).lower()
    best_role = ""
    best_score = -1.0
    for hint in list(hints or []):
        role = _string(hint.get("role"))
        if not role:
            continue
        score = 0.0
        keywords = _lower_tokens(hint.get("keywords") or [])
        if keywords and any(token and token in blob for token in keywords):
            score += 2.0
        hint_bbox = normalize_bbox(hint.get("bbox"))
        if any(hint_bbox):
            overlap = bbox_iou(bbox, hint_bbox)
            if overlap >= float(hint.get("minIou", 0.15)):
                score += overlap + 1.0
        if score > best_score and score > 0.0:
            best_score = score
            best_role = role
    return best_role


def _infer_generic_role(text_blob: str) -> str:
    blob = _string(text_blob).lower()
    if not blob:
        return "visual_element"
    if "搜索" in blob or "search" in blob:
        return "primary_input"
    if "发送" in blob or "send" in blob:
        return "confirm_action"
    if "播放" in blob or "play" in blob:
        return "primary_action"
    if "文件" in blob or "folder" in blob:
        return "content_receiver"
    return "visual_element"


def _match_named_hints(*, text_blob: str, hints: Iterable[dict[str, Any]], id_key: str) -> list[str]:
    blob = _string(text_blob).lower()
    matched: list[str] = []
    for hint in list(hints or []):
        hint_id = _string(hint.get(id_key))
        if not hint_id:
            continue
        keywords = _lower_tokens(hint.get("keywords") or [])
        if keywords and any(token and token in blob for token in keywords):
            matched.append(hint_id)
    return matched


class OmniParserVisualParserAdapter:
    parser_id = "omniparser_visual_parser"

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        som_model_path: str | Path | None = None,
        caption_model_name: str | None = None,
        caption_model_path: str | None = None,
        device: str | None = None,
        box_threshold: float = 0.05,
        iou_threshold: float = 0.1,
        icon_process_batch_size: int = 128,
        parser_id: str | None = None,
    ) -> None:
        desktop_tools = get_desktop_tools_config()
        env_repo_root = desktop_tools.get("omniParserRoot")
        env_som_model = desktop_tools.get("omniParserSomModelPath")
        env_caption_model_path = desktop_tools.get("omniParserCaptionModelPath")
        env_caption_model_name = desktop_tools.get("omniParserCaptionModelName")
        env_device = desktop_tools.get("omniParserDevice")
        self.repo_root = Path(repo_root or env_repo_root or "").expanduser() if (repo_root or env_repo_root) else None
        self.som_model_path = Path(som_model_path or env_som_model or "").expanduser() if (som_model_path or env_som_model) else None
        self.caption_model_name = _string(caption_model_name or env_caption_model_name or "florence2") or "florence2"
        self.caption_model_path = _string(caption_model_path or env_caption_model_path)
        self.device = _string(device or env_device or "cuda") or "cuda"
        self.box_threshold = float(box_threshold)
        self.iou_threshold = float(iou_threshold)
        self.icon_process_batch_size = int(icon_process_batch_size)
        if parser_id:
            self.parser_id = str(parser_id).strip() or self.parser_id
        self._loaded_repo_root: str | None = None
        self._model_bundle: dict[str, Any] | None = None

    def capability_summary(self) -> Dict[str, Any]:
        status = self.installation_status()
        return DesktopVisualParserCapabilities(
            parser_id=self.parser_id,
            mode="offline",
            supports_bbox_grounding=True,
            supports_page_identity_candidates=True,
            supports_blocker_candidates=True,
            supports_hit_zone_candidates=True,
            supports_affordance_regions=True,
            notes=[
                "真实 OmniParser 适配器，使用官方仓库运行截图解析。",
                "当前仅用于离线 benchmark，不接入主执行链。",
            ],
            installation_status=status.get("status", "unknown"),
            installation_notes=list(status.get("notes") or []),
        ).as_dict()

    def installation_status(self) -> Dict[str, Any]:
        notes: list[str] = []
        status = "ready"
        if not self.repo_root:
            status = "missing_repo_root"
            notes.append("未配置 OmniParser 根目录。")
        elif not self.repo_root.exists():
            status = "missing_repo_root"
            notes.append(f"OmniParser 仓库目录不存在: {self.repo_root}")
        elif not (self.repo_root / "util" / "utils.py").exists():
            status = "missing_repo_files"
            notes.append("仓库目录中未找到 util/utils.py，疑似不是官方 OmniParser 根目录。")
        if not self.som_model_path:
            status = status if status != "ready" else "missing_som_model"
            notes.append("未配置 SOM 模型路径。")
        elif not self.som_model_path.exists():
            status = status if status != "ready" else "missing_som_model"
            notes.append(f"SOM 模型文件不存在: {self.som_model_path}")
        if not self.caption_model_path:
            notes.append("未配置 Caption 模型路径，将依赖外部默认模型解析路径。")
        else:
            caption_path = Path(self.caption_model_path).expanduser()
            if not caption_path.exists():
                status = status if status != "ready" else "missing_caption_model"
                notes.append(f"Caption 模型路径不存在: {caption_path}")
            elif caption_path.is_dir():
                required_caption_files = ["config.json", "generation_config.json", "model.safetensors"]
                missing_caption_files = [name for name in required_caption_files if not (caption_path / name).exists()]
                if missing_caption_files:
                    status = status if status != "ready" else "incomplete_caption_model"
                    notes.append(
                        "Caption 模型目录不完整，缺少文件: " + ", ".join(missing_caption_files)
                    )
        return {
            "status": status,
            "notes": notes,
            "repoRoot": str(self.repo_root) if self.repo_root else "",
            "somModelPath": str(self.som_model_path) if self.som_model_path else "",
            "captionModelName": self.caption_model_name,
            "captionModelPath": self.caption_model_path,
            "device": self.device,
        }

    def is_available(self) -> bool:
        return self.installation_status().get("status") == "ready"

    def _ensure_import_path(self) -> None:
        if not self.repo_root:
            raise RuntimeError("未配置 OmniParser 仓库根目录。")
        repo_root = str(self.repo_root.resolve())
        if self._loaded_repo_root == repo_root:
            return
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        self._loaded_repo_root = repo_root

    def _load_models(self) -> dict[str, Any]:
        if self._model_bundle is not None:
            return self._model_bundle
        status = self.installation_status()
        if status.get("status") != "ready":
            raise RuntimeError(f"OmniParser 当前不可用: {status}")
        self._ensure_import_path()
        utils = importlib.import_module("util.utils")
        get_yolo_model = getattr(utils, "get_yolo_model")
        get_caption_model_processor = getattr(utils, "get_caption_model_processor")
        som_model = get_yolo_model(model_path=str(self.som_model_path))
        caption_processor = get_caption_model_processor(
            model_name=self.caption_model_name,
            model_name_or_path=self.caption_model_path or self.caption_model_name,
            device=self.device,
        )
        self._model_bundle = {
            "utils": utils,
            "som_model": som_model,
            "caption_model_processor": caption_processor,
        }
        return self._model_bundle

    def _build_contextual_hints(self, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        data = dict(context or {})
        return {
            "roleHints": list(data.get("roleHints") or []),
            "pageIdentityHints": list(data.get("pageIdentityHints") or []),
            "blockerHints": list(data.get("blockerHints") or []),
        }

    def parse_image(
        self,
        *,
        image_path: str | Path,
        context: Optional[Dict[str, Any]] = None,
    ) -> OfflineVisualParseResult:
        bundle = self._load_models()
        utils = bundle["utils"]
        image_file = Path(image_path)
        if not image_file.exists():
            raise FileNotFoundError(f"未找到截图文件: {image_file}")
        start = time.perf_counter()
        image = Image.open(image_file)
        image_size = image.size
        image.close()
        ocr_bbox_result, _ = utils.check_ocr_box(
            str(image_file),
            display_img=False,
            output_bb_format="xyxy",
            easyocr_args={"paragraph": False, "text_threshold": 0.6},
            use_paddleocr=False,
        )
        ocr_text, ocr_bbox = ocr_bbox_result
        _, label_coordinates, parsed_content_list = utils.get_som_labeled_img(
            str(image_file),
            bundle["som_model"],
            BOX_TRESHOLD=self.box_threshold,
            output_coord_in_ratio=True,
            ocr_bbox=ocr_bbox,
            draw_bbox_config=None,
            caption_model_processor=bundle["caption_model_processor"],
            ocr_text=ocr_text,
            iou_threshold=self.iou_threshold,
            icon_process_batch_size=self.icon_process_batch_size,
        )
        hints = self._build_contextual_hints(context)
        text_blob = "\n".join(_collect_strings(parsed_content_list))
        page_identity_candidates = _match_named_hints(
            text_blob=text_blob,
            hints=hints.get("pageIdentityHints") or [],
            id_key="sceneId",
        )
        blocker_candidates = _match_named_hints(
            text_blob=text_blob,
            hints=hints.get("blockerHints") or [],
            id_key="blockerId",
        )
        element_candidates = []
        hit_zones = []
        affordance_regions = []
        for index, item in enumerate(list(parsed_content_list or [])):
            candidate_text = " ".join(_collect_strings(item))
            bbox = _resolve_candidate_bbox(index=index, label_coordinates=label_coordinates, image_size=image_size)
            role = _match_hint_role(text_blob=candidate_text, bbox=bbox, hints=hints.get("roleHints") or [])
            role = role or _infer_generic_role(candidate_text)
            metadata = {
                "rawIndex": index,
                "rawItem": item,
            }
            element_candidates.append(
                {
                    "role": role,
                    "label": candidate_text,
                    "text": candidate_text,
                    "bbox": bbox,
                    "confidence": 0.75,
                    "interactionHint": "click",
                    "metadata": metadata,
                }
            )
            hit_zones.append(
                {
                    "role": role,
                    "bbox": bbox,
                    "gesture": "click",
                    "confidence": 0.7,
                    "metadata": metadata,
                }
            )
            affordance_regions.append(
                {
                    "role": role,
                    "bbox": bbox,
                    "confidence": 0.7,
                    "source": "omniparser",
                }
            )
        latency_ms = int((time.perf_counter() - start) * 1000)
        return parse_visual_result(
            {
                "parserId": self.parser_id,
                "pageIdentityCandidates": page_identity_candidates,
                "blockerCandidates": blocker_candidates,
                "affordanceRegions": affordance_regions,
                "elementCandidates": element_candidates,
                "candidateHitZones": hit_zones,
                "visualConfidence": 0.8 if element_candidates else 0.0,
                "latencyMs": latency_ms,
                "source": "offline_omniparser",
                "rawPayload": {
                    "ocrText": ocr_text,
                    "ocrBbox": ocr_bbox,
                    "labelCoordinates": label_coordinates,
                    "parsedContentList": parsed_content_list,
                    "context": dict(context or {}),
                },
            },
            parser_id=self.parser_id,
        )
