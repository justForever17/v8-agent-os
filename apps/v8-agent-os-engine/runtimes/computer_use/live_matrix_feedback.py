from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List


REPORT_ROOT = Path.home() / ".v8-agent-os" / "reports" / "computer_use"
REPORT_GLOB = "primitive_live_matrix_*.json"


def _normalize_action(value: Any) -> str:
    return str(value or "").strip().lower()


def _match_runtime_actions(case: Dict[str, Any]) -> List[str]:
    runtime_actions = [_normalize_action(item) for item in list(case.get("runtimeActions") or []) if _normalize_action(item)]
    if runtime_actions:
        return runtime_actions
    primitive = _normalize_action(case.get("primitive"))
    fallback_map = {
        "launch_app": ["open_app"],
        "ensure_window": ["focus_window"],
        "observe_scene": ["observe"],
        "click_target": ["click", "double_click", "right_click", "hover"],
        "right_click_target": ["right_click"],
        "hover_target": ["hover"],
        "input_text": ["type_text", "find_and_type"],
        "paste_text": ["type_text"],
        "paste_files": ["type_text"],
        "send_hotkey": ["hotkey"],
        "scroll_view": ["scroll", "page_scroll", "scroll_list"],
        "drag_pointer": ["drag"],
    }
    return list(fallback_map.get(primitive, []))


def _load_report(path: Path) -> Dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _recent_report_paths(*, report_root: Path, limit: int) -> List[Path]:
    if not report_root.exists():
        return []
    paths = sorted(report_root.glob(REPORT_GLOB), key=lambda item: item.stat().st_mtime, reverse=True)
    return paths[: max(1, limit)]


