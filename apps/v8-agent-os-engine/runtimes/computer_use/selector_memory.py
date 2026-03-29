from __future__ import annotations

from typing import Any, Dict, List

from core.multimodal_payload_adapter import utc_now_iso
from core.storage import storage
from runtimes.computer_use.target_strategy import (
    is_result_selector_key,
    is_search_selector_key,
    merge_target_strategies,
    normalize_target_strategy,
)


class ComputerUseSelectorMemory:
    def __init__(self) -> None:
        self._max_per_app = 24
        self._max_windows_per_app = 12
        self._max_interactions_per_app = 32
        self._max_target_strategies_per_app = 32
        self._max_governance_events_per_app = 32

    def _normalize_text_key(self, value: Any) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _normalize_relative_point(self, point: Any) -> List[float] | None:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        try:
            x = round(float(point[0]), 4)
            y = round(float(point[1]), 4)
        except Exception:
            return None
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return None
        return [x, y]

    def _normalize_relative_rect(self, rect: Any) -> List[float] | None:
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            return None
        try:
            left = round(float(rect[0]), 4)
            top = round(float(rect[1]), 4)
            right = round(float(rect[2]), 4)
            bottom = round(float(rect[3]), 4)
        except Exception:
            return None
        if not (0.0 <= left <= 1.0 and 0.0 <= top <= 1.0 and 0.0 <= right <= 1.0 and 0.0 <= bottom <= 1.0):
            return None
        if right < left or bottom < top:
            return None
        return [left, top, right, bottom]

    def _normalize_relative_point_candidates(self, value: Any) -> List[List[float]]:
        if not isinstance(value, (list, tuple)):
            return []
        candidates: List[List[float]] = []
        for item in value:
            normalized = self._normalize_relative_point(item)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    def _normalize_spatial_anchor(self, anchor: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(anchor, dict):
            return {}
        normalized: Dict[str, Any] = {}
        for key in (
            "windowRelativePoint",
            "screenRelativePoint",
            "windowRelativeRect",
            "displayBounds",
            "windowBounds",
            "displayId",
            "dpiScale",
        ):
            value = anchor.get(key)
            if key.endswith("Point"):
                normalized_point = self._normalize_relative_point(value)
                if normalized_point:
                    normalized[key] = normalized_point
            elif key.endswith("Rect"):
                normalized_rect = self._normalize_relative_rect(value)
                if normalized_rect:
                    normalized[key] = normalized_rect
            elif key.endswith("Bounds") and isinstance(value, (list, tuple)) and len(value) == 4:
                try:
                    normalized[key] = [int(value[0]), int(value[1]), int(value[2]), int(value[3])]
                except Exception:
                    continue
            elif key == "dpiScale" and value not in (None, ""):
                try:
                    normalized[key] = round(float(value), 4)
                except Exception:
                    continue
            elif isinstance(value, str) and value.strip():
                normalized[key] = value.strip()
        return normalized

    def _merge_relative_points(self, *groups: Any) -> List[List[float]]:
        merged: List[List[float]] = []
        for group in groups:
            for point in self._normalize_relative_point_candidates(group):
                if point not in merged:
                    merged.append(point)
        return merged

    def _normalize_selector(self, selector: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(selector, dict):
            return {}
        normalized: Dict[str, Any] = {}
        for source_key, target_key in (
            ("name", "name"),
            ("automation_id", "automation_id"),
            ("automationId", "automation_id"),
            ("control_type", "control_type"),
            ("controlType", "control_type"),
            ("class_name", "class_name"),
            ("className", "class_name"),
            ("handle", "handle"),
        ):
            value = selector.get(source_key)
            if isinstance(value, str) and value.strip():
                normalized[target_key] = value.strip()
            elif target_key == "handle" and value not in (None, ""):
                try:
                    normalized[target_key] = int(value)
                except Exception:
                    continue
        return normalized

    def _normalize_governance_event(self, event: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(event, dict):
            return {}
        normalized: Dict[str, Any] = {}
        for source_key, target_key in (
            ("templateId", "templateId"),
            ("template_id", "templateId"),
            ("draftId", "draftId"),
            ("draft_id", "draftId"),
            ("status", "status"),
            ("stage", "stage"),
            ("rolloutMode", "rolloutMode"),
            ("executionPath", "executionPath"),
            ("recommendedDecision", "recommendedDecision"),
            ("executionState", "executionState"),
            ("outcomeFamily", "outcomeFamily"),
            ("decisionScope", "decisionScope"),
            ("decisionReasonGroup", "decisionReasonGroup"),
            ("eventType", "eventType"),
        ):
            value = str(event.get(source_key) or "").strip()
            if value:
                normalized[target_key] = value
        confidence = event.get("confidence")
        if confidence not in (None, ""):
            try:
                normalized["confidence"] = round(float(confidence), 3)
            except Exception:
                pass
        reason = str(event.get("reason") or "").strip()
        if reason:
            normalized["reason"] = reason
        updated_at = str(event.get("updatedAt") or event.get("at") or "").strip()
        if updated_at:
            normalized["updatedAt"] = updated_at
        target_strategy_keys = [str(item).strip() for item in list(event.get("targetStrategyKeys") or []) if str(item).strip()]
        if target_strategy_keys:
            normalized["targetStrategyKeys"] = target_strategy_keys
        attachment_capabilities = [str(item).strip() for item in list(event.get("attachmentCapabilities") or []) if str(item).strip()]
        if attachment_capabilities:
            normalized["attachmentCapabilities"] = attachment_capabilities
        decision_signals = dict(event.get("decisionSignals") or {})
        if decision_signals:
            normalized["decisionSignals"] = decision_signals
        return normalized

    def _normalize_interaction_match(
        self,
        *,
        action_name: str | None = None,
        selector_key: str | None = None,
        target_text: str | None = None,
        control_type: str | None = None,
        window_class: str | None = None,
        window_title: str | None = None,
    ) -> Dict[str, Any]:
        match: Dict[str, Any] = {}
        if isinstance(action_name, str) and action_name.strip():
            match["actionName"] = action_name.strip().lower()
        if isinstance(selector_key, str) and selector_key.strip():
            match["selectorKey"] = selector_key.strip()
        normalized_target_text = self._normalize_text_key(target_text)
        if normalized_target_text:
            match["targetText"] = normalized_target_text
            match["targetTextRaw"] = str(target_text or "").strip()
        if isinstance(control_type, str) and control_type.strip():
            match["controlType"] = control_type.strip().lower()
        if isinstance(window_class, str) and window_class.strip():
            match["windowClass"] = window_class.strip().lower()
        if isinstance(window_title, str) and window_title.strip():
            match["windowTitle"] = window_title.strip().lower()
        return match

    def _normalize_interaction_patch(self, patch: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(patch, dict):
            return {}
        normalized: Dict[str, Any] = {}
        point = self._normalize_relative_point(patch.get("point"))
        if point:
            normalized["point"] = point
        point_rect = self._normalize_relative_rect(patch.get("point_rect") or patch.get("pointRect"))
        if point_rect:
            normalized["point_rect"] = point_rect
        point_candidates = self._merge_relative_points(
            patch.get("point_candidates"),
            patch.get("pointCandidates"),
        )
        if point_candidates:
            normalized["point_candidates"] = point_candidates
        spatial_anchor = self._normalize_spatial_anchor(
            patch.get("spatial_anchor") or patch.get("spatialAnchor")
        )
        if spatial_anchor:
            normalized["spatial_anchor"] = spatial_anchor
        coordinate_source = str(patch.get("coordinate_source") or patch.get("coordinateSource") or "").strip()
        if coordinate_source:
            normalized["coordinate_source"] = coordinate_source
        for key in ("prefer_sendinput_click", "window_typing", "clear_first", "abort_on_major_deviation"):
            if key in patch:
                normalized[key] = bool(patch.get(key))
        for source_key, target_key in (
            ("post_action_settle_timeout_ms", "post_action_settle_timeout_ms"),
            ("postActionSettleTimeoutMs", "post_action_settle_timeout_ms"),
            ("post_action_settle_poll_ms", "post_action_settle_poll_ms"),
            ("postActionSettlePollMs", "post_action_settle_poll_ms"),
            ("post_action_stable_rounds", "post_action_stable_rounds"),
            ("postActionStableRounds", "post_action_stable_rounds"),
        ):
            value = patch.get(source_key)
            if value in (None, ""):
                continue
            try:
                normalized[target_key] = int(value)
            except Exception:
                continue
        return normalized

    def _merge_interaction_patch(self, base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base or {})
        for key in (
            "point",
            "point_rect",
            "spatial_anchor",
            "coordinate_source",
            "window_typing",
            "clear_first",
            "prefer_sendinput_click",
            "post_action_settle_timeout_ms",
            "post_action_settle_poll_ms",
            "post_action_stable_rounds",
            "abort_on_major_deviation",
        ):
            if merged.get(key) in (None, "", [], {}) and patch.get(key) not in (None, "", [], {}):
                merged[key] = patch.get(key)
        point_candidates = self._merge_relative_points(
            merged.get("point_candidates"),
            patch.get("point_candidates"),
        )
        if point_candidates:
            merged["point_candidates"] = point_candidates
        return merged

    def _load(self) -> Dict[str, Any]:
        return storage.get_computer_use_memory()

    def _save(self, payload: Dict[str, Any]) -> None:
        storage.save_computer_use_memory(payload)

    def remember(
        self,
        *,
        app_id: str,
        selector: Dict[str, Any] | None,
        source: str,
        reason: str | None = None,
        weight: int = 24,
        window_class: str | None = None,
        window_title: str | None = None,
        action_name: str | None = None,
    ) -> None:
        normalized_selector = self._normalize_selector(selector)
        if not app_id or not normalized_selector:
            return
        payload = self._load()
        apps = payload.setdefault("apps", {})
        app_bucket = apps.setdefault(app_id, {"selectors": []})
        selector_items = list(app_bucket.get("selectors") or [])
        selector_items = [
            item
            for item in selector_items
            if not (
                isinstance(item, dict)
                and dict(item.get("selector") or {}) == normalized_selector
                and str(item.get("source") or "") == str(source or "")
            )
        ]
        selector_items.insert(
            0,
            {
                "selector": normalized_selector,
                "source": str(source or "runtime_hint").strip() or "runtime_hint",
                "reason": str(reason).strip() if reason else None,
                "weight": max(8, min(int(weight), 96)),
                "windowClass": str(window_class or "").strip() or None,
                "windowTitle": str(window_title or "").strip() or None,
                "actionName": str(action_name or "").strip() or None,
                "updatedAt": utc_now_iso(),
            },
        )
        app_bucket["selectors"] = selector_items[: self._max_per_app]
        self._save(payload)

    def remember_window(
        self,
        *,
        app_id: str,
        window_title: str | None = None,
        window_class: str | None = None,
        process_name: str | None = None,
        source: str = "window_binding",
        reason: str | None = None,
        weight: int = 28,
    ) -> None:
        normalized_title = str(window_title or "").strip()
        normalized_class = str(window_class or "").strip()
        normalized_process = str(process_name or "").strip().lower()
        if not app_id or not any((normalized_title, normalized_class, normalized_process)):
            return
        payload = self._load()
        apps = payload.setdefault("apps", {})
        app_bucket = apps.setdefault(app_id, {"selectors": []})
        window_items = list(app_bucket.get("windows") or [])
        window_items = [
            item
            for item in window_items
            if not (
                isinstance(item, dict)
                and str(item.get("title") or "").strip() == normalized_title
                and str(item.get("className") or "").strip() == normalized_class
                and str(item.get("processName") or "").strip().lower() == normalized_process
                and str(item.get("source") or "") == str(source or "")
            )
        ]
        window_items.insert(
            0,
            {
                "title": normalized_title or None,
                "className": normalized_class or None,
                "processName": normalized_process or None,
                "source": str(source or "window_binding").strip() or "window_binding",
                "reason": str(reason).strip() if reason else None,
                "weight": max(8, min(int(weight), 96)),
                "updatedAt": utc_now_iso(),
            },
        )
        app_bucket["windows"] = window_items[: self._max_windows_per_app]
        self._save(payload)

    def remember_interaction(
        self,
        *,
        app_id: str,
        patch: Dict[str, Any] | None,
        source: str,
        reason: str | None = None,
        weight: int = 36,
        action_name: str | None = None,
        selector_key: str | None = None,
        target_text: str | None = None,
        control_type: str | None = None,
        window_class: str | None = None,
        window_title: str | None = None,
    ) -> None:
        normalized_patch = self._normalize_interaction_patch(patch)
        if not app_id or not normalized_patch:
            return
        match = self._normalize_interaction_match(
            action_name=action_name,
            selector_key=selector_key,
            target_text=target_text,
            control_type=control_type,
            window_class=window_class,
            window_title=window_title,
        )
        payload = self._load()
        apps = payload.setdefault("apps", {})
        app_bucket = apps.setdefault(app_id, {"selectors": []})
        items = list(app_bucket.get("interactions") or [])
        source_name = str(source or "runtime_interaction").strip() or "runtime_interaction"
        items = [
            item
            for item in items
            if not (
                isinstance(item, dict)
                and dict(item.get("match") or {}) == match
                and str(item.get("source") or "") == source_name
            )
        ]
        items.insert(
            0,
            {
                "match": match,
                "patch": normalized_patch,
                "source": source_name,
                "reason": str(reason).strip() if reason else None,
                "weight": max(8, min(int(weight), 96)),
                "updatedAt": utc_now_iso(),
            },
        )
        app_bucket["interactions"] = items[: self._max_interactions_per_app]
        self._save(payload)

    def remember_target_strategy(
        self,
        *,
        app_id: str,
        strategy: Dict[str, Any] | None,
        source: str,
        reason: str | None = None,
        weight: int = 40,
        action_name: str | None = None,
        selector_key: str | None = None,
        target_text: str | None = None,
        window_class: str | None = None,
        window_title: str | None = None,
    ) -> None:
        normalized_strategy = normalize_target_strategy(strategy)
        if not app_id or not normalized_strategy:
            return
        match = self._normalize_interaction_match(
            action_name=action_name,
            selector_key=selector_key,
            target_text=target_text or normalized_strategy.get("target_text"),
            window_class=window_class,
            window_title=window_title,
        )
        if normalized_strategy.get("target_text") and "targetText" not in match:
            match["targetText"] = self._normalize_text_key(normalized_strategy.get("target_text"))
            match["targetTextRaw"] = str(normalized_strategy.get("target_text") or "").strip()
        payload = self._load()
        apps = payload.setdefault("apps", {})
        app_bucket = apps.setdefault(app_id, {"selectors": []})
        items = list(app_bucket.get("targetStrategies") or [])
        source_name = str(source or "target_strategy").strip() or "target_strategy"
        items = [
            item
            for item in items
            if not (
                isinstance(item, dict)
                and dict(item.get("match") or {}) == match
                and str(item.get("source") or "") == source_name
            )
        ]
        items.insert(
            0,
            {
                "match": match,
                "strategy": normalized_strategy,
                "source": source_name,
                "reason": str(reason).strip() if reason else None,
                "weight": max(8, min(int(weight), 96)),
                "updatedAt": utc_now_iso(),
            },
        )
        app_bucket["targetStrategies"] = items[: self._max_target_strategies_per_app]
        self._save(payload)

    def remember_governance_event(
        self,
        *,
        app_id: str,
        event: Dict[str, Any] | None,
        source: str = "rpa_template_governance",
        reason: str | None = None,
        weight: int = 48,
    ) -> None:
        normalized_event = self._normalize_governance_event(event)
        if not app_id or not normalized_event:
            return
        payload = self._load()
        apps = payload.setdefault("apps", {})
        app_bucket = apps.setdefault(app_id, {"selectors": []})
        items = list(app_bucket.get("governanceEvents") or [])
        source_name = str(source or "rpa_template_governance").strip() or "rpa_template_governance"
        items = [
            item
            for item in items
            if not (
                isinstance(item, dict)
                and dict(item.get("event") or {}) == normalized_event
                and str(item.get("source") or "") == source_name
            )
        ]
        items.insert(
            0,
            {
                "event": normalized_event,
                "source": source_name,
                "reason": str(reason).strip() if reason else None,
                "weight": max(8, min(int(weight), 96)),
                "updatedAt": utc_now_iso(),
            },
        )
        app_bucket["governanceEvents"] = items[: self._max_governance_events_per_app]
        self._save(payload)

    def get_hints(
        self,
        *,
        app_id: str | None,
        window_class: str | None = None,
        window_title: str | None = None,
        action_name: str | None = None,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        if not app_id:
            return []
        payload = self._load()
        apps = dict(payload.get("apps") or {})
        bucket = dict(apps.get(app_id) or {})
        selector_items = list(bucket.get("selectors") or [])
        title_lower = str(window_title or "").strip().lower()
        class_lower = str(window_class or "").strip().lower()
        action_lower = str(action_name or "").strip().lower()

        ranked: List[tuple[int, Dict[str, Any]]] = []
        for item in selector_items:
            if not isinstance(item, dict):
                continue
            selector = self._normalize_selector(dict(item.get("selector") or {}))
            if not selector:
                continue
            score = int(item.get("weight") or 0)
            item_class = str(item.get("windowClass") or "").strip().lower()
            item_title = str(item.get("windowTitle") or "").strip().lower()
            item_action = str(item.get("actionName") or "").strip().lower()
            item_source = str(item.get("source") or "").strip().lower()
            if class_lower and item_class and class_lower == item_class:
                score += 12
            if title_lower and item_title and item_title in title_lower:
                score += 10
            if action_lower and item_action and action_lower == item_action:
                score += 8
            if item_source in {"visual_guard_confirmed", "visual_guard_pre_confirmed"}:
                score += 18
            elif item_source == "visual_guard_post_confirmed":
                score += 16
            elif item_source == "visual_recovery_success":
                score += 12
            elif item_source in {"visual_guard_unconfirmed", "visual_guard_pre_unconfirmed"}:
                score -= 6
            elif item_source == "visual_guard_post_unconfirmed":
                score -= 4
            ranked.append(
                (
                    score,
                    {
                        "selector": selector,
                        "source": str(item.get("source") or ""),
                        "reason": item.get("reason"),
                        "weight": score,
                    },
                )
            )
        ranked.sort(key=lambda item: -item[0])
        return [payload for _score, payload in ranked[: max(1, limit)]]

    def get_window_hints(
        self,
        *,
        app_id: str | None,
        window_title: str | None = None,
        window_class: str | None = None,
        process_names: List[str] | None = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        if not app_id:
            return []
        payload = self._load()
        apps = dict(payload.get("apps") or {})
        bucket = dict(apps.get(app_id) or {})
        window_items = list(bucket.get("windows") or [])
        title_lower = str(window_title or "").strip().lower()
        class_lower = str(window_class or "").strip().lower()
        accepted_process_names = {str(item).strip().lower() for item in (process_names or []) if str(item).strip()}

        ranked: List[tuple[int, Dict[str, Any]]] = []
        for item in window_items:
            if not isinstance(item, dict):
                continue
            score = int(item.get("weight") or 0)
            item_title = str(item.get("title") or "").strip()
            item_class = str(item.get("className") or "").strip()
            item_process = str(item.get("processName") or "").strip().lower()
            if title_lower and item_title and item_title.lower() in title_lower:
                score += 16
            if class_lower and item_class and item_class.lower() == class_lower:
                score += 12
            if accepted_process_names and item_process and item_process in accepted_process_names:
                score += 14
            ranked.append(
                (
                    score,
                    {
                        "title": item_title or None,
                        "className": item_class or None,
                        "processName": item_process or None,
                        "source": str(item.get("source") or ""),
                        "reason": item.get("reason"),
                        "weight": score,
                    },
                )
            )
        ranked.sort(key=lambda item: -item[0])
        return [entry for _score, entry in ranked[: max(1, limit)]]

    def get_interaction_patch(
        self,
        *,
        app_id: str | None,
        action_name: str | None = None,
        selector_key: str | None = None,
        target_text: str | None = None,
        control_type: str | None = None,
        window_class: str | None = None,
        window_title: str | None = None,
        limit: int = 3,
    ) -> Dict[str, Any]:
        if not app_id:
            return {}
        payload = self._load()
        apps = dict(payload.get("apps") or {})
        bucket = dict(apps.get(app_id) or {})
        items = list(bucket.get("interactions") or [])
        target_text_key = self._normalize_text_key(target_text)
        selector_key_value = str(selector_key or "").strip()
        action_name_value = str(action_name or "").strip().lower()
        control_type_value = str(control_type or "").strip().lower()
        window_class_value = str(window_class or "").strip().lower()
        window_title_value = str(window_title or "").strip().lower()

        ranked: List[tuple[int, Dict[str, Any], Dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            match = dict(item.get("match") or {})
            patch = self._normalize_interaction_patch(dict(item.get("patch") or {}))
            if not patch:
                continue
            item_selector_key = str(match.get("selectorKey") or "").strip()
            if selector_key_value and item_selector_key and item_selector_key != selector_key_value:
                continue
            item_action_name = str(match.get("actionName") or "").strip().lower()
            if action_name_value and item_action_name and item_action_name != action_name_value:
                continue
            item_control_type = str(match.get("controlType") or "").strip().lower()
            if control_type_value and item_control_type and item_control_type != control_type_value:
                continue
            item_window_class = str(match.get("windowClass") or "").strip().lower()
            if window_class_value and item_window_class and item_window_class != window_class_value:
                continue
            item_target_text = str(match.get("targetText") or "").strip()
            if target_text_key and item_target_text and item_target_text != target_text_key:
                continue

            score = int(item.get("weight") or 0)
            if selector_key_value and item_selector_key == selector_key_value:
                score += 18
            if action_name_value and item_action_name == action_name_value:
                score += 14
            if control_type_value and item_control_type == control_type_value:
                score += 8
            if window_class_value and item_window_class == window_class_value:
                score += 10
            item_window_title = str(match.get("windowTitle") or "").strip().lower()
            if window_title_value and item_window_title:
                if item_window_title in window_title_value or window_title_value in item_window_title:
                    score += 8
                else:
                    score -= 3
            if target_text_key:
                if item_target_text == target_text_key:
                    score += 22
                elif not item_target_text:
                    score += 4
            elif item_target_text:
                score -= 4
            item_source = str(item.get("source") or "").strip().lower()
            if item_source in {"learned_coordinate_interaction", "learned_sendinput_interaction"}:
                score += 10
            elif item_source == "learned_interaction":
                score += 8
            ranked.append((score, patch, item))

        if not ranked:
            return {}
        ranked.sort(key=lambda entry: -entry[0])
        merged_patch: Dict[str, Any] = {}
        matches: List[Dict[str, Any]] = []
        for score, patch, item in ranked[: max(1, limit)]:
            merged_patch = self._merge_interaction_patch(merged_patch, patch)
            matches.append(
                {
                    "source": str(item.get("source") or ""),
                    "reason": item.get("reason"),
                    "weight": score,
                    "match": dict(item.get("match") or {}),
                }
            )
        if not merged_patch:
            return {}
        return {
            "patch": merged_patch,
            "matches": matches,
            "weight": matches[0]["weight"] if matches else 0,
        }

    def get_target_strategy(
        self,
        *,
        app_id: str | None,
        action_name: str | None = None,
        selector_key: str | None = None,
        target_text: str | None = None,
        window_class: str | None = None,
        window_title: str | None = None,
        limit: int = 4,
    ) -> Dict[str, Any]:
        if not app_id:
            return {}
        payload = self._load()
        apps = dict(payload.get("apps") or {})
        bucket = dict(apps.get(app_id) or {})
        items = list(bucket.get("targetStrategies") or [])
        target_text_key = self._normalize_text_key(target_text)
        selector_key_value = str(selector_key or "").strip()
        selector_key_lower = selector_key_value.lower()
        action_name_value = str(action_name or "").strip().lower()
        window_class_value = str(window_class or "").strip().lower()
        window_title_value = str(window_title or "").strip().lower()

        ranked: List[tuple[int, Dict[str, Any], Dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            match = dict(item.get("match") or {})
            strategy = normalize_target_strategy(dict(item.get("strategy") or {}))
            if not strategy:
                continue
            item_target_text = str(match.get("targetText") or "").strip()
            if target_text_key and item_target_text and item_target_text != target_text_key:
                continue

            score = int(item.get("weight") or 0)
            item_selector_key = str(match.get("selectorKey") or "").strip()
            item_selector_lower = item_selector_key.lower()
            item_action_name = str(match.get("actionName") or "").strip().lower()
            item_window_class = str(match.get("windowClass") or "").strip().lower()
            item_window_title = str(match.get("windowTitle") or "").strip().lower()

            if target_text_key:
                if item_target_text == target_text_key:
                    score += 28
                elif not item_target_text:
                    score += 4
            if selector_key_value:
                if item_selector_key == selector_key_value:
                    score += 16
                elif is_search_selector_key(selector_key_lower) and is_search_selector_key(item_selector_lower):
                    score += 6
                elif is_result_selector_key(selector_key_lower) and is_result_selector_key(item_selector_lower):
                    score += 6
            if action_name_value and item_action_name:
                if item_action_name == action_name_value:
                    score += 12
                elif item_action_name in {"click", "double_click"} and action_name_value in {"click", "double_click"}:
                    score += 6
            if window_class_value and item_window_class:
                if item_window_class == window_class_value:
                    score += 10
                else:
                    score -= 2
            if window_title_value and item_window_title:
                if item_window_title in window_title_value or window_title_value in item_window_title:
                    score += 8
                else:
                    score -= 2
            if is_search_selector_key(selector_key_lower) and str(strategy.get("query_text") or "").strip():
                score += 8
            if is_result_selector_key(selector_key_lower) and str(strategy.get("preferred_result_region") or "").strip():
                score += 8
            if is_result_selector_key(selector_key_lower) and str(strategy.get("preferred_result_section") or "").strip():
                score += 6
            if is_result_selector_key(selector_key_lower) and str(strategy.get("preferred_hit_zone") or "").strip():
                score += 6
            if is_result_selector_key(selector_key_lower) and str(strategy.get("activation_gesture") or "").strip():
                score += 4
            item_source = str(item.get("source") or "").strip().lower()
            if item_source == "learned_search_result_strategy":
                score += 10
            elif item_source == "learned_search_strategy":
                score += 8
            ranked.append((score, strategy, item))

        if not ranked:
            return {}
        ranked.sort(key=lambda entry: -entry[0])
        merged_strategy: Dict[str, Any] = {}
        matches: List[Dict[str, Any]] = []
        for score, strategy, item in ranked[: max(1, limit)]:
            merged_strategy = merge_target_strategies(merged_strategy, strategy)
            matches.append(
                {
                    "source": str(item.get("source") or ""),
                    "reason": item.get("reason"),
                    "weight": score,
                    "match": dict(item.get("match") or {}),
                    "strategy": strategy,
                }
            )
        if not merged_strategy:
            return {}
        return {
            "strategy": merged_strategy,
            "matches": matches,
            "weight": matches[0]["weight"] if matches else 0,
        }

    def get_governance_hints(
        self,
        *,
        app_id: str | None,
        selector_key: str | None = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        if not app_id:
            return []
        payload = self._load()
        apps = dict(payload.get("apps") or {})
        bucket = dict(apps.get(app_id) or {})
        items = list(bucket.get("governanceEvents") or [])
        selector_value = str(selector_key or "").strip()
        ranked: List[tuple[int, Dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            event = self._normalize_governance_event(dict(item.get("event") or {}))
            if not event:
                continue
            strategy_keys = [str(value).strip() for value in list(event.get("targetStrategyKeys") or []) if str(value).strip()]
            score = int(item.get("weight") or 0)
            if selector_value and strategy_keys and selector_value in strategy_keys:
                score += 10
            stage = str(event.get("stage") or "").strip().lower()
            execution_path = str(event.get("executionPath") or "").strip().lower()
            if stage == "approved_live":
                score += 8
            elif stage in {"approved_at_risk", "frozen_hold", "rejected_hold"}:
                score += 14
            if execution_path == "computer_use_first":
                score += 12
            if str(event.get("eventType") or "").strip().lower().startswith("template_auto_"):
                score += 6
            ranked.append(
                (
                    score,
                    {
                        "event": event,
                        "source": str(item.get("source") or ""),
                        "reason": item.get("reason"),
                        "weight": score,
                    },
                )
            )
        ranked.sort(key=lambda entry: -entry[0])
        return [payload for _score, payload in ranked[: max(1, limit)]]
