from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
import time

from core.local_visual_support import is_local_provider, probe_local_multimodal_capability
from core.model_control_plane import model_control_plane
from core.tools.vision_media_analyzer import vision_media_analyzer
from erc.runtime_context import bind_runtime_context
from runtimes.computer_use.visual_locator_scope import (
    crop_capture_image_to_bounds,
    expand_scope_bounds,
    split_locator_candidates,
)
from runtimes.computer_use.visual_dialog_observer import observe_centered_dialog_scope
from runtimes.computer_use.visual_locator_ranking import (
    infer_visual_locator_chain_role,
    merge_visual_locator_candidate_resolutions,
    rank_visual_locator_resolution,
)
from runtimes.computer_use.visual_semantic_candidates import (
    build_semantic_visual_candidates,
    semantic_candidates_to_resolution,
)
from runtimes.computer_use.visual_observation_contract import (
    build_visual_judge_suggestion,
    summarize_visual_observation,
)
from runtimes.computer_use.visual_judge import run_visual_judge

def resolve_pure_visual_click_point(
    *,
    primary: Dict[str, Any],
    fallback_center: list[int],
    strategy: str,
) -> list[int]:
    normalized_strategy = str(strategy or "").strip().lower() or "center"
    if normalized_strategy not in {"center", "text_input"}:
        raise ValueError(f"不支持的 focus strategy: {strategy}")
    if normalized_strategy == "center":
        return [int(fallback_center[0]), int(fallback_center[1])]
    bbox = list(primary.get("bbox") or [])
    if len(bbox) != 4:
        return [int(fallback_center[0]), int(fallback_center[1])]
    left, top, right, bottom = [int(v) for v in bbox]
    width = max(1, right - left)
    height = max(1, bottom - top)
    return [
        int(left + max(20, round(width * 0.12))),
        int(top + max(20, round(height * 0.78))),
    ]


def _offset_resolution_to_global(
    resolved: Dict[str, Any],
    *,
    origin_left: int,
    origin_top: int,
) -> Dict[str, Any]:
    payload = dict(resolved or {})
    matches = [dict(item) for item in list(payload.get("matches") or []) if isinstance(item, dict)]
    if matches:
        shifted_matches: list[dict] = []
        for match in matches:
            shifted = dict(match)
            bbox = list(shifted.get("bbox") or [])
            center = list(shifted.get("center") or [])
            if len(bbox) == 4:
                shifted["bbox"] = [
                    int(bbox[0]) + int(origin_left),
                    int(bbox[1]) + int(origin_top),
                    int(bbox[2]) + int(origin_left),
                    int(bbox[3]) + int(origin_top),
                ]
            if len(center) == 2:
                shifted["center"] = [
                    int(center[0]) + int(origin_left),
                    int(center[1]) + int(origin_top),
                ]
            shifted_matches.append(shifted)
        payload["matches"] = shifted_matches
    semantic_ranking = dict(payload.get("semanticRanking") or {})
    ranked_candidates: list[dict] = []
    for item in list(semantic_ranking.get("rankedCandidates") or []):
        candidate = dict(item or {})
        bbox = list(candidate.get("bbox") or [])
        if len(bbox) == 4:
            candidate["bbox"] = [
                int(bbox[0]) + int(origin_left),
                int(bbox[1]) + int(origin_top),
                int(bbox[2]) + int(origin_left),
                int(bbox[3]) + int(origin_top),
            ]
        ranked_candidates.append(candidate)
    if ranked_candidates:
        semantic_ranking["rankedCandidates"] = ranked_candidates
        payload["semanticRanking"] = semantic_ranking
    return payload
