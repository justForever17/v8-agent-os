"""Capture synthetic role inputs at the native adapter boundary; no provider call."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def capture_agent_contract(agent, *, provider_standard="openai", tool_policy=None, persona_override=None):
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from core.llm_chat_adapter import V8ChatModelAdapter
    from core.runtime_tool_access import filter_visible_tools_for_actor
    from core.tools.native.creative_media_facade import (
        creative_media_assets, creative_media_capabilities, creative_media_edit,
        creative_media_jobs, creative_media_plan, creative_media_quality,
    )
    from core.tools.native.workspace_file import read_native_file, write_native_file
    from graph.agent_factories import _apply_task_tool_policy, _build_agent_system_bundle, _format_delegated_task_contract

    creative = agent.capabilitySnapshot.get("specialistFamily") == "creative_media"
    access = ["creative_media.core"] if creative else []
    pool = [read_native_file, write_native_file]
    if creative:
        pool += [creative_media_assets, creative_media_capabilities, creative_media_edit,
                 creative_media_jobs, creative_media_plan, creative_media_quality]
    brief = {"taskBriefId": "synthetic-prompt-audit", "goal": "Verify the assigned reference and report the result.",
             "readOnly": True, "writeSet": [], "allowChildDelegation": False,
             "runtimeAccess": access, "toolPolicy": tool_policy or {"mode": "default"},
             "acceptanceContract": ["Preserve source identity; disclose any missing observation."]}
    visible = _apply_task_tool_policy(filter_visible_tools_for_actor(
        pool, actor="subagent", runtime_access=access, route_context={"taskBrief": brief}), brief)
    bundle = _build_agent_system_bundle(
        agent_name=agent.name,
        agent_system_prompt=agent.system_prompt if persona_override is None else persona_override,
        env_context="<environment>\nOS: Fixture\nUser-Visible Language: zh-CN\nActive Workspace Root: /fixture/workspace\n</environment>\n",
        delegated_plan_context=_format_delegated_task_contract(brief),
        available_tool_names=[tool.name for tool in visible],
    )

    class CaptureNative:
        def __init__(self):
            self.messages = []
            self.tools = []

        def bind_tools(self, tools, **_kwargs):
            self.tools = list(tools)
            return self

        def invoke(self, messages, **_kwargs):
            self.messages = list(messages)
            return AIMessage(content="Synthetic adapter capture only; no task execution.")

    native = CaptureNative()
    adapter = V8ChatModelAdapter(model_id="fixture-model", provider_standard=provider_standard,
        role=f"agent:{agent.id}", meta={"api_standard": provider_standard,
            "effective_capability_matrix": {"supports_native_tools": True}}, model_kwargs={}, builder=lambda: native)
    adapter.bind_tools(visible).invoke([
        SystemMessage(content=str(bundle["content"]), additional_kwargs={"v8_prompt_segments": bundle["segments"]}),
        HumanMessage(content="Inspect the supplied synthetic reference; do not write files."),
    ])
    system = "\n".join(str(message.content) for message in native.messages if isinstance(message, SystemMessage))
    return {"agentId": agent.id, "providerStandard": provider_standard, "evidenceLevel": "synthetic_native_adapter_capture",
            "systemPrompt": system, "systemSha256": hashlib.sha256(system.encode()).hexdigest(),
            "segments": bundle["segments"], "taskBrief": brief,
            "tools": [convert_to_openai_tool(tool) for tool in native.tools],
            "boundToolNames": [tool.name for tool in visible]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    isolated = output / "isolated-home"
    if isolated.exists():
        parser.error("Use a fresh output directory; isolated-home already exists")
    isolated.mkdir()
    os.environ["V8_AGENT_OS_HOME"] = str(isolated)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.agents import parse_agent_md
    from core.storage import StorageManager

    manager = StorageManager.__new__(StorageManager)
    manager.base_dir = isolated
    manager._ensure_default_subagents()
    captures = []
    for file in sorted((isolated / "agents").glob("*.md")):
        agent = parse_agent_md(file.read_text(encoding="utf-8"), file.name)
        for standard in ("openai", "anthropic"):
            captures.append(capture_agent_contract(agent, provider_standard=standard))
    target = output / "agent-provider-inputs.json"
    target.write_text(json.dumps({"realProviderVerified": False, "captures": captures}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(target), "captures": len(captures), "realProviderVerified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
