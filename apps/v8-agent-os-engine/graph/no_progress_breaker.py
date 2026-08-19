from __future__ import annotations

import hashlib
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


_ASYNC_PROGRESS_MARKERS = (
    "running",
    "processing",
    "submitted",
    "queued",
    "pending",
    "in progress",
    "still generating",
    "not ready",
    "运行中",
    "处理中",
    "已提交",
    "排队",
    "等待中",
    "生成中",
    "仍在生成",
    "尚未完成",
    "稍后重试",
    "轮询",
)

MIN_REMAINING_STEPS_FOR_TOOL_ROUND = 6


def _stable_json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps(str(value), ensure_ascii=False, sort_keys=True)


def _normalize_tool_args(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _normalize_tool_args(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_tool_args(item) for item in value]
    return str(value)


def _tool_target_identity(tool_call: dict) -> str | None:
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return None
    target_candidates = []
    for key in (
        "target",
        "target_id",
        "path",
        "file_path",
        "url",
        "selector",
        "window",
        "window_title",
        "app",
        "application",
        "resource_id",
        "channel_id",
        "chat_id",
        "query",
    ):
        value = args.get(key)
        if value not in (None, "", [], {}):
            target_candidates.append(f"{key}={value}")
    if not target_candidates:
        return None
    return " | ".join(target_candidates[:3])


def _tool_observation_fingerprint(tool_messages: list[ToolMessage]) -> str | None:
    if not tool_messages:
        return None
    payload = []
    for message in tool_messages:
        payload.append(
            {
                "name": getattr(message, "name", None),
                "content": str(message.content)[:400],
            }
        )
    return hashlib.md5(_stable_json(payload).encode("utf-8")).hexdigest()


def _tool_observation_preview(tool_messages: list[ToolMessage]) -> str:
    previews: list[str] = []
    for message in tool_messages:
        previews.append(str(message.content or "")[:600])
    return "\n".join(previews)


def _looks_async_in_progress(observation_preview: str) -> bool:
    normalized = " ".join(str(observation_preview or "").strip().lower().split())
    if not normalized:
        return False
    return any(marker in normalized for marker in _ASYNC_PROGRESS_MARKERS)


def _build_tool_cycle(ai_message: AIMessage, tool_messages: list[ToolMessage]) -> dict | None:
    tool_calls = list(getattr(ai_message, "tool_calls", None) or [])
    if not tool_calls:
        return None
    normalized_calls = []
    tool_names = []
    target_identities = []
    for tool_call in tool_calls:
        tool_names.append(str(tool_call.get("name") or "unknown"))
        target_identity = _tool_target_identity(tool_call)
        if target_identity:
            target_identities.append(target_identity)
        normalized_calls.append(
            {
                "name": tool_call.get("name"),
                "args": _normalize_tool_args(tool_call.get("args")),
                "target_identity": target_identity,
            }
        )
    signature = _stable_json(normalized_calls)
    return {
        "signature": signature,
        "tool_names": tool_names or ["unknown"],
        "target_identities": target_identities,
        "observation_fingerprint": _tool_observation_fingerprint(tool_messages),
        "observation_preview": _tool_observation_preview(tool_messages),
    }


def _collect_completed_tool_cycles(messages) -> list[dict]:
    filtered = [message for message in messages if not isinstance(message, SystemMessage)]
    cycles: list[dict] = []
    index = 0
    while index < len(filtered):
        message = filtered[index]
        if isinstance(message, HumanMessage):
            cycles.clear()
            index += 1
            continue
        if not isinstance(message, AIMessage) or not getattr(message, "tool_calls", None):
            index += 1
            continue
        tool_call_ids = {str(item.get("id") or "") for item in list(message.tool_calls or []) if item.get("id")}
        tool_messages: list[ToolMessage] = []
        cursor = index + 1
        while cursor < len(filtered):
            candidate = filtered[cursor]
            if isinstance(candidate, HumanMessage):
                break
            if isinstance(candidate, AIMessage):
                break
            if isinstance(candidate, ToolMessage) and (
                not tool_call_ids or candidate.tool_call_id in tool_call_ids
            ):
                tool_messages.append(candidate)
            cursor += 1
        cycle = _build_tool_cycle(message, tool_messages)
        if cycle and tool_messages:
            cycles.append(cycle)
        index = cursor
    return cycles


def apply_no_progress_breaker(messages, response) -> tuple[AIMessage, dict | None]:
    if not getattr(response, "tool_calls", None):
        return response, None

    completed_cycles = _collect_completed_tool_cycles(messages)
    if len(completed_cycles) < 2:
        return response, None

    pending_cycle = _build_tool_cycle(response, [])
    if pending_cycle is None:
        return response, None

    last_cycle = completed_cycles[-1]
    previous_cycle = completed_cycles[-2]
    if last_cycle["signature"] != pending_cycle["signature"]:
        return response, None
    if previous_cycle["signature"] != pending_cycle["signature"]:
        return response, None

    observation_fingerprint = str(last_cycle.get("observation_fingerprint") or "").strip()
    if (
        not observation_fingerprint
        or observation_fingerprint != str(previous_cycle.get("observation_fingerprint") or "").strip()
    ):
        return response, None

    trailing_count = 2
    for cycle in reversed(completed_cycles[:-2]):
        if cycle["signature"] != pending_cycle["signature"]:
            break
        if str(cycle.get("observation_fingerprint") or "").strip() != observation_fingerprint:
            break
        trailing_count += 1
    repeat_count = trailing_count + 1
    if repeat_count < 3:
        return response, None

    observation_preview = str(last_cycle.get("observation_preview") or "").strip()
    if _looks_async_in_progress(observation_preview):
        return response, None

    tool_names = list(dict.fromkeys(last_cycle.get("tool_names") or pending_cycle.get("tool_names") or ["unknown"]))
    target_identities = last_cycle.get("target_identities") or pending_cycle.get("target_identities") or []
    blocker_lines = [
        "我检测到自己正在重复调用同一组工具，而且最近两轮观察结果没有变化。",
        f"为避免继续空转，这一轮停止再次调用工具。重复工具: {', '.join(tool_names)}。",
    ]
    if target_identities:
        blocker_lines.append(f"重复目标: {'; '.join(target_identities[:2])}。")
    blocker_lines.append("当前更合理的下一步应该是总结已知状态、说明阻塞点，或等待新的输入/审批。若外部任务仍在处理中，应改用 wait(seconds, note) 后再轮询。")
    loop_breaker_response = AIMessage(
        content="\n".join(blocker_lines),
        additional_kwargs={
            **dict(getattr(response, "additional_kwargs", {}) or {}),
            "loop_breaker": {
                "reason": "repeated_tool_cycle_without_progress",
                "repeat_count": repeat_count,
                "tool_names": tool_names,
                "target_identities": target_identities[:4],
                "observation_fingerprint": observation_fingerprint,
            },
        },
    )
    return loop_breaker_response, {
        "count": repeat_count,
        "tool_names": tool_names,
        "target_identities": target_identities[:4],
        "observation_fingerprint": observation_fingerprint,
    }


def apply_remaining_steps_guard(response, remaining_steps: int | None) -> tuple[AIMessage, dict | None]:
    tool_calls = list(getattr(response, "tool_calls", None) or [])
    if not tool_calls:
        return response, None
    if remaining_steps is None:
        # RemainingSteps is a framework-managed value. Missing state is
        # unknown, not permission to invent or reset a product budget.
        return response, None
    try:
        remaining = max(0, int(remaining_steps))
    except (TypeError, ValueError):
        return response, None
    if remaining > MIN_REMAINING_STEPS_FOR_TOOL_ROUND:
        return response, None

    tool_names = list(dict.fromkeys(
        str(call.get("name") or "unknown").strip() or "unknown"
        for call in tool_calls
        if isinstance(call, dict)
    ))
    guard = {
        "reason": "insufficient_remaining_steps_for_tool_round",
        "remaining_steps": remaining,
        "minimum_steps": MIN_REMAINING_STEPS_FOR_TOOL_ROUND,
        "suppressed_tool_names": tool_names,
        "checkpoint_preserved": True,
    }
    return AIMessage(
        content=(
            "当前长任务已接近本段图执行的安全边界。系统已保留 checkpoint，"
            "并停止开启新的工具回合，以免重复调用和重复计费。"
            "请从当前已完成状态继续，或把剩余工作交给持久化的 Engineering/delegation Runtime。"
        ),
        additional_kwargs={
            **dict(getattr(response, "additional_kwargs", {}) or {}),
            "execution_progress_guard": guard,
        },
    ), guard
