from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List


def normalize_expected_texts(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        normalized: List[str] = []
        for item in value:
            token = str(item or "").strip()
            if token:
                normalized.append(token)
        return normalized
    token = str(value or "").strip()
    return [token] if token else []


def visual_text_matches(*, read_text: Any, expected_texts: Iterable[str]) -> bool:
    normalized_expected = [str(item or "").strip() for item in expected_texts if str(item or "").strip()]
    if not normalized_expected:
        return True
    haystack = str(read_text or "").strip().lower()
    if not haystack:
        return False
    return all(token.lower() in haystack for token in normalized_expected)


def _compact_text(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _frame(bundle: Dict[str, Any], key: str) -> Dict[str, Any]:
    return dict(((bundle.get("frames") or {}).get(key)) or {})


def _changed_flags(bundle: Dict[str, Any]) -> Dict[str, bool]:
    merged: Dict[str, bool] = {}
    for source in ("preToPost", "midToPost"):
        for key, value in dict(((bundle.get("diff") or {}).get(source)) or {}).items():
            merged[key] = bool(value) or bool(merged.get(key))
    return merged


def _frame_contains_any_text(frame: Dict[str, Any], tokens: Iterable[str]) -> bool:
    haystack = _compact_text(" ".join(str(frame.get(key) or "") for key in ("windowTitle", "pageIdentity", "pageTitle", "pageUrl", "textDigest")))
    if not haystack:
        return False
    for token in tokens:
        normalized = _compact_text(token)
        if normalized and normalized in haystack:
            return True
    return False


def _build_semantic_evidence(
    *,
    action_type: str,
    action_payload: Dict[str, Any] | None,
    verification_details: Dict[str, Any] | None,
    observation_bundle: Dict[str, Any] | None,
) -> Dict[str, Any]:
    bundle = dict(observation_bundle or {})
    sampling_available = bool(bundle.get("enabled"))
    semantic_available = sampling_available
    if not sampling_available:
        return {
            "available": False,
            "passed": None,
            "status": "frame_sequence_unavailable",
            "level": None,
            "reason": "当前动作没有可用的三帧 observation bundle，无法执行语义验证。",
            "evidenceType": None,
            "evidenceSummary": None,
            "frameSequenceSamplingAvailable": False,
            "frameSequenceSemanticVerificationAvailable": False,
            "samplingSource": "computer_use_local_capture",
        }

    details = dict(verification_details or {})
    normalized_action = str(action_type or "").strip().lower()
    post_frame = _frame(bundle, "postAction")
    changed = _changed_flags(bundle)
    sampling_source = str(
        bundle.get("samplingSource")
        or ((bundle.get("desktopLive") or {}).get("source"))
        or "computer_use_local_capture"
    ).strip() or "computer_use_local_capture"
    action_context = dict(bundle.get("actionContext") or {})
    target_selector = dict(bundle.get("targetSelector") or {})
    expected_texts = normalize_expected_texts(
        action_payload.get("post_action_expect_text") if isinstance(action_payload, dict) else None
    )
    if not expected_texts and isinstance(action_payload, dict):
        expected_texts = normalize_expected_texts(
            action_payload.get("post_action_expect_text")
            or action_payload.get("postActionExpectText")
            or action_payload.get("post_action_expect_texts")
            or action_payload.get("postActionExpectTexts")
            or action_payload.get("target_text")
            or target_selector.get("targetText")
        )

    actual_text = str(details.get("actualText") or "").strip()
    if normalized_action == "paste_files":
        file_names = [Path(item).name for item in list((action_payload or {}).get("file_paths") or (action_payload or {}).get("attachment_paths") or []) if str(item or "").strip()]
        if not file_names and (action_payload or {}).get("file_path"):
            file_names = [Path(str((action_payload or {}).get("file_path"))).name]
        expected_texts = normalize_expected_texts(expected_texts + file_names)
    elif normalized_action == "type_text" and not expected_texts:
        expected_texts = normalize_expected_texts(actual_text or action_context.get("textPreview") or (action_payload or {}).get("text"))

    focus_state = dict(details.get("focusState") or {})
    change_evidence = dict(details.get("changeEvidence") or {})
    page_window_advanced = any(changed.get(key) for key in ("windowChanged", "pageIdentityChanged", "transitionStateChanged"))
    visual_region_advanced = any(changed.get(key) for key in ("treeChanged", "screenChanged", "blockerStateChanged"))
    text_visible = bool(change_evidence.get("observationTargetVisible")) or _frame_contains_any_text(post_frame, expected_texts)
    actual_text_matched = bool(actual_text) and visual_text_matches(read_text=actual_text, expected_texts=expected_texts)
    focus_confirmed = bool(focus_state.get("hasKeyboardFocus") or focus_state.get("isActiveWindow"))
    expected_window_title = str((action_payload or {}).get("window_title") or action_context.get("windowTitle") or "").strip()
    window_title_matched = False
    if expected_window_title:
        window_title_matched = _compact_text(expected_window_title) in _compact_text(post_frame.get("windowTitle"))

    if normalized_action in {"open_app", "focus_window"}:
        if window_title_matched or page_window_advanced or post_frame.get("windowHandle"):
            return {
                "available": True,
                "passed": True,
                "status": "semantic_window_verified",
                "level": "verified",
                "reason": "三帧观察已确认窗口上下文推进或目标窗口进入前台。",
                "evidenceType": "window_context_advanced",
                "evidenceSummary": f"windowTitle={post_frame.get('windowTitle') or ''} pageIdentity={post_frame.get('pageIdentity') or ''}".strip(),
                "frameSequenceSamplingAvailable": sampling_available,
                "frameSequenceSemanticVerificationAvailable": semantic_available,
                "samplingSource": sampling_source,
            }
        level = "failed" if normalized_action == "open_app" else "review_required"
        return {
            "available": True,
            "passed": False,
            "status": "semantic_window_unconfirmed",
            "level": level,
            "reason": "三帧观察未确认窗口句柄、标题或页面身份推进。",
            "evidenceType": "no_confirming_window_evidence",
            "evidenceSummary": f"windowChanged={changed.get('windowChanged')} pageIdentityChanged={changed.get('pageIdentityChanged')}",
            "frameSequenceSamplingAvailable": sampling_available,
            "frameSequenceSemanticVerificationAvailable": semantic_available,
            "samplingSource": sampling_source,
        }

    if normalized_action == "type_text":
        if actual_text_matched or text_visible:
            return {
                "available": True,
                "passed": True,
                "status": "semantic_text_verified",
                "level": "verified",
                "reason": "三帧观察与控件回读已确认输入文本进入目标区域。",
                "evidenceType": "target_text_matched",
                "evidenceSummary": actual_text or post_frame.get("textDigest"),
                "frameSequenceSamplingAvailable": sampling_available,
                "frameSequenceSemanticVerificationAvailable": semantic_available,
                "samplingSource": sampling_source,
            }
        return {
            "available": True,
            "passed": False,
            "status": "semantic_text_unconfirmed",
            "level": "review_required",
            "reason": "三帧观察未确认目标文本进入页面或控件。",
            "evidenceType": "no_confirming_text_evidence",
            "evidenceSummary": f"focusConfirmed={focus_confirmed} visualRegionAdvanced={visual_region_advanced}",
            "frameSequenceSamplingAvailable": sampling_available,
            "frameSequenceSemanticVerificationAvailable": semantic_available,
            "samplingSource": sampling_source,
        }

    if normalized_action == "scroll":
        if page_window_advanced or visual_region_advanced:
            return {
                "available": True,
                "passed": True,
                "status": "semantic_scroll_verified",
                "level": "verified",
                "reason": "三帧观察已确认滚动后的页面或区域状态变化。",
                "evidenceType": "viewport_state_changed",
                "evidenceSummary": f"treeChanged={changed.get('treeChanged')} screenChanged={changed.get('screenChanged')}",
                "frameSequenceSamplingAvailable": sampling_available,
                "frameSequenceSemanticVerificationAvailable": semantic_available,
                "samplingSource": sampling_source,
            }
        return {
            "available": True,
            "passed": False,
            "status": "semantic_scroll_unconfirmed",
            "level": "failed",
            "reason": "三帧观察未确认滚动产生可见状态变化。",
            "evidenceType": "viewport_state_static",
            "evidenceSummary": f"treeChanged={changed.get('treeChanged')} screenChanged={changed.get('screenChanged')}",
            "frameSequenceSamplingAvailable": sampling_available,
            "frameSequenceSemanticVerificationAvailable": semantic_available,
            "samplingSource": sampling_source,
        }

    if normalized_action == "paste_files":
        if text_visible:
            return {
                "available": True,
                "passed": True,
                "status": "semantic_file_attach_verified",
                "level": "verified",
                "reason": "三帧观察已在页面或窗口中发现附件文件名。",
                "evidenceType": "attachment_name_visible",
                "evidenceSummary": post_frame.get("textDigest"),
                "frameSequenceSamplingAvailable": sampling_available,
                "frameSequenceSemanticVerificationAvailable": semantic_available,
                "samplingSource": sampling_source,
            }
        if visual_region_advanced:
            return {
                "available": True,
                "passed": False,
                "status": "semantic_file_attach_unconfirmed",
                "level": "review_required",
                "reason": "三帧观察发现区域变化，但尚未确认文件名或附件状态。",
                "evidenceType": "attachment_region_changed_only",
                "evidenceSummary": post_frame.get("textDigest"),
                "frameSequenceSamplingAvailable": sampling_available,
                "frameSequenceSemanticVerificationAvailable": semantic_available,
                "samplingSource": sampling_source,
            }
        return {
            "available": True,
            "passed": False,
            "status": "semantic_file_attach_missing",
            "level": "review_required",
            "reason": "三帧观察未确认附件区域变化或文件名出现。",
            "evidenceType": "no_attachment_evidence",
            "evidenceSummary": post_frame.get("textDigest"),
            "frameSequenceSamplingAvailable": sampling_available,
            "frameSequenceSemanticVerificationAvailable": semantic_available,
            "samplingSource": sampling_source,
        }

    if normalized_action in {"click", "double_click"}:
        if page_window_advanced or visual_region_advanced or focus_confirmed:
            evidence_type = "page_or_window_advanced" if page_window_advanced else "control_state_advanced"
            return {
                "available": True,
                "passed": True,
                "status": "semantic_click_verified",
                "level": "verified",
                "reason": "三帧观察已确认点击后页面、窗口或控件状态发生推进。",
                "evidenceType": evidence_type,
                "evidenceSummary": f"focusConfirmed={focus_confirmed} changed={changed}",
                "frameSequenceSamplingAvailable": sampling_available,
                "frameSequenceSemanticVerificationAvailable": semantic_available,
                "samplingSource": sampling_source,
            }
        return {
            "available": True,
            "passed": False,
            "status": "semantic_click_unconfirmed",
            "level": "review_required",
            "reason": "三帧观察未确认点击导致的控件、页面或窗口状态推进。",
            "evidenceType": "no_confirming_click_evidence",
            "evidenceSummary": f"focusConfirmed={focus_confirmed} changed={changed}",
            "frameSequenceSamplingAvailable": sampling_available,
            "frameSequenceSemanticVerificationAvailable": semantic_available,
            "samplingSource": sampling_source,
        }

    if page_window_advanced or visual_region_advanced:
        return {
            "available": True,
            "passed": True,
            "status": "semantic_state_verified",
            "level": "verified",
            "reason": "三帧观察已确认动作后界面状态推进。",
            "evidenceType": "region_or_page_advanced",
            "evidenceSummary": str(changed),
            "frameSequenceSamplingAvailable": sampling_available,
            "frameSequenceSemanticVerificationAvailable": semantic_available,
            "samplingSource": sampling_source,
        }
    return {
        "available": True,
        "passed": False,
        "status": "semantic_unconfirmed",
        "level": "review_required",
        "reason": "三帧观察未给出足够的语义确认信号。",
        "evidenceType": "no_confirming_evidence",
        "evidenceSummary": str(changed),
        "frameSequenceSamplingAvailable": sampling_available,
        "frameSequenceSemanticVerificationAvailable": semantic_available,
        "samplingSource": sampling_source,
    }


def summarize_semantic_post_action_verification(
    *,
    action_type: str,
    action_payload: Dict[str, Any] | None,
    verification_details: Dict[str, Any] | None,
    observation_bundle: Dict[str, Any] | None,
) -> Dict[str, Any]:
    return _build_semantic_evidence(
        action_type=action_type,
        action_payload=action_payload,
        verification_details=verification_details,
        observation_bundle=observation_bundle,
    )


def summarize_post_action_visual_check(
    *,
    provider_id: str,
    locator: str,
    resolved: Dict[str, Any] | None,
    expected_texts: Iterable[str] | None = None,
    observation_bundle: Dict[str, Any] | None = None,
    error: str | None = None,
    action_type: str | None = None,
    action_payload: Dict[str, Any] | None = None,
    verification_details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = dict(resolved or {})
    normalized_expected = normalize_expected_texts(list(expected_texts or []))
    read_text = str(payload.get("readText") or "").strip()
    match_count = int(payload.get("matchCount") or 0)
    text_matched = visual_text_matches(read_text=read_text, expected_texts=normalized_expected)
    if error:
        status = "error"
        confirmed = False
        reason = error
    elif match_count <= 0:
        status = "not_found"
        confirmed = False
        reason = "动作后统一视觉定位未找到预期区域。"
    elif normalized_expected and not text_matched:
        status = "text_mismatch"
        confirmed = False
        reason = "动作后统一视觉定位已找到区域，但文本未匹配预期。"
    else:
        status = "verified"
        confirmed = True
        reason = "动作后统一视觉定位已确认预期结果。"
    semantic = summarize_semantic_post_action_verification(
        action_type=str(action_type or ""),
        action_payload=action_payload,
        verification_details=verification_details,
        observation_bundle=observation_bundle,
    )
    return {
        "providerId": provider_id,
        "locator": locator,
        "status": status,
        "confirmed": confirmed,
        "reason": reason,
        "matchCount": match_count,
        "matches": list(payload.get("matches") or []),
        "readText": read_text,
        "readTextError": payload.get("readTextError"),
        "expectedTexts": normalized_expected,
        "readTextMatched": text_matched,
        "latencyMs": int(payload.get("latencyMs") or 0),
        "usedTimeoutMs": int(payload.get("usedTimeoutMs") or 0),
        "usedConfidence": payload.get("usedConfidence"),
        "offset": list(payload.get("offset") or []),
        "observationBundle": dict(observation_bundle or {}),
        "frameSequenceEnabled": bool((observation_bundle or {}).get("enabled")),
        "frameStateAdvanced": bool(((observation_bundle or {}).get("diff") or {}).get("stateAdvanced")),
        "semanticVerificationStatus": semantic.get("status"),
        "semanticEvidenceType": semantic.get("evidenceType"),
        "semanticEvidenceSummary": semantic.get("evidenceSummary"),
        "frameSequenceSamplingAvailable": bool(semantic.get("frameSequenceSamplingAvailable")),
        "frameSequenceSemanticVerificationAvailable": bool(semantic.get("frameSequenceSemanticVerificationAvailable")),
    }
