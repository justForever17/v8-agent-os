from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence


WINDOWS_SHELL_CLASS_TOKENS = (
    "shell_traywnd",
    "workerw",
    "progman",
    "notifyiconoverflowwindow",
)

WINDOWS_DIALOG_CLASS_TOKENS = (
    "#32770",
    "dialog",
    "popup",
)


def normalize_window_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_window_payload(window: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(window or {})
    payload["title"] = str(payload.get("title") or payload.get("windowTitle") or "").strip()
    payload["className"] = str(payload.get("className") or payload.get("class_name") or "").strip()
    payload["processName"] = str(payload.get("processName") or payload.get("process_name") or "").strip()
    payload["handle"] = payload.get("handle") or payload.get("windowHandle") or payload.get("window_handle")
    payload["bounds"] = list(payload.get("bounds") or [])
    return payload


def _normalized_window_tokens(values: Iterable[Any] | None) -> List[str]:
    return [normalize_window_text(item) for item in list(values or []) if normalize_window_text(item)]


def window_title_match_score(candidate_title: Any, expected_titles: Iterable[Any] | None) -> int:
    candidate = normalize_window_text(candidate_title)
    if not candidate:
        return 0
    tokens = _normalized_window_tokens(expected_titles)
    if not tokens:
        return 0
    separators = (" - ", " | ", " — ", " – ", " · ", " • ")
    segments = {candidate}
    for separator in separators:
        if separator in candidate:
            segments.update(part.strip() for part in candidate.split(separator) if part.strip())
    best = 0
    for token in tokens:
        if not token:
            continue
        if token == candidate:
            best = max(best, 100)
            continue
        if token in segments:
            best = max(best, 72)
            continue
        if token in candidate:
            best = max(best, 40 + min(len(token), 20))
    return best


def is_shell_surface_window(window: Dict[str, Any] | None, *, platform: str = "windows") -> bool:
    payload = normalize_window_payload(window)
    if platform != "windows":
        return False
    class_name = normalize_window_text(payload.get("className"))
    return any(token in class_name for token in WINDOWS_SHELL_CLASS_TOKENS)


def is_probable_dialog_window(window: Dict[str, Any] | None, *, platform: str = "windows") -> bool:
    payload = normalize_window_payload(window)
    if platform != "windows":
        return False
    class_name = normalize_window_text(payload.get("className"))
    return any(token in class_name for token in WINDOWS_DIALOG_CLASS_TOKENS)


def is_suspicious_capture_bounds(
    bounds: Sequence[int] | None,
    *,
    display_bounds: Sequence[int] | None = None,
) -> bool:
    if not isinstance(bounds, Sequence) or len(bounds) != 4:
        return False
    left, top, right, bottom = [int(value) for value in bounds]
    width = max(0, right - left)
    height = max(0, bottom - top)
    if width <= 0 or height <= 0:
        return True
    if width <= 220 and height >= 520:
        return True
    if height <= 180 and width >= 900:
        return True
    if display_bounds and len(display_bounds) == 4:
        display_left, display_top, display_right, display_bottom = [int(value) for value in display_bounds]
        display_width = max(1, display_right - display_left)
        display_height = max(1, display_bottom - display_top)
        touches_bottom = bottom >= display_bottom - 8
        touches_top = top <= display_top + 8
        touches_left = left <= display_left + 8
        touches_right = right >= display_right - 8
        if touches_bottom and height <= max(180, int(display_height * 0.2)) and width >= int(display_width * 0.55):
            return True
        if touches_top and height <= max(120, int(display_height * 0.18)) and width >= int(display_width * 0.55):
            return True
        if (touches_left or touches_right) and width <= max(220, int(display_width * 0.18)) and height >= int(display_height * 0.5):
            return True
    return False


def score_window_candidate(
    window: Dict[str, Any] | None,
    *,
    expected_titles: Iterable[str] | None = None,
    expected_classes: Iterable[str] | None = None,
    expected_process_names: Iterable[str] | None = None,
    preferred_handle: int | None = None,
    platform: str = "windows",
) -> int:
    payload = normalize_window_payload(window)
    title = normalize_window_text(payload.get("title"))
    class_name = normalize_window_text(payload.get("className"))
    process_name = normalize_window_text(payload.get("processName"))
    handle = payload.get("handle")
    score = int(payload.get("matchScore") or 0)
    if preferred_handle not in (None, "", 0) and handle not in (None, "", 0):
        if int(handle) == int(preferred_handle):
            score += 24
    title_tokens = _normalized_window_tokens(expected_titles)
    class_tokens = _normalized_window_tokens(expected_classes)
    process_tokens = _normalized_window_tokens(expected_process_names)
    if title_tokens:
        score += window_title_match_score(title, title_tokens)
    if class_tokens:
        if any(token and token == class_name for token in class_tokens):
            score += 18
    if process_tokens:
        if any(token and token == process_name for token in process_tokens):
            score += 18
    if payload.get("isVisible") is True:
        score += 6
    if title:
        score += 4
    if is_probable_dialog_window(payload, platform=platform):
        score -= 8
    if is_shell_surface_window(payload, platform=platform):
        score -= 42
    return score


def requires_strict_window_binding(
    *,
    expected_titles: Iterable[str] | None = None,
    expected_classes: Iterable[str] | None = None,
) -> bool:
    return bool(_normalized_window_tokens(expected_titles) or _normalized_window_tokens(expected_classes))


def window_satisfies_binding(
    window: Dict[str, Any] | None,
    *,
    expected_titles: Iterable[str] | None = None,
    expected_classes: Iterable[str] | None = None,
    expected_process_names: Iterable[str] | None = None,
    platform: str = "windows",
    require_title_or_class_match: bool = False,
) -> bool:
    payload = normalize_window_payload(window)
    if not payload or is_shell_surface_window(payload, platform=platform):
        return False
    title = normalize_window_text(payload.get("title"))
    class_name = normalize_window_text(payload.get("className"))
    process_name = normalize_window_text(payload.get("processName"))
    title_tokens = _normalized_window_tokens(expected_titles)
    class_tokens = _normalized_window_tokens(expected_classes)
    process_tokens = _normalized_window_tokens(expected_process_names)
    title_match = not title_tokens or window_title_match_score(title, title_tokens) > 0
    class_match = not class_tokens or any(token and token == class_name for token in class_tokens)
    if process_tokens and not process_name and require_title_or_class_match:
        process_match = True
    else:
        process_match = not process_tokens or any(token and token == process_name for token in process_tokens)
    if require_title_or_class_match and (title_tokens or class_tokens):
        structural_match = (bool(title_tokens) and title_match) or (bool(class_tokens) and class_match)
        return bool(structural_match and process_match)
    return bool(title_match and class_match and process_match)


def choose_best_window_candidate(
    candidates: Iterable[Dict[str, Any]] | None,
    *,
    expected_titles: Iterable[str] | None = None,
    expected_classes: Iterable[str] | None = None,
    expected_process_names: Iterable[str] | None = None,
    preferred_handle: int | None = None,
    platform: str = "windows",
) -> Dict[str, Any] | None:
    ranked: List[tuple[int, Dict[str, Any]]] = []
    for item in list(candidates or []):
        payload = normalize_window_payload(item)
        if not payload:
            continue
        ranked.append(
            (
                score_window_candidate(
                    payload,
                    expected_titles=expected_titles,
                    expected_classes=expected_classes,
                    expected_process_names=expected_process_names,
                    preferred_handle=preferred_handle,
                    platform=platform,
                ),
                payload,
            )
        )
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[0], reverse=True)
    return ranked[0][1]