@lru_cache(maxsize=16)
def _cached_feedback(report_root_value: str, limit: int) -> Dict[str, Any]:
    report_root = Path(report_root_value)
    reports = []
    for path in _recent_report_paths(report_root=report_root, limit=limit):
        payload = _load_report(path)
        if isinstance(payload, dict) and str(payload.get("mode") or "").strip().lower() != "status_only":
            payload["_reportPath"] = str(path)
            reports.append(payload)
    by_action: Dict[str, Dict[str, Any]] = {}
    by_acceptance: Dict[str, Dict[str, Any]] = {}
    for report in reports:
        report_path = str(report.get("_reportPath") or "")
        generated_at = str(report.get("generatedAt") or "")
        acceptance_id = str(report.get("acceptanceId") or report.get("name") or "").strip() or "default_acceptance"
        acceptance_entry = by_acceptance.setdefault(
            acceptance_id,
            {
                "acceptanceId": acceptance_id,
                "name": str(report.get("name") or "").strip() or acceptance_id,
                "tags": [],
                "reportCount": 0,
                "caseCount": 0,
                "executedCount": 0,
                "passedCount": 0,
                "requiredCount": 0,
                "requiredFailedCount": 0,
                "skippedCount": 0,
                "assetSkippedCount": 0,
                "blockedCount": 0,
                "visualLocatorCaseCount": 0,
                "verifiedVisualLocatorCaseCount": 0,
                "visualLocatorReadTextCaseCount": 0,
                "ocrEnhancedCaseCount": 0,
                "visualLocatorProviders": [],
                "latestReportPath": "",
                "latestGeneratedAt": "",
            },
        )
        acceptance_entry["reportCount"] += 1
        acceptance_entry["tags"].extend([str(item).strip() for item in list(report.get("tags") or []) if str(item).strip()])
        if not acceptance_entry["latestReportPath"]:
            acceptance_entry["latestReportPath"] = report_path
            acceptance_entry["latestGeneratedAt"] = generated_at
        for case in list(report.get("cases") or []):
            if not isinstance(case, dict) or case.get("enabled") is False:
                continue
            acceptance_entry["caseCount"] += 1
            if bool(case.get("required")):
                acceptance_entry["requiredCount"] += 1
            if bool(case.get("skipped")):
                acceptance_entry["skippedCount"] += 1
                if str(case.get("reason") or "").strip() == "asset_missing":
                    acceptance_entry["assetSkippedCount"] += 1
                continue
            acceptance_entry["executedCount"] += 1
            if bool(case.get("passed")):
                acceptance_entry["passedCount"] += 1
            elif bool(case.get("required")):
                acceptance_entry["requiredFailedCount"] += 1
            if bool(case.get("blocked")):
                acceptance_entry["blockedCount"] += 1
            visual_locator = dict(case.get("visualLocator") or {})
            ocr_enhancement = dict(case.get("ocrEnhancement") or {})
            if bool(visual_locator.get("used")):
                acceptance_entry["visualLocatorCaseCount"] += 1
                acceptance_entry["verifiedVisualLocatorCaseCount"] += int(visual_locator.get("verifiedCount") or 0)
                if int(visual_locator.get("readTextCount") or 0) > 0:
                    acceptance_entry["visualLocatorReadTextCaseCount"] += 1
                acceptance_entry["visualLocatorProviders"].extend(
                    [str(item).strip() for item in list(visual_locator.get("providerIds") or []) if str(item).strip()]
                )
            if bool(ocr_enhancement.get("enabled")):
                acceptance_entry["ocrEnhancedCaseCount"] += 1
            for action in _match_runtime_actions(case):
                entry = by_action.setdefault(
                    action,
                    {
                        "runtimeAction": action,
                        "sampleCount": 0,
                        "passedCount": 0,
                        "requiredCount": 0,
                        "requiredFailedCount": 0,
                        "blockedCount": 0,
                        "latestElapsedMs": 0,
                        "latestCaseId": "",
                        "latestReportPath": "",
                        "latestGeneratedAt": "",
                        "caseIds": [],
                        "verifiedCount": 0,
                        "softVerifiedCount": 0,
                        "reviewRequiredCount": 0,
                    },
                )
                entry["sampleCount"] += 1
                if bool(case.get("passed")):
                    entry["passedCount"] += 1
                verification_level = str(case.get("verificationLevel") or "").strip().lower()
                if verification_level == "verified":
                    entry["verifiedCount"] += 1
                elif verification_level == "soft_verified":
                    entry["softVerifiedCount"] += 1
                elif verification_level == "review_required":
                    entry["reviewRequiredCount"] += 1
                if bool(case.get("required")):
                    entry["requiredCount"] += 1
                    if not bool(case.get("passed")):
                        entry["requiredFailedCount"] += 1
                if bool(case.get("blocked")):
                    entry["blockedCount"] += 1
                if not entry["latestReportPath"]:
                    entry["latestElapsedMs"] = int(((case.get("budget") or {}).get("elapsedMs")) or 0)
                    entry["latestCaseId"] = str(case.get("id") or "")
                    entry["latestReportPath"] = report_path
                    entry["latestGeneratedAt"] = generated_at
                entry["caseIds"].append(str(case.get("id") or ""))

    for entry in by_action.values():
        sample_count = max(1, int(entry.get("sampleCount") or 0))
        pass_rate = float(entry.get("passedCount") or 0) / sample_count
        entry["passRate"] = round(pass_rate, 4)
        strong_rate = float(entry.get("verifiedCount") or 0) / sample_count
        soft_rate = float(entry.get("softVerifiedCount") or 0) / sample_count
        review_rate = float(entry.get("reviewRequiredCount") or 0) / sample_count
        entry["strongVerificationRate"] = round(strong_rate, 4)
        entry["softVerificationRate"] = round(soft_rate, 4)
        entry["reviewRequiredRate"] = round(review_rate, 4)
        if int(entry.get("sampleCount") or 0) <= 0:
            status = "unknown"
        elif int(entry.get("requiredFailedCount") or 0) > 0 or pass_rate < 0.5:
            status = "unhealthy"
        elif pass_rate < 0.85 or int(entry.get("blockedCount") or 0) > 0 or strong_rate < 0.5:
            status = "degraded"
        else:
            status = "healthy"
        entry["status"] = status
        entry["gateSuggestion"] = (
            "require_strong_verification"
            if status == "unhealthy"
            else "prefer_stronger_verification"
            if strong_rate < 0.5
            else "tighten_verification"
            if status == "degraded"
            else "allow_normal_execution"
        )
        entry["caseIds"] = list(dict.fromkeys(entry["caseIds"]))
    for entry in by_acceptance.values():
        executed_count = max(0, int(entry.get("executedCount") or 0))
        required_count = int(entry.get("requiredCount") or 0)
        pass_rate = float(entry.get("passedCount") or 0) / max(1, executed_count) if executed_count > 0 else 0.0
        visual_locator_case_count = int(entry.get("visualLocatorCaseCount") or 0)
        verified_visual_locator_rate = (
            float(entry.get("verifiedVisualLocatorCaseCount") or 0) / max(1, visual_locator_case_count)
            if visual_locator_case_count > 0
            else 0.0
        )
        if executed_count == 0 and int(entry.get("assetSkippedCount") or 0) > 0 and int(entry.get("requiredFailedCount") or 0) == 0:
            status = "awaiting_assets"
        elif int(entry.get("requiredFailedCount") or 0) > 0 or (executed_count > 0 and pass_rate < 0.5):
            status = "unhealthy"
        elif (executed_count > 0 and pass_rate < 0.85) or int(entry.get("assetSkippedCount") or 0) > 0 or (
            visual_locator_case_count > 0 and verified_visual_locator_rate < 0.5
        ):
            status = "degraded"
        elif required_count <= 0 and executed_count <= 0:
            status = "unknown"
        else:
            status = "healthy"
        entry["status"] = status
        entry["passRate"] = round(pass_rate, 4) if executed_count > 0 else 0.0
        entry["verifiedVisualLocatorRate"] = round(verified_visual_locator_rate, 4) if visual_locator_case_count > 0 else 0.0
        entry["visualLocatorProviders"] = list(dict.fromkeys(entry.get("visualLocatorProviders") or []))
        entry["tags"] = list(dict.fromkeys(entry.get("tags") or []))
        normalized_tags = {str(item).strip().lower() for item in list(entry.get("tags") or []) if str(item).strip()}
        normalized_acceptance_id = _normalize_action(entry.get("acceptanceId"))
        normalized_name = _normalize_action(entry.get("name"))
        entry["hasVisualLocatorIntent"] = bool(
            "visual_locator" in normalized_tags
            or (
                ("acceptance" in normalized_acceptance_id or "acceptance" in normalized_name)
                and (visual_locator_case_count > 0 or int(entry.get("assetSkippedCount") or 0) > 0)
            )
        )
    return {"reports": reports, "byAction": by_action, "byAcceptance": by_acceptance}


