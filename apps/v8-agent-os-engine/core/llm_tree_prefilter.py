from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from core.background_model_output import sanitize_background_model_output
from core.background_model_timeout import (
    BackgroundModelTimeoutError,
    invoke_background_model_with_timeout,
    run_cancellable_background_call,
)
from core.llm_factory import llm_factory

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
_PREFILTER_CACHE_TTL_SECONDS = 300.0
_PREFILTER_TIMEOUT_SECONDS = 1.0
_PREFILTER_CACHE_LOCK = threading.Lock()
_PREFILTER_CACHE: dict[str, tuple[float, list[str], dict[str, Any]]] = {}
_PREFILTER_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="v8-extensions-prefilter")


def _response_text(value: Any) -> str:
    return sanitize_background_model_output(value).text.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    normalized = str(text or "").strip()
    if not normalized:
        return {}
    candidates = [normalized]
    fenced = normalized.replace("```json", "```")
    if "```" in fenced:
        segments = [segment.strip() for segment in fenced.split("```") if segment.strip()]
        candidates.extend(segments)
    match = _JSON_BLOCK_RE.search(normalized)
    if match:
        candidates.append(match.group(0).strip())
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _prefilter_cache_key(
    *,
    role: str,
    user_query: str,
    family_label: str,
    max_families: int,
    families: list[dict[str, Any]],
) -> str:
    payload = json.dumps(
        {
            "role": str(role or "").strip(),
            "userQuery": str(user_query or "").strip(),
            "familyLabel": str(family_label or "").strip(),
            "maxFamilies": int(max_families or 0),
            "families": families,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _read_prefilter_cache(cache_key: str) -> tuple[list[str], dict[str, Any]] | None:
    with _PREFILTER_CACHE_LOCK:
        entry = _PREFILTER_CACHE.get(cache_key)
        if not entry:
            return None
        cached_at, selected_keys, state = entry
        if cached_at <= 0 or (time.monotonic() - cached_at) > _PREFILTER_CACHE_TTL_SECONDS:
            _PREFILTER_CACHE.pop(cache_key, None)
            return None
        return list(selected_keys), dict(state)


def _write_prefilter_cache(cache_key: str, *, selected_keys: list[str], state: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    normalized_keys = [str(item).strip() for item in list(selected_keys or []) if str(item).strip()]
    normalized_state = dict(state or {})
    with _PREFILTER_CACHE_LOCK:
        _PREFILTER_CACHE[cache_key] = (time.monotonic(), list(normalized_keys), dict(normalized_state))
    return list(normalized_keys), dict(normalized_state)


def _invoke_prefilter_model(
    *,
    role: str,
    prompt_payload: str,
    timeout_seconds: float,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    model = llm_factory.create_for_role(
        role,
        streaming=False,
        temperature=0,
        timeout=timeout_seconds,
    )
    response = invoke_background_model_with_timeout(
        model,
        [
            SystemMessage(
                content=(
                    "你是 V8 Agent OS 的扩展候选预筛器。\n"
                    "任务：根据用户查询，从候选 Skills / MCP servers / 工具树中选出最相关的 key。\n"
                    "要求：\n"
                    "1. 只做候选单位级预筛，不做叶子工具改写。\n"
                    "2. MCP 候选以 server 为单位；选中 server 代表暴露完整工具树。\n"
                    "3. 只能返回 JSON，不要输出解释性文本。\n"
                    "4. JSON 结构必须为 {\"selected\": [\"key1\", ...], \"reason\": \"...\"}。\n"
                    "5. selected 中的 key 必须来自输入 families，且数量不能超过 maxFamilies。"
                )
            ),
            HumanMessage(content=prompt_payload),
        ],
        timeout_seconds=timeout_seconds,
        config={"callbacks": []},
    )
    sanitized = sanitize_background_model_output(response)
    raw_response = sanitized.text
    return raw_response, _extract_json_object(raw_response), sanitized.diagnostics()


def select_family_keys_with_llm(
    *,
    role: str,
    user_query: str,
    family_label: str,
    families: list[dict[str, Any]],
    max_families: int,
    timeout_seconds: float | None = None,
) -> tuple[list[str], dict[str, Any]]:
    normalized_families: list[dict[str, Any]] = []
    for item in list(families or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        family: dict[str, Any] = {"key": key}
        title = str(item.get("title") or item.get("name") or item.get("serverName") or "").strip()
        description = str(item.get("description") or "").strip()
        member_count = int(item.get("memberCount") or 0)
        examples = [
            str(example).strip()
            for example in list(item.get("examples") or [])
            if str(example).strip()
        ][:8]
        if title:
            family["title"] = title
        if description:
            family["description"] = description
        if member_count > 0:
            family["memberCount"] = member_count
        if examples:
            family["examples"] = examples
        name = str(item.get("name") or "").strip()
        if name:
            family["name"] = name
        server_name = str(item.get("serverName") or "").strip()
        if server_name:
            family["serverName"] = server_name
        tools: list[dict[str, str]] = []
        for raw_tool in list(item.get("tools") or []):
            if isinstance(raw_tool, dict):
                tool_name = str(raw_tool.get("name") or "").strip()
                tool_description = str(raw_tool.get("description") or "").strip()
            else:
                tool_name = str(raw_tool or "").strip()
                tool_description = ""
            if tool_name or tool_description:
                tools.append({"name": tool_name, "description": tool_description})
        if tools:
            family["tools"] = tools
            if not family.get("memberCount"):
                family["memberCount"] = len(tools)
        normalized_families.append(family)
    if not normalized_families or max_families <= 0:
        return [], {
            "mode": "lexical",
            "reason": "候选家族为空。",
            "rawResponse": "",
        }
    if len(normalized_families) <= max_families:
        return [item["key"] for item in normalized_families], {
            "mode": "lexical",
            "reason": "候选数量不足，无需额外预筛。",
            "rawResponse": "",
            "timedOut": False,
            "cacheHit": False,
            "bypassed": True,
        }

    prompt_payload = json.dumps(
        {
            "query": str(user_query or "").strip(),
            "familyLabel": family_label,
            "maxFamilies": max_families,
            "families": normalized_families,
        },
        ensure_ascii=False,
    )
    cache_key = _prefilter_cache_key(
        role=role,
        user_query=user_query,
        family_label=family_label,
        max_families=max_families,
        families=normalized_families,
    )
    cached = _read_prefilter_cache(cache_key)
    if cached is not None:
        cached_keys, cached_state = cached
        cached_state["cacheHit"] = True
        cached_state["timedOut"] = bool(cached_state.get("timedOut", False))
        return cached_keys, cached_state

    started_at = time.perf_counter()
    timeout_budget = max(float(timeout_seconds or _PREFILTER_TIMEOUT_SECONDS), 0.05)
    try:
        raw_response, payload, sanitizer_diagnostics = run_cancellable_background_call(
            _PREFILTER_EXECUTOR,
            lambda: _invoke_prefilter_model(
                role=role,
                prompt_payload=prompt_payload,
                timeout_seconds=timeout_budget,
            ),
            timeout_seconds=timeout_budget,
        )
    except BackgroundModelTimeoutError as exc:
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
        return _write_prefilter_cache(
            cache_key,
            selected_keys=[],
            state={
                "mode": "fallback",
                "reason": "timeout",
                "rawResponse": "",
                "timedOut": True,
                "cacheHit": False,
                "durationMs": duration_ms,
                **exc.diagnostics(),
            },
        )
    except Exception as exc:
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
        return _write_prefilter_cache(
            cache_key,
            selected_keys=[],
            state={
                "mode": "fallback",
                "reason": str(exc).strip() or exc.__class__.__name__,
                "rawResponse": "",
                "timedOut": False,
                "cacheHit": False,
                "durationMs": duration_ms,
            },
        )

    duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
    selected_keys: list[str] = []
    seen: set[str] = set()
    allowed = {item["key"] for item in normalized_families}
    selected_field_present = any(key in payload for key in ("selected", "keys", "families"))
    raw_selected = payload.get("selected")
    if raw_selected is None:
        raw_selected = payload.get("keys")
    if raw_selected is None:
        raw_selected = payload.get("families")
    if not isinstance(raw_selected, list):
        raw_selected = []
    for key in raw_selected:
        normalized_key = str(key or "").strip()
        if not normalized_key or normalized_key not in allowed or normalized_key in seen:
            continue
        seen.add(normalized_key)
        selected_keys.append(normalized_key)
        if len(selected_keys) >= max_families:
            break
    mode = "llm_tree" if selected_field_present else "fallback"
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        reason = "LLM 明确返回空候选。" if selected_field_present and not selected_keys else ("LLM 未返回可用家族。" if not selected_keys else "")
    return _write_prefilter_cache(
        cache_key,
        selected_keys=selected_keys,
        state={
            "mode": mode,
            "reason": reason,
            "rawResponse": raw_response,
            "backgroundOutput": sanitizer_diagnostics,
            "emptySelection": bool(selected_field_present and not selected_keys),
            "timedOut": False,
            "cacheHit": False,
            "durationMs": duration_ms,
        },
    )