def should_replace_window_context(
    current_window: Dict[str, Any] | None,
    replacement_window: Dict[str, Any] | None,
    *,
    expected_titles: Iterable[str] | None = None,
    expected_classes: Iterable[str] | None = None,
    expected_process_names: Iterable[str] | None = None,
    platform: str = "windows",
) -> bool:
    replacement = normalize_window_payload(replacement_window)
    if not replacement:
        return False
    current = normalize_window_payload(current_window)
    strict_binding_required = requires_strict_window_binding(
        expected_titles=expected_titles,
        expected_classes=expected_classes,
    )
    replacement_strict_match = window_satisfies_binding(
        replacement,
        expected_titles=expected_titles,
        expected_classes=expected_classes,
        expected_process_names=expected_process_names,
        platform=platform,
        require_title_or_class_match=True,
    )
    if not current:
        return replacement_strict_match if strict_binding_required else True
    current_strict_match = window_satisfies_binding(
        current,
        expected_titles=expected_titles,
        expected_classes=expected_classes,
        expected_process_names=expected_process_names,
        platform=platform,
        require_title_or_class_match=True,
    )
    if strict_binding_required and not replacement_strict_match:
        return False
    if strict_binding_required and replacement_strict_match and not current_strict_match:
        return True
    if is_shell_surface_window(current, platform=platform) and not is_shell_surface_window(replacement, platform=platform):
        return True
    current_score = score_window_candidate(
        current,
        expected_titles=expected_titles,
        expected_classes=expected_classes,
        expected_process_names=expected_process_names,
        preferred_handle=current.get("handle"),
        platform=platform,
    )
    replacement_score = score_window_candidate(
        replacement,
        expected_titles=expected_titles,
        expected_classes=expected_classes,
        expected_process_names=expected_process_names,
        preferred_handle=current.get("handle"),
        platform=platform,
    )
    return replacement_score >= current_score + 12


