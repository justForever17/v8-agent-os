from __future__ import annotations

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


def summarize_post_action_visual_check(
    *,
    provider_id: str,
    locator: str,
    resolved: Dict[str, Any] | None,
    expected_texts: Iterable[str] | None = None,
    error: str | None = None,
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
    }