@dataclass(slots=True)
class PureVisualCenterClickRuntime:
    driver: Any
    visual_locator_runtime: Any
    capture_screenshot_fn: Any

    def _resolve_visual_role_state(
        self,
        role: str,
        *,
        fallback_role: str | None = None,
    ) -> Dict[str, Any]:
        try:
            resolved = model_control_plane.resolve_model_for_role(role)
        except Exception as exc:
            return {"available": False, "reason": str(exc)}
        raw_model_id = str(resolved.get("rawModelId") or "").strip()
        source_role = role
        if not raw_model_id and fallback_role:
            try:
                resolved = model_control_plane.resolve_model_for_role(fallback_role)
                source_role = fallback_role
            except Exception as exc:
                return {"available": False, "reason": str(exc), "sourceRole": fallback_role}
        resolved_model_id = str(resolved.get("resolvedModelId") or "").strip()
        resolved_provider = dict(resolved.get("resolvedProvider") or {})
        capability_probe = None
        available = bool(resolved_model_id)
        reason = None
        if available and is_local_provider(resolved_provider):
            capability_probe = probe_local_multimodal_capability(
                model_id=resolved_model_id,
                provider_type=str(resolved_provider.get("type") or "LOCAL"),
                base_url=str(resolved_provider.get("base_url") or ""),
                api_key=str(resolved_provider.get("api_key") or ""),
            )
            if capability_probe.get("status") == "unsupported":
                available = False
                reason = str(capability_probe.get("message") or "当前本地视觉模型不可用。")
        return {
            "available": available,
            "reason": reason,
            "sourceRole": source_role,
            "modelId": resolved_model_id or None,
            "capabilityProbe": capability_probe,
        }

    def _vision_judge_available(self) -> bool:
        return bool(self._resolve_visual_role_state("computer_use_visual_judge", fallback_role="vision").get("available"))

    def _invoke_visual_judge(self, *, file_path: str, prompt: str) -> str:
        visual_judge_state = self._resolve_visual_role_state("computer_use_visual_judge", fallback_role="vision")
        role_name = str(visual_judge_state.get("sourceRole") or "vision").strip() or "vision"
        tool = vision_media_analyzer
        func = getattr(tool, "func", None)
        with bind_runtime_context(vision_role_override=role_name):
            if callable(func):
                result = func(file_path=str(file_path), prompt=prompt)
            elif hasattr(tool, "invoke"):
                result = tool.invoke({"file_path": str(file_path), "prompt": prompt})
            else:
                raise RuntimeError("视觉裁判工具不可调用。")
        return str(result)

    def _build_semantic_visual_resolution(
        self,
        *,
        locator: str,
        locator_role: str,
        scope_bounds: list[int] | None,
        capture_bounds: list[int] | None,
    ) -> Dict[str, Any] | None:
        candidates = build_semantic_visual_candidates(
            role=locator_role,
            scope_bounds=list(scope_bounds) if isinstance(scope_bounds, list) else None,
            capture_bounds=list(capture_bounds) if isinstance(capture_bounds, list) else None,
        )
        if not candidates:
            return None
        return semantic_candidates_to_resolution(
            locator=locator,
            role=locator_role,
            scope_bounds=list(scope_bounds) if isinstance(scope_bounds, list) else list(capture_bounds) if isinstance(capture_bounds, list) else None,
            candidates=candidates,
        )

    def click(
        self,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
        locator: str,
        confidence: float | None = None,
        timeout_ms: int = 2500,
        scope_locator: str | None = None,
        scope_padding: list[int] | None = None,
        scope_seed_strategy: str | None = None,
        capture_before: bool = True,
        capture_after: bool = True,
        prefer_sendinput_click: bool = True,
        double: bool = False,
    ) -> Dict[str, Any]:
        before_capture, resolved, primary, center = self._resolve_visual_center(
            window_title=window_title,
            window_handle=window_handle,
            locator=locator,
            confidence=confidence,
            timeout_ms=timeout_ms,
            scope_locator=scope_locator,
            scope_padding=scope_padding,
            scope_seed_strategy=scope_seed_strategy,
            capture_before=capture_before,
        )

        click_result = self.driver.click_point(
            point=[int(center[0]), int(center[1])],
            window_title=str(window_title or "").strip() or None,
            window_handle=int(window_handle) if window_handle not in (None, "") else None,
            double=bool(double),
            prefer_sendinput_click=bool(prefer_sendinput_click),
        )

        after_capture = None
        if capture_after:
            after_capture = self.capture_screenshot_fn(
                window_title=str(window_title or "").strip() or None,
                window_handle=int(window_handle) if window_handle not in (None, "") else None,
            )

        return {
            "status": "completed",
            "window": {
                "title": str(window_title or "").strip() or None,
                "handle": int(window_handle) if window_handle not in (None, "") else None,
            },
            "visualLocator": {
                "providerId": resolved.get("providerId"),
                "locator": resolved.get("locator"),
                "usedConfidence": resolved.get("usedConfidence"),
                "matchCount": resolved.get("matchCount"),
                "match": primary,
                "scopeLocator": resolved.get("scopeLocator"),
                "scopeBounds": resolved.get("scopeBounds"),
            },
            "clickedPoint": [int(center[0]), int(center[1])],
            "double": bool(double),
            "clickResult": click_result,
            "artifacts": {
                "before": self._compact_capture(before_capture),
                "after": self._compact_capture(after_capture),
            },
        }

    def click_and_type(
        self,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
        locator: str,
        text: str,
        confidence: float | None = None,
        timeout_ms: int = 2500,
        scope_locator: str | None = None,
        scope_padding: list[int] | None = None,
        scope_seed_strategy: str | None = None,
        capture_before: bool = True,
        capture_after: bool = True,
        prefer_sendinput_click: bool = True,
        clear_first: bool = True,
        press_enter: bool = False,
        settle_ms: int = 80,
        focus_strategy: str = "center",
    ) -> Dict[str, Any]:
        if not str(text or "").strip():
            raise ValueError("text 不能为空。")

        before_capture, resolved, primary, center = self._resolve_visual_center(
            window_title=window_title,
            window_handle=window_handle,
            locator=locator,
            confidence=confidence,
            timeout_ms=timeout_ms,
            scope_locator=scope_locator,
            scope_padding=scope_padding,
            scope_seed_strategy=scope_seed_strategy,
            capture_before=capture_before,
        )
        click_point = self._resolve_click_point(primary=primary, fallback_center=center, strategy=focus_strategy)

        click_result = self.driver.click_point(
            point=[int(click_point[0]), int(click_point[1])],
            window_title=str(window_title or "").strip() or None,
            window_handle=int(window_handle) if window_handle not in (None, "") else None,
            double=False,
            prefer_sendinput_click=bool(prefer_sendinput_click),
        )

        if int(settle_ms) > 0:
            time.sleep(max(0.0, int(settle_ms)) / 1000.0)

        type_result = self.driver.type_text_in_window(
            text=text,
            window_title=str(window_title or "").strip() or None,
            window_handle=int(window_handle) if window_handle not in (None, "") else None,
            point=[int(click_point[0]), int(click_point[1])],
            clear_first=bool(clear_first),
            press_enter=bool(press_enter),
            prefer_sendinput_click=bool(prefer_sendinput_click),
            prefer_sendinput_text=True,
        )

        after_capture = None
        if capture_after:
            after_capture = self.capture_screenshot_fn(
                window_title=str(window_title or "").strip() or None,
                window_handle=int(window_handle) if window_handle not in (None, "") else None,
            )

        return {
            "status": "completed",
            "window": {
                "title": str(window_title or "").strip() or None,
                "handle": int(window_handle) if window_handle not in (None, "") else None,
            },
            "visualLocator": {
                "providerId": resolved.get("providerId"),
                "locator": resolved.get("locator"),
                "usedConfidence": resolved.get("usedConfidence"),
                "matchCount": resolved.get("matchCount"),
                "match": primary,
                "focusStrategy": str(focus_strategy or "center"),
                "scopeLocator": resolved.get("scopeLocator"),
                "scopeBounds": resolved.get("scopeBounds"),
            },
            "clickedPoint": [int(click_point[0]), int(click_point[1])],
            "typedText": text,
            "clickResult": click_result,
            "typeResult": type_result,
            "artifacts": {
                "before": self._compact_capture(before_capture),
                "after": self._compact_capture(after_capture),
            },
        }

    def click_and_paste_files(
        self,
        *,
        window_title: str | None = None,
        window_handle: int | None = None,
        locator: str,
        file_paths: list[str],
        text: str | None = None,
        confidence: float | None = None,
        timeout_ms: int = 2500,
        scope_locator: str | None = None,
        scope_padding: list[int] | None = None,
        scope_seed_strategy: str | None = None,
        capture_before: bool = True,
        capture_after: bool = True,
        prefer_sendinput_click: bool = True,
        settle_ms: int = 120,
        focus_strategy: str = "text_input",
    ) -> Dict[str, Any]:
        normalized_paths = [str(item).strip() for item in list(file_paths or []) if str(item).strip()]
        if not normalized_paths:
            raise ValueError("file_paths 不能为空。")

        before_capture, resolved, primary, center = self._resolve_visual_center(
            window_title=window_title,
            window_handle=window_handle,
            locator=locator,
            confidence=confidence,
            timeout_ms=timeout_ms,
            scope_locator=scope_locator,
            scope_padding=scope_padding,
            scope_seed_strategy=scope_seed_strategy,
            capture_before=capture_before,
        )
        click_point = self._resolve_click_point(primary=primary, fallback_center=center, strategy=focus_strategy)

        click_result = self.driver.click_point(
            point=[int(click_point[0]), int(click_point[1])],
            window_title=str(window_title or "").strip() or None,
            window_handle=int(window_handle) if window_handle not in (None, "") else None,
            double=False,
            prefer_sendinput_click=bool(prefer_sendinput_click),
        )

        if int(settle_ms) > 0:
            time.sleep(max(0.0, int(settle_ms)) / 1000.0)

        paste_result = self.driver.type_text_in_window(
            text=str(text or ""),
            window_title=str(window_title or "").strip() or None,
            window_handle=int(window_handle) if window_handle not in (None, "") else None,
            point=[int(click_point[0]), int(click_point[1])],
            file_paths=normalized_paths,
            clear_first=False,
            press_enter=False,
            prefer_sendinput_click=bool(prefer_sendinput_click),
            prefer_sendinput_text=True,
        )

        after_capture = None
        if capture_after:
            after_capture = self.capture_screenshot_fn(
                window_title=str(window_title or "").strip() or None,
                window_handle=int(window_handle) if window_handle not in (None, "") else None,
            )

        return {
            "status": "completed",
            "window": {
                "title": str(window_title or "").strip() or None,
                "handle": int(window_handle) if window_handle not in (None, "") else None,
            },
            "visualLocator": {
                "providerId": resolved.get("providerId"),
                "locator": resolved.get("locator"),
                "usedConfidence": resolved.get("usedConfidence"),
                "matchCount": resolved.get("matchCount"),
                "match": primary,
                "focusStrategy": str(focus_strategy or "text_input"),
                "scopeLocator": resolved.get("scopeLocator"),
                "scopeBounds": resolved.get("scopeBounds"),
            },
            "clickedPoint": [int(click_point[0]), int(click_point[1])],
            "pastedFiles": normalized_paths,
            "clickResult": click_result,
            "pasteResult": paste_result,
            "artifacts": {
                "before": self._compact_capture(before_capture),
                "after": self._compact_capture(after_capture),
            },
        }

    def _resolve_visual_center(
        self,
        *,
        window_title: str | None,
        window_handle: int | None,
        locator: str,
        confidence: float | None,
        timeout_ms: int,
        scope_locator: str | None,
        scope_padding: list[int] | None,
        scope_seed_strategy: str | None,
        capture_before: bool,
    ) -> tuple[dict | None, dict, dict, list[int]]:
        if not str(locator or "").strip():
            raise ValueError("locator 不能为空。")

        if window_title or window_handle:
            self.driver.focus_window(
                window_title=str(window_title or "").strip() or None,
                window_handle=int(window_handle) if window_handle not in (None, "") else None,
            )

        before_capture = None
        if capture_before:
            before_capture = self.capture_screenshot_fn(
                window_title=str(window_title or "").strip() or None,
                window_handle=int(window_handle) if window_handle not in (None, "") else None,
            )

        search_image_path = self._capture_artifact_path(before_capture)
        capture_bounds = self._capture_bounds(before_capture)
        search_scope_bounds = list(capture_bounds) if isinstance(capture_bounds, list) else None
        search_origin = [
            int(capture_bounds[0]),
            int(capture_bounds[1]),
        ] if isinstance(capture_bounds, list) and len(capture_bounds) == 4 else [0, 0]
        locator_role = infer_visual_locator_chain_role(split_locator_candidates(locator))
        scope_resolution: Dict[str, Any] | None = None
        temporary_scope_image_path: str | None = None
        observer_resolution: Dict[str, Any] | None = None
        if search_image_path and str(scope_seed_strategy or "").strip().lower() == "centered_dialog":
            observer_resolution, observer_temp_paths = observe_centered_dialog_scope(
                visual_locator_runtime=self.visual_locator_runtime,
                capture_image_path=search_image_path,
                capture_bounds=capture_bounds,
            )
            observer_confident = str((observer_resolution or {}).get("dialogConfidenceLevel") or "").strip().lower() in {"medium", "high"}
            for observer_temp_path in list(observer_temp_paths or []):
                if observer_temp_path:
                    if temporary_scope_image_path:
                        Path(temporary_scope_image_path).unlink(missing_ok=True)
                    temporary_scope_image_path = observer_temp_path
            observed_dialog_bounds = list((observer_resolution or {}).get("dialogBounds") or [])
            observer_zone_bounds = self._observer_zone_bounds(
                observer_resolution,
                role=locator_role,
            )
            if observer_confident and len(observed_dialog_bounds) == 4:
                cropped_dialog_path, temp_dialog_path = crop_capture_image_to_bounds(
                    image_path=search_image_path,
                    capture_bounds=capture_bounds,
                    target_bounds=observed_dialog_bounds,
                )
                if cropped_dialog_path:
                    search_image_path = cropped_dialog_path
                    search_scope_bounds = list(observed_dialog_bounds)
                    search_origin = [int(observed_dialog_bounds[0]), int(observed_dialog_bounds[1])]
                    if temporary_scope_image_path and temporary_scope_image_path != temp_dialog_path:
                        Path(temporary_scope_image_path).unlink(missing_ok=True)
                    temporary_scope_image_path = temp_dialog_path
                zone_bounds = observer_zone_bounds
                if not str(scope_locator or "").strip() and isinstance(zone_bounds, list) and len(zone_bounds) == 4:
                    cropped_zone_path, temp_zone_path = crop_capture_image_to_bounds(
                        image_path=search_image_path,
                        capture_bounds=observed_dialog_bounds,
                        target_bounds=zone_bounds,
                    )
                    if cropped_zone_path:
                        search_image_path = cropped_zone_path
                        search_scope_bounds = list(zone_bounds)
                        search_origin = [int(zone_bounds[0]), int(zone_bounds[1])]
                        if temporary_scope_image_path and temporary_scope_image_path != temp_zone_path:
                            Path(temporary_scope_image_path).unlink(missing_ok=True)
                        temporary_scope_image_path = temp_zone_path
            elif isinstance((observer_resolution or {}).get("seedBounds"), list) and len((observer_resolution or {}).get("seedBounds") or []) == 4:
                centered_seed_bounds = list((observer_resolution or {}).get("seedBounds") or [])
                cropped_seed_path, temp_seed_path = crop_capture_image_to_bounds(
                    image_path=search_image_path,
                    capture_bounds=capture_bounds,
                    target_bounds=centered_seed_bounds,
                )
                if cropped_seed_path:
                    search_image_path = cropped_seed_path
                    search_scope_bounds = list(centered_seed_bounds)
                    search_origin = [int(centered_seed_bounds[0]), int(centered_seed_bounds[1])]
                    if temporary_scope_image_path and temporary_scope_image_path != temp_seed_path:
                        Path(temporary_scope_image_path).unlink(missing_ok=True)
                    temporary_scope_image_path = temp_seed_path
            else:
                observer_zone_bounds = observer_zone_bounds if isinstance(observer_zone_bounds, list) and len(observer_zone_bounds) == 4 else None
        else:
            observer_zone_bounds = None
        if str(scope_locator or "").strip() and search_image_path:
            scope_search_image_path = search_image_path
            scope_resolution = self._locate_with_candidates(
                locator_candidates=split_locator_candidates(scope_locator),
                locator_scope_bounds=list(search_scope_bounds) if isinstance(search_scope_bounds, list) else None,
                preferred_bounds=None,
                locator_role="generic",
                timeout_ms=int(timeout_ms),
                confidence=confidence,
                multiple=False,
                read_text=True,
                search_image_path=scope_search_image_path,
                search_origin=search_origin,
            )
            scope_matches = list(scope_resolution.get("matches") or [])
            if not scope_matches:
                raise RuntimeError(f"visual locator scope 未返回任何匹配结果：{scope_locator}")
            expanded_scope_bounds = expand_scope_bounds(
                match=dict(scope_matches[0] or {}),
                capture_bounds=capture_bounds,
                scope_padding=scope_padding,
            )
            cropped_scope_path, temp_scope_path = crop_capture_image_to_bounds(
                image_path=search_image_path,
                capture_bounds=capture_bounds,
                target_bounds=expanded_scope_bounds,
            )
            if temporary_scope_image_path and temporary_scope_image_path != temp_scope_path:
                Path(temporary_scope_image_path).unlink(missing_ok=True)
            temporary_scope_image_path = temp_scope_path
            if cropped_scope_path and expanded_scope_bounds:
                search_image_path = cropped_scope_path
                search_scope_bounds = list(expanded_scope_bounds)
                search_origin = [int(expanded_scope_bounds[0]), int(expanded_scope_bounds[1])]

        try:
            try:
                resolved = self._locate_with_candidates(
                    locator_candidates=split_locator_candidates(locator),
                    locator_scope_bounds=list(search_scope_bounds) if isinstance(search_scope_bounds, list) else None,
                    preferred_bounds=list(observer_zone_bounds) if isinstance(observer_zone_bounds, list) and len(observer_zone_bounds) == 4 else None,
                    locator_role=locator_role,
                    timeout_ms=int(timeout_ms),
                    confidence=confidence,
                    multiple=False,
                    read_text=False,
                    search_image_path=search_image_path,
                    search_origin=search_origin,
                )
                ranking = dict(resolved.get("semanticRanking") or {})
                if (
                    locator_role == "search_box"
                    and (
                        not list(resolved.get("matches") or [])
                        or not bool(ranking.get("selectedStrong"))
                    )
                ):
                    semantic_resolution = self._build_semantic_visual_resolution(
                        locator=locator,
                        locator_role=locator_role,
                        scope_bounds=list(search_scope_bounds) if isinstance(search_scope_bounds, list) else None,
                        capture_bounds=list(capture_bounds) if isinstance(capture_bounds, list) else None,
                    )
                    if isinstance(semantic_resolution, dict):
                        resolved = semantic_resolution
            except Exception as exc:
                if (
                    locator_role == "action_button"
                    and str((observer_resolution or {}).get("dialogConfidenceLevel") or "").strip().lower() in {"medium", "high"}
                    and isinstance(observer_zone_bounds, list)
                    and len(observer_zone_bounds) == 4
                ):
                    zone_left, zone_top, zone_right, zone_bottom = [int(item) for item in observer_zone_bounds]
                    zone_center = [
                        int(zone_left + max(1, zone_right - zone_left) // 2),
                        int(zone_top + max(1, zone_bottom - zone_top) // 2),
                    ]
                    resolved = {
                        "providerId": "centered_dialog_scope_fallback",
                        "status": "resolved",
                        "locator": locator,
                        "matchCount": 1,
                        "matches": [
                            {
                                "bbox": [zone_left, zone_top, zone_right, zone_bottom],
                                "center": zone_center,
                                "confidence": 0.0,
                            }
                        ],
                        "scopeBounds": list(observer_zone_bounds),
                        "fallbackReason": f"{exc.__class__.__name__}: {exc}",
                    }
                else:
                    raise
        finally:
            if temporary_scope_image_path:
                Path(temporary_scope_image_path).unlink(missing_ok=True)
        matches = list(resolved.get("matches") or [])
        if not matches:
            raise RuntimeError("visual locator 未返回任何匹配结果。")
        if isinstance(search_scope_bounds, list) and len(search_scope_bounds) == 4:
            resolved = dict(resolved)
            resolved["scopeBounds"] = list(search_scope_bounds)
        if isinstance(scope_resolution, dict):
            resolved = dict(resolved)
            resolved["scopeLocator"] = dict(scope_resolution)
        if isinstance(observer_resolution, dict):
            resolved = dict(resolved)
            resolved["visualObserver"] = dict(observer_resolution)
        resolved = dict(resolved)
        resolved["visualObservation"] = summarize_visual_observation(
            locator=locator,
            role=locator_role,
            observer_resolution=observer_resolution,
            locator_resolution=resolved,
        )
        judge_suggestion = build_visual_judge_suggestion(
            observation=resolved.get("visualObservation"),
            locator_resolution=resolved,
        )
        if judge_suggestion is not None:
            resolved["visualJudgeSuggestion"] = judge_suggestion
            resolved = run_visual_judge(
                resolution=resolved,
                current_search_image_path=search_image_path,
                capture_image_path=self._capture_artifact_path(before_capture),
                capture_bounds=capture_bounds,
                invoke=self._invoke_visual_judge,
                available=self._vision_judge_available(),
            )
            resolved["visualObservation"] = summarize_visual_observation(
                locator=locator,
                role=locator_role,
                observer_resolution=observer_resolution,
                locator_resolution=resolved,
            )
        matches = list(resolved.get("matches") or [])
        if not matches:
            raise RuntimeError("visual locator 未返回任何匹配结果。")
        primary = dict(matches[0])
        center = list(primary.get("center") or [])
        if len(center) != 2:
            raise RuntimeError("visual locator 返回结果缺少 center。")
        return before_capture, resolved, primary, center

    def _locate_with_candidates(
        self,
        *,
        locator_candidates: list[str],
        locator_scope_bounds: list[int] | None,
        preferred_bounds: list[int] | None,
        locator_role: str,
        timeout_ms: int,
        confidence: float | None,
        multiple: bool,
        read_text: bool,
        search_image_path: str | None,
        search_origin: list[int] | None,
    ) -> Dict[str, Any]:
        if not locator_candidates:
            raise ValueError("locator 不能为空。")
        errors: list[str] = []
        first_ambiguous: Dict[str, Any] | None = None
        successful_resolutions: list[Dict[str, Any]] = []
        resolved_origin = list(search_origin or [0, 0])
        origin_left = int(resolved_origin[0]) if len(resolved_origin) >= 2 else 0
        origin_top = int(resolved_origin[1]) if len(resolved_origin) >= 2 else 0
        for index, candidate in enumerate(locator_candidates):
            try:
                resolved = self.visual_locator_runtime.locate(
                    locator=candidate,
                    timeout_ms=int(timeout_ms),
                    confidence=confidence,
                    multiple=bool(multiple),
                    read_text=bool(read_text),
                    search_image_path=search_image_path,
                    search_bounds=None,
                )
                resolved = _offset_resolution_to_global(
                    resolved,
                    origin_left=origin_left,
                    origin_top=origin_top,
                )
                resolved = rank_visual_locator_resolution(
                    resolved,
                    locator=candidate,
                    scope_bounds=locator_scope_bounds,
                    role=locator_role,
                    preferred_bounds=preferred_bounds,
                )
                match_count = int(resolved.get("matchCount") or len(list(resolved.get("matches") or [])))
                ranking = dict(resolved.get("semanticRanking") or {})
                selected_strong = bool(ranking.get("selectedStrong"))
                if _is_ambiguous_ocr_candidate(candidate, match_count=match_count) and not selected_strong and index < len(locator_candidates) - 1:
                    if first_ambiguous is None:
                        first_ambiguous = dict(resolved or {})
                        first_ambiguous["ambiguousOcrCandidate"] = candidate
                        first_ambiguous["ambiguousOcrMatchCount"] = match_count
                    successful_resolutions.append(dict(resolved or {}))
                    continue
                if list(resolved.get("matches") or []):
                    enriched = dict(resolved or {})
                    if len(locator_candidates) > 1:
                        enriched["locatorChain"] = list(locator_candidates)
                        enriched["locatorCandidateIndex"] = index
                        enriched["locatorCandidate"] = candidate
                    successful_resolutions.append(enriched)
            except Exception as exc:
                errors.append(f"{candidate}: {exc.__class__.__name__}: {exc}")
        if successful_resolutions:
            return merge_visual_locator_candidate_resolutions(
                successful_resolutions,
                locator_candidates=locator_candidates,
                scope_bounds=locator_scope_bounds,
                role=locator_role,
                preferred_bounds=preferred_bounds,
            )
        if first_ambiguous is not None:
            if len(locator_candidates) > 1:
                first_ambiguous["locatorChain"] = list(locator_candidates)
            return first_ambiguous
        if errors:
            raise RuntimeError(" ; ".join(errors))
        return {
            "providerId": getattr(self.visual_locator_runtime, "provider_id", None),
            "locator": locator_candidates[0],
            "locatorChain": list(locator_candidates) if len(locator_candidates) > 1 else None,
            "matchCount": 0,
            "matches": [],
        }

    def _resolve_click_point(
        self,
        *,
        primary: Dict[str, Any],
        fallback_center: list[int],
        strategy: str,
    ) -> list[int]:
        return resolve_pure_visual_click_point(
            primary=primary,
            fallback_center=fallback_center,
            strategy=strategy,
        )

    def _observer_zone_bounds(
        self,
        observer_resolution: Dict[str, Any] | None,
        *,
        role: str,
    ) -> list[int] | None:
        payload = dict(observer_resolution or {})
        if role == "action_button":
            for key in ("primaryActionButtonBounds", "primaryActionZoneBounds", "actionZoneBounds"):
                bounds = list(payload.get(key) or [])
                if len(bounds) == 4:
                    return [int(item) for item in bounds]
            return None
        if role == "dialog_title":
            bounds = list(payload.get("titleZoneBounds") or [])
            if len(bounds) == 4:
                return [int(item) for item in bounds]
        return None

    def _capture_artifact_path(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        artifact = dict(payload.get("artifact") or {})
        if artifact:
            for key in ("filePath", "sourcePath", "path", "file_path", "source_path"):
                candidate = str(artifact.get(key) or "").strip()
                if candidate:
                    return candidate
        for key in ("artifactPath", "filePath", "path"):
            candidate = str(payload.get(key) or "").strip()
            if candidate:
                return candidate
        result = dict(payload.get("result") or {})
        nested_artifact = dict(result.get("artifact") or {})
        for key in ("filePath", "sourcePath", "path", "file_path", "source_path"):
            candidate = str(nested_artifact.get(key) or "").strip()
            if candidate:
                return candidate
        return None

    def _capture_bounds(self, payload: Any) -> list[int] | None:
        if not isinstance(payload, dict):
            return None
        result = dict(payload.get("result") or {})
        target = dict(result.get("target") or {})
        bounds = target.get("bounds")
        if isinstance(bounds, list) and len(bounds) == 4:
            try:
                return [int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3])]
            except Exception:
                return None
        return None

    def _compact_capture(self, payload: Any) -> Dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        result = dict(payload.get("result") or {})
        target = dict(result.get("target") or {})
        target_window = dict(target.get("window") or {})
        return {
            "artifactPath": self._capture_artifact_path(payload),
            "bounds": self._capture_bounds(payload),
            "windowTitle": str(target_window.get("title") or "").strip() or None,
            "windowHandle": target_window.get("handle"),
        }


def _is_ambiguous_ocr_candidate(locator: str, *, match_count: int) -> bool:
    token = str(locator or "").strip().lower()
    return token.startswith("ocr:") and int(match_count) > 1
