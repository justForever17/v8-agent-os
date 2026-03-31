from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable
from runtimes.computer_use.visual_benchmark import (
    OfflineVisualParseResult,
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
