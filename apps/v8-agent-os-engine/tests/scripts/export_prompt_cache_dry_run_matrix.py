from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from core.prompt_cache_gateway import prompt_cache_gateway  # noqa: E402
from core.prompt_cache_segments import build_prompt_segments_from_parts  # noqa: E402


PROVIDERS = [
    ("openai", "gpt-5.5", "openai:gpt-5.5"),
    ("anthropic", "claude-sonnet-4.5", "anthropic:claude-sonnet-4.5"),
    ("gemini-api", "gemini-3-pro", "gemini-api:gemini-3-pro"),
    ("deepseek", "deepseek-chat", "deepseek:deepseek-chat"),
    ("dashscope", "qwen-max", "dashscope:qwen-max"),
    ("volcengine-ark", "doubao-seed-2-0-pro", "volcengine-ark:doubao-seed-2-0-pro"),
    ("minimax", "minimax-m1", "minimax:minimax-m1"),
    ("zhipu", "glm-5", "zhipu:glm-5"),
    ("moonshot", "kimi-k2.6", "moonshot:kimi-k2.6"),
    ("xai", "grok-4", "xai:grok-4"),
    ("mistral", "mistral-large-latest", "mistral:mistral-large-latest"),
    ("openrouter", "openai/gpt-5.5", "openrouter:openai/gpt-5.5"),
]


STATIC_SYSTEM = """V8 Agent OS supervisor base prompt.
<capability_registry>
kind=chat | ChatRuntime
kind=creative_media | Creative Media Runtime
</capability_registry>
<direct_tool_registry>
runtime_broker, read_native_file, write_native_file
</direct_tool_registry>
<workspace_rules hash="demo-agents-hash">
Keep edits scoped and verify with tests.
</workspace_rules>
"""


def structured_system_message(dynamic_text: str = "") -> SystemMessage:
    parts = [
        {"source": "v8_agent_os.base_prompt", "type": "stable_static", "text": "V8 Agent OS supervisor base prompt.\n"},
        {
            "source": "capability_registry.descriptors",
            "type": "scoped_static",
            "text": "<capability_registry>\nkind=chat | ChatRuntime\nkind=creative_media | Creative Media Runtime\n</capability_registry>\n",
        },
        {
            "source": "direct_tool_registry",
            "type": "scoped_static",
            "text": "<direct_tool_registry>\nruntime_broker, read_native_file, write_native_file\n</direct_tool_registry>\n",
        },
        {
            "source": "workspace.agents_rules",
            "type": "scoped_static",
            "text": "<workspace_rules hash=\"demo-agents-hash\">\nKeep edits scoped and verify with tests.\n</workspace_rules>\n",
        },
        {"source": "scenario.dynamic_context", "type": "dynamic", "text": dynamic_text},
    ]
    return SystemMessage(
        content="".join(part["text"] for part in parts),
        additional_kwargs={"v8_prompt_segments": build_prompt_segments_from_parts(parts)},
    )


