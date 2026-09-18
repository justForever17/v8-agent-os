from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS = 60000
MAX_TOOL_OUTPUT_LENGTH = DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS
DEFAULT_CONTEXT_WINDOW_TOKENS = 32000
DEFAULT_OUTPUT_RESERVE_TOKENS = 2048
CONTEXT_SAFETY_BUFFER_RATIO = 0.2
CHARS_PER_TOKEN_ESTIMATE = 4
MIN_TOOL_OUTPUT_BUDGET_CHARS = 1200
MAX_RESEARCH_DELIVERY_SURFACE_CHARS = 24000


@dataclass(slots=True)
class ToolSurfaceEnvelope:
    """Agent-visible summary contract for tool output surfaces."""

    runId: str | None = None
    tool: str = ""
    toolCallId: str | None = None
    runtimeKind: str = "native"
    summary: str = ""
    refs: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    omitted: dict[str, Any] = field(default_factory=dict)
    nextAction: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value not in (None, "", {}, [])}

TOOL_OUTPUT_TARGET_CHARS = {
    "default": 6000,
    "catalog": 4000,
    "diagnostic": 10000,
    "operation": 2500,
    "research": 24000,
    "web": 16000,
    "skill_instructions": 24000,
}

JSON_PRIORITY_KEYS = (
    "ok",
    "kind",
    "status",
    "summary",
    "runId",
    "traceId",
    "toolCallId",
    "rawRef",
    "summaryRef",
    "researchAnswerPack",
    "recommendedNextAction",
    "selectedPlaybook",
    "selectedPlaybookExecutor",
    "factResolution",
    "laneDecision",
    "candidateAttempts",
    "shortSequenceVerification",
    "verification",
    "artifactIds",
    "artifacts",
    "jobId",
    "providerTaskId",
    "operationKind",
    "modality",
    "providerId",
    "model",
    "modelId",
    "modelRef",
    "qualityStatus",
    "qualityJobId",
    "qualityJobIds",
    "retryReason",
    "fallbackAttempts",
    "policyRejectReason",
    "rawProviderResponseRef",
    "error",
    "exitCode",
    "contentVersion",
    "returnCode",
    "stderr",
    "stderrTail",
    "refs",
    "count",
    "limit",
    "hasMore",
    "cursor",
    "detailTool",
)

COMMAND_TOOL_NAMES = {
    "run_system_command",
    "execute_system_command",
    "command_session_broker",
    "read_background_output",
    "send_background_input",
    "terminate_background_command",
}
TOOL_OBSERVATION_DETAIL_NAME = "tool_observation_detail"