def invalidate_live_matrix_feedback_cache() -> None:
    _cached_feedback.cache_clear()


def primitive_live_feedback_for_action(
    action_type: str,
    *,
    report_root: Path | None = None,
    recent_limit: int = 5,
) -> Dict[str, Any] | None:
    normalized_action = _normalize_action(action_type)
    if not normalized_action:
        return None
    root = str((report_root or REPORT_ROOT).resolve())
    payload = _cached_feedback(root, max(1, recent_limit))
    entry = dict((payload.get("byAction") or {}).get(normalized_action) or {})
    if not entry:
        return None
    return entry


def primitive_live_feedback_snapshot(
    *,
    actions: Iterable[str] | None = None,
    report_root: Path | None = None,
    recent_limit: int = 5,
) -> Dict[str, Any]:
    root = str((report_root or REPORT_ROOT).resolve())
    payload = _cached_feedback(root, max(1, recent_limit))
    by_action = dict(payload.get("byAction") or {})
    requested_actions = [_normalize_action(item) for item in list(actions or []) if _normalize_action(item)]
    if requested_actions:
        by_action = {key: value for key, value in by_action.items() if key in requested_actions}
    return {
        "reportCount": len(list(payload.get("reports") or [])),
        "actions": {key: dict(value) for key, value in by_action.items()},
    }


def visual_acceptance_feedback_for_id(
    acceptance_id: str,
    *,
    report_root: Path | None = None,
    recent_limit: int = 5,
) -> Dict[str, Any] | None:
    normalized_acceptance = _normalize_action(acceptance_id)
    if not normalized_acceptance:
        return None
    root = str((report_root or REPORT_ROOT).resolve())
    payload = _cached_feedback(root, max(1, recent_limit))
    for key, value in dict(payload.get("byAcceptance") or {}).items():
        if _normalize_action(key) == normalized_acceptance:
            return dict(value)
    return None


def visual_acceptance_feedback_snapshot(
    *,
    acceptance_ids: Iterable[str] | None = None,
    report_root: Path | None = None,
    recent_limit: int = 5,
) -> Dict[str, Any]:
    root = str((report_root or REPORT_ROOT).resolve())
    payload = _cached_feedback(root, max(1, recent_limit))
    by_acceptance = dict(payload.get("byAcceptance") or {})
    requested_ids = [_normalize_action(item) for item in list(acceptance_ids or []) if _normalize_action(item)]
    if requested_ids:
        by_acceptance = {
            key: value
            for key, value in by_acceptance.items()
            if _normalize_action(key) in requested_ids
        }
    else:
        by_acceptance = {
            key: value
            for key, value in by_acceptance.items()
            if bool((value or {}).get("hasVisualLocatorIntent"))
        }
    statuses = [_normalize_action((value or {}).get("status")) for value in by_acceptance.values()]
    if not statuses:
        aggregate_status = "unknown"
    elif "unhealthy" in statuses:
        aggregate_status = "unhealthy"
    elif "degraded" in statuses:
        aggregate_status = "degraded"
    elif "awaiting_assets" in statuses:
        aggregate_status = "awaiting_assets"
    elif "healthy" in statuses:
        aggregate_status = "healthy"
    else:
        aggregate_status = "unknown"
    return {
        "reportCount": len(list(payload.get("reports") or [])),
        "acceptanceCount": len(by_acceptance),
        "aggregateStatus": aggregate_status,
        "acceptances": {key: dict(value) for key, value in by_acceptance.items()},
    }