def infer_window_page_identity(
    window: Dict[str, Any] | None,
    *,
    app_id: str | None = None,
    expected_titles: Iterable[str] | None = None,
    platform: str = "windows",
) -> Dict[str, Any]:
    payload = normalize_window_payload(window)
    title = str(payload.get("title") or "").strip()
    normalized_app = normalize_window_text(app_id) or normalize_window_text(payload.get("processName")) or "desktop"
    reasons: List[str] = []
    if not payload:
        return {
            "pageIdentity": f"{normalized_app}.window_unknown",
            "confidence": "low",
            "reasons": ["缺少窗口载荷，无法推断页面身份。"],
        }
    if is_shell_surface_window(payload, platform=platform):
        return {
            "pageIdentity": "windows.shell_surface",
            "confidence": "low",
            "reasons": ["当前窗口属于桌面壳层/任务栏，不应作为业务页面身份。"],
        }
    if is_probable_dialog_window(payload, platform=platform):
        reasons.append("窗口类名接近系统对话框。")
        return {
            "pageIdentity": f"{normalized_app}.dialog",
            "confidence": "medium",
            "reasons": reasons,
        }
    title_tokens = _normalized_window_tokens(expected_titles)
    normalized_title = normalize_window_text(title)
    if title_tokens and any(token and (token == normalized_title or token in normalized_title) for token in title_tokens):
        reasons.append("当前窗口标题命中预期标题提示。")
        return {
            "pageIdentity": f"{normalized_app}.bound_window",
            "confidence": "high",
            "reasons": reasons,
        }
    if normalized_title:
        reasons.append("根据当前窗口标题推断页面身份。")
        return {
            "pageIdentity": f"{normalized_app}.window",
            "confidence": "medium",
            "reasons": reasons,
        }
    reasons.append("窗口标题为空，仅能回退到应用级页面身份。")
    return {
        "pageIdentity": f"{normalized_app}.window_unknown",
        "confidence": "low",
        "reasons": reasons,
    }