def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
def runtime_kind_for_tool(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if name in {"plugin_broker", "plugin_cli"}:
        return "plugin_manager"
    if name.startswith("creative_media_"):
        return "creative_media"
    if name.startswith("computer_use_"):
        return "computer_use"
    if name.startswith("rpa_"):
        return "rpa"
    if name.startswith("memory_") or name.startswith("mem_"):
        return "memory"
    if name == "web_broker" or name.startswith("web_"):
        return "web"
    if name in {"manage_cron", "manage_hook", "list_processes", "read_audit_log"}:
        return "automation"
    if name == "runtime_broker":
        return "runtime_broker"
    if name == "session_context_broker":
        return "session_context"
    if name in {"session_message_broker", "session_command_broker"}:
        return "session_coordination"
    if name == "delegation_broker" or name.startswith("delegation_") or name.startswith("subagent_"):
        return "subagent_swarm"
    if name == "fetch_skill_instructions":
        return "extensions"
    return "native"
def _text_for_token_estimate(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    content = getattr(value, "content", None)
    if content is not None:
        return str(content)
    if isinstance(value, dict):
        return str(value.get("content") or value)
    return str(value)
def _estimate_tokens_from_chars(text: str) -> int:
    return max(0, int(len(text or "") / CHARS_PER_TOKEN_ESTIMATE))
def _request_messages(request: Any) -> list[Any]:
    state = getattr(request, "state", None)
    if isinstance(state, dict):
        messages = state.get("messages")
        if isinstance(messages, list):
            return messages
    input_payload = getattr(request, "input", None)
    if isinstance(input_payload, dict):
        messages = input_payload.get("messages")
        if isinstance(messages, list):
            return messages
    return []
def _nested_config_value(config: Any, *names: str) -> Any:
    if not isinstance(config, dict):
        return None
    for name in names:
        if config.get(name) not in (None, ""):
            return config.get(name)
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        for name in names:
            if configurable.get(name) not in (None, ""):
                return configurable.get(name)
    metadata = config.get("metadata")
    if isinstance(metadata, dict):
        for name in names:
            if metadata.get(name) not in (None, ""):
                return metadata.get(name)
    return None
def _request_config(request: Any) -> dict[str, Any]:
    # LangGraph ToolCallRequest carries RunnableConfig on its ToolRuntime.
    runtime_config = getattr(getattr(request, "runtime", None), "config", None)
    if isinstance(runtime_config, dict):
        return runtime_config
    config = getattr(request, "config", None)
    return config if isinstance(config, dict) else {}
def _tool_output_kind(tool_name: str) -> str:
    normalized = (tool_name or "").lower()
    if normalized == "session_context_broker":
        return "diagnostic"
    if normalized in {"session_message_broker", "session_command_broker"}:
        return "operation"
    if normalized == "fetch_skill_instructions":
        return "skill_instructions"
    if normalized == "web_broker" or normalized.startswith("web_"):
        return "web"
    if normalized == "research_broker" or normalized.startswith("research_"):
        return "research"
    if "catalog" in normalized or "list_" in normalized or normalized.endswith("_list"):
        return "catalog"
    if "diagnostic" in normalized or "capabilities" in normalized or "observe" in normalized:
        return "diagnostic"
    if any(part in normalized for part in ("delete", "update", "write", "run_", "execute", "manage", "broker")):
        return "operation"
    return "default"
def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default
def _scaled_tool_target_chars(kind: str, base_target: int, context_window_tokens: int) -> int:
    """Let long-context models see more cleaned evidence without expanding mutating tool noise."""
    if kind == "operation":
        return base_target
    if context_window_tokens >= 1_000_000:
        caps = {
            "skill_instructions": 80_000,
            "research": 80_000,
            "web": 60_000,
            "diagnostic": 24_000,
            "default": 16_000,
            "catalog": 8_000,
        }
    elif context_window_tokens >= 200_000:
        caps = {
            "skill_instructions": 48_000,
            "research": 48_000,
            "web": 32_000,
            "diagnostic": 18_000,
            "default": 12_000,
            "catalog": 6_000,
        }
    else:
        caps = {}
    return max(base_target, int(caps.get(kind, base_target)))
def tool_output_budget_for_request(request: Any, tool_name: str) -> dict[str, Any]:
    config = _request_config(request)
    state = getattr(request, "state", None)
    state = state if isinstance(state, dict) else {}
    run_id = _nested_config_value(config, "runId", "run_id", "activeRunId", "active_run_id") or state.get("run_id") or state.get("runId")
    session_id = _nested_config_value(config, "sessionId", "session_id") or state.get("session_id") or state.get("sessionId")
    workspace_path = (
        _nested_config_value(config, "workspacePath", "workspace_path")
        or state.get("workspace_path")
        or state.get("workspacePath")
    )
    context_window_tokens = _safe_int(
        _nested_config_value(
            config,
            "contextWindowTokens",
            "modelContextWindowTokens",
            "model_context_window_tokens",
            "context_window_tokens",
        ),
        DEFAULT_CONTEXT_WINDOW_TOKENS,
    )
    output_reserve_tokens = _safe_int(
        _nested_config_value(
            config,
            "reservedOutputTokens",
            "maxOutputTokens",
            "max_tokens",
            "output_reserve_tokens",
        ),
        DEFAULT_OUTPUT_RESERVE_TOKENS,
    )
    hard_max_chars = _safe_int(
        _nested_config_value(
            config,
            "toolOutputHardMaxChars",
            "maxToolOutputChars",
            "tool_output_hard_max_chars",
        ),
        DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS,
    )
    messages = _request_messages(request)
    used_tokens = sum(_estimate_tokens_from_chars(_text_for_token_estimate(item)) for item in messages)
    safety_buffer_tokens = int(context_window_tokens * CONTEXT_SAFETY_BUFFER_RATIO)
    remaining_tokens = max(0, context_window_tokens - used_tokens - output_reserve_tokens - safety_buffer_tokens)
    dynamic_budget_chars = max(MIN_TOOL_OUTPUT_BUDGET_CHARS, remaining_tokens * CHARS_PER_TOKEN_ESTIMATE)
    kind = _tool_output_kind(tool_name)
    call = getattr(request, "tool_call", None)
    args = call.get("args") if isinstance(call, dict) else None
    if (tool_name in {"runtime_broker", "delegation_broker"} and isinstance(args, dict)
            and args.get("mode") == "inspect" and (args.get("episode_id") or args.get("delegation_id"))):
        # Inspection carries evidence and exact version/control identities, not
        # an operation acknowledgement. Keep the existing context/hard ceilings.
        kind = "diagnostic"
    base_target_chars = TOOL_OUTPUT_TARGET_CHARS.get(kind, TOOL_OUTPUT_TARGET_CHARS["default"])
    target_chars = _scaled_tool_target_chars(kind, base_target_chars, context_window_tokens)
    if tool_name == TOOL_OBSERVATION_DETAIL_NAME:
        requested_chars = _safe_int(args.get("max_chars"), base_target_chars) if isinstance(args, dict) else base_target_chars
        # Explicit recovery reads can consume more than a summary. The model
        # context reserve, configured hard ceiling and redaction still apply.
        target_chars = max(target_chars, min(requested_chars, DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS))
    agent_visible_budget = max(MIN_TOOL_OUTPUT_BUDGET_CHARS, min(dynamic_budget_chars, target_chars, hard_max_chars))
    payload = {
        "budgetSource": "dynamic_context_budget",
        "runId": run_id,
        "agentVisibleBudget": int(agent_visible_budget),
        "dynamicBudgetChars": int(dynamic_budget_chars),
        "hardMaxChars": int(hard_max_chars),
        "targetChars": int(target_chars),
        "toolOutputKind": kind,
        "contextWindowTokens": int(context_window_tokens),
        "estimatedPromptTokens": int(used_tokens),
        "reservedOutputTokens": int(output_reserve_tokens),
        "safetyBufferTokens": int(safety_buffer_tokens),
        "baseTargetChars": int(base_target_chars),
    }
    if session_id:
        payload["sessionId"] = str(session_id)
    if workspace_path:
        payload["workspacePath"] = str(workspace_path)
    return payload