SCENARIOS: dict[str, dict[str, Any]] = {
    "daily_chat": {
        "role": "supervisor",
        "messages": [
            structured_system_message("\n<current_time>2026-04-27T10:00:00+08:00</current_time>"),
            HumanMessage(content="帮我总结今天要做的三件事。"),
        ],
        "kwargs": {"temperature": 0},
        "altUser": "帮我总结明天要做的三件事。",
    },
    "project_coding": {
        "role": "supervisor",
        "messages": [
            structured_system_message("\n<runtime_state>{\"phase\":\"inspect\"}</runtime_state>"),
            HumanMessage(content="修复项目里的一个类型错误。"),
        ],
        "kwargs": {"temperature": 0},
        "altUser": "修复项目里的一个导入错误。",
    },
    "network_api": {
        "role": "network_supervisor",
        "messages": [
            structured_system_message("\n<route_context>{\"runtime\":\"network\"}</route_context>"),
            HumanMessage(content="查询一个公开 API 的状态并返回摘要。"),
        ],
        "kwargs": {"temperature": 0},
        "altUser": "查询另一个公开 API 的状态并返回摘要。",
    },
    "creative_media_after_grant": {
        "role": "supervisor",
        "messages": [
            structured_system_message("\n<runtime_tool_grants>[\"creative_media.core\"]</runtime_tool_grants>"),
            HumanMessage(content="生成一张海报 recipe，并登记为资产。"),
        ],
        "kwargs": {"temperature": 0},
        "altUser": "生成一个短视频 recipe，并登记为资产。",
    },
    "memory_extraction": {
        "role": "memory_extraction",
        "messages": [
            structured_system_message("\n<memory>session transcript summary hash only</memory>"),
            HumanMessage(content="从会话中提取稳定偏好。"),
        ],
        "kwargs": {"temperature": 0},
        "altUser": "从会话中提取稳定项目事实。",
    },
    "extensions_prefilter": {
        "role": "extensions_prefilter",
        "messages": [
            structured_system_message("\n<dynamic_context>route candidates changed</dynamic_context>"),
            HumanMessage(content="为任务筛选可用 skill。"),
        ],
        "kwargs": {"temperature": 0},
        "altUser": "为任务筛选可用 MCP server。",
    },
    "subagent_delegated": {
        "role": "subagent",
        "messages": [
            structured_system_message("\n<artifact_awareness>{\"count\":2}</artifact_awareness>"),
            HumanMessage(content="根据 task brief 完成只读分析。"),
        ],
        "kwargs": {"temperature": 0},
        "boundTools": [
            SimpleNamespace(name="read_native_file", description="Read a workspace file.", args_schema={"path": "string"})
        ],
        "altUser": "根据 task brief 完成测试分析。",
    },
}


def _with_alt_user(messages: list[Any], replacement: str) -> list[Any]:
    updated = list(messages)
    for index in range(len(updated) - 1, -1, -1):
        if isinstance(updated[index], HumanMessage):
            updated[index] = HumanMessage(content=replacement)
            break
    return updated


def build_matrix() -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    for scenario_name, scenario in SCENARIOS.items():
        for provider_id, model_id, model_ref in PROVIDERS:
            base = prompt_cache_gateway.dry_run(
                messages=scenario["messages"],
                provider_id=provider_id,
                model_id=model_id,
                model_ref=model_ref,
                role=scenario["role"],
                kwargs=scenario.get("kwargs") or {},
                bound_tools=scenario.get("boundTools") or [],
            )
            alt = prompt_cache_gateway.dry_run(
                messages=_with_alt_user(scenario["messages"], str(scenario["altUser"])),
                provider_id=provider_id,
                model_id=model_id,
                model_ref=model_ref,
                role=scenario["role"],
                kwargs=scenario.get("kwargs") or {},
                bound_tools=scenario.get("boundTools") or [],
            )
            diagnostics = base["cacheDiagnostics"]
            alt_diagnostics = alt["cacheDiagnostics"]
            cells.append(
                {
                    "scenario": scenario_name,
                    "providerId": provider_id,
                    "modelId": model_id,
                    "modelRef": model_ref,
                    "normalizedMessages": base["normalizedMessages"],
                    "providerRequestPatch": diagnostics.get("providerRequestPatch") or {},
                    "segments": diagnostics.get("segments") or [],
                    "staticPrefixKey": diagnostics.get("staticPrefixKey"),
                    "dynamicRequestHash": diagnostics.get("dynamicRequestHash"),
                    "responseCacheDecision": diagnostics.get("responseCacheDecision"),
                    "skipReason": diagnostics.get("skipReason"),
                    "expectedUsageFields": diagnostics.get("usageFields") or [],
                    "samePrefixAcrossAltUserQuery": diagnostics.get("staticPrefixKey") == alt_diagnostics.get("staticPrefixKey"),
                    "altDynamicHashChanged": diagnostics.get("dynamicRequestHash") != alt_diagnostics.get("dynamicRequestHash"),
                }
            )
    return {
        "version": 1,
        "description": "Prompt cache dry-run matrix. No raw prompt content is exported.",
        "providers": [item[0] for item in PROVIDERS],
        "scenarios": list(SCENARIOS.keys()),
        "cells": cells,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export V8 prompt-cache dry-run matrix.")
    parser.add_argument("--output", help="Optional output JSON path. Defaults to stdout.")
    args = parser.parse_args()
    payload = build_matrix()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(str(output))
        return
    print(text)


if __name__ == "__main__":
    main()