def build_window_binding_assessment(
    window: Dict[str, Any] | None,
    *,
    expected_titles: Iterable[str] | None = None,
    expected_classes: Iterable[str] | None = None,
    expected_process_names: Iterable[str] | None = None,
    preferred_handle: int | None = None,
    app_id: str | None = None,
    platform: str = "windows",
) -> Dict[str, Any]:
    payload = normalize_window_payload(window)
    expected_title_tokens = _normalized_window_tokens(expected_titles)
    expected_class_tokens = _normalized_window_tokens(expected_classes)
    expected_process_tokens = _normalized_window_tokens(expected_process_names)
    strict_binding_required = requires_strict_window_binding(
        expected_titles=expected_titles,
        expected_classes=expected_classes,
    )
    if not payload:
        return {
            "status": "missing_window",
            "confidence": "low",
            "score": 0,
            "strictBindingRequired": strict_binding_required,
            "requiresUpdateRequest": True,
            "matches": {
                "title": False,
                "className": False,
                "processName": False,
                "handle": False,
            },
            "expected": {
                "titles": list(expected_titles or []),
                "classes": list(expected_classes or []),
                "processNames": list(expected_process_names or []),
                "preferredHandle": preferred_handle,
            },
            "pageIdentity": infer_window_page_identity(
                payload,
                app_id=app_id,
                expected_titles=expected_titles,
                platform=platform,
            ).get("pageIdentity"),
            "reasons": ["未获得窗口上下文，无法建立绑定。"],
        }
    title = normalize_window_text(payload.get("title"))
    class_name = normalize_window_text(payload.get("className"))
    process_name = normalize_window_text(payload.get("processName"))
    handle = payload.get("handle")
    title_match = not expected_title_tokens or any(
        token and (token == title or token in title) for token in expected_title_tokens
    )
    class_match = not expected_class_tokens or any(
        token and token == class_name for token in expected_class_tokens
    )
    process_match = not expected_process_tokens or any(
        token and token == process_name for token in expected_process_tokens
    )
    handle_match = preferred_handle in (None, "", 0) or (
        handle not in (None, "", 0) and int(handle) == int(preferred_handle)
    )
    score = score_window_candidate(
        payload,
        expected_titles=expected_titles,
        expected_classes=expected_classes,
        expected_process_names=expected_process_names,
        preferred_handle=preferred_handle,
        platform=platform,
    )
    page_identity = infer_window_page_identity(
        payload,
        app_id=app_id,
        expected_titles=expected_titles,
        platform=platform,
    ).get("pageIdentity")
    reasons: List[str] = []
    if is_shell_surface_window(payload, platform=platform):
        reasons.append("当前窗口属于桌面壳层/任务栏。")
        status = "shell_surface"
        confidence = "low"
        requires_update_request = True
    else:
        structural_match = (bool(expected_title_tokens) and title_match) or (bool(expected_class_tokens) and class_match)
        if strict_binding_required and not structural_match:
            reasons.append("严格窗口绑定要求下，标题/类名均未命中。")
            status = "unresolved"
            confidence = "low"
            requires_update_request = True
        elif title_match and class_match and process_match and handle_match:
            reasons.append("窗口标题/类名/进程/句柄绑定均通过。")
            status = "verified"
            confidence = "high"
            requires_update_request = False
        elif title_match or class_match or process_match or handle_match:
            reasons.append("已命中部分窗口绑定信号，但仍缺少完整证据。")
            status = "partial"
            confidence = "medium"
            requires_update_request = strict_binding_required and not structural_match
        else:
            reasons.append("未命中任何有效窗口绑定信号。")
            status = "unresolved"
            confidence = "low"
            requires_update_request = True
    if is_probable_dialog_window(payload, platform=platform):
        reasons.append("当前窗口疑似系统/应用对话框。")
    return {
        "status": status,
        "confidence": confidence,
        "score": score,
        "strictBindingRequired": strict_binding_required,
        "requiresUpdateRequest": requires_update_request,
        "matches": {
            "title": bool(title_match),
            "className": bool(class_match),
            "processName": bool(process_match),
            "handle": bool(handle_match),
        },
        "expected": {
            "titles": list(expected_titles or []),
            "classes": list(expected_classes or []),
            "processNames": list(expected_process_names or []),
            "preferredHandle": preferred_handle,
        },
        "pageIdentity": page_identity,
        "reasons": reasons,
    }
