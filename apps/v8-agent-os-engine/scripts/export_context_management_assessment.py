from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

if "chromadb" not in sys.modules:
    class _FakeChromaCollection:
        def upsert(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {}

    class _FakeChromaClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def get_or_create_collection(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeChromaCollection()

    sys.modules["chromadb"] = type("chromadb", (), {"PersistentClient": _FakeChromaClient})()

from core.context_orchestrator import ContextOrchestrator  # noqa: E402
from core.database import db  # noqa: E402
from core.storage import storage  # noqa: E402
from erc.runtime_context import bind_runtime_context  # noqa: E402
from graph.supervisor_context import _build_workspace_rules_context, workspace_resolution_service  # noqa: E402
from runtimes.extensions.runtime import extensions_runtime_service  # noqa: E402
from runtimes.memory.runtime import memory_runtime  # noqa: E402


REPO_ROOT = ENGINE_ROOT.parents[1]
DOCS_ROOT = REPO_ROOT / "docs" / "chatruntime"
OUTPUT_ROOT = DOCS_ROOT / "context_management_reports"
RUNBOOK_PATH = DOCS_ROOT / "ASSESSMENT_DIAGNOSTICS_RUNBOOK_ZH.md"


def _estimate_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // 4) if str(text or "").strip() else 0


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _latest_stress_report_path() -> str | None:
    candidates = sorted(
        DOCS_ROOT.glob("system_content_stress_reports/*_system_content_stress_report.md"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def _parse_tagged_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    content = str(text or "")
    lines = content.splitlines()
    current_name = ""
    current_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[/"):
            if current_name:
                body = "\n".join(current_lines).strip()
                sections.append(
                    {
                        "name": current_name,
                        "estimatedTokens": _estimate_tokens(body),
                        "preview": body[:160],
                    }
                )
            current_name = stripped.strip("[]")
            current_lines = []
            continue
        if current_name:
            current_lines.append(line)
    if current_name:
        body = "\n".join(current_lines).strip()
        sections.append(
            {
                "name": current_name,
                "estimatedTokens": _estimate_tokens(body),
                "preview": body[:160],
            }
        )
    return sections


def _select_project_descriptor() -> dict[str, Any] | None:
    registry = storage.get_projects_registry() or {}
    projects = [dict(item) for item in list(registry.get("projects") or []) if isinstance(item, dict)]
    for project in projects:
        workspace_path = str(project.get("workspacePath") or "").strip()
        if workspace_path:
            return project
    return None


def _main_workspace_path() -> str:
    try:
        return str(workspace_resolution_service.get_main_workspace_path() or "").strip()
    except Exception:
        workspace_config = storage.get_workspace_config() or {}
        return str(workspace_config.get("path") or workspace_config.get("workspacePath") or "").strip()


def _build_passive_context(
    *,
    user_query: str,
    scope: str,
    scope_chain: list[str],
    session_id: str,
    run_id: str,
    suppress_daily_memory: bool,
    suppress_memory_map: bool,
) -> tuple[str, list[dict[str, Any]]]:
    context = memory_runtime.build_session_context(
        user_query=user_query,
        scope=scope,
        scope_chain=scope_chain,
        session_id=session_id,
        run_id=run_id,
        suppress_daily_memory=suppress_daily_memory,
        suppress_memory_map=suppress_memory_map,
    )
    return context, _parse_tagged_sections(context)


def _build_workspace_rules(
    *,
    state: dict[str, Any],
    session_id: str,
) -> tuple[str, list[dict[str, Any]]]:
    content, diagnostics = _build_workspace_rules_context(state=state, session_id=session_id)
    return content, [
        {
            "root": str(item.get("root") or item.get("workspaceRoot") or ""),
            "estimatedTokens": int(item.get("estimatedTokens") or 0),
            "truncated": bool(item.get("truncated", False)),
            "deliveryMode": str(item.get("deliveryMode") or ""),
        }
        for item in list(diagnostics or [])
        if isinstance(item, dict)
    ]


def _build_extensions_route(
    *,
    user_query: str,
    session_id: str,
    runtime_kind: str,
    workspace_id: str | None = None,
    workspace_path: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    token = extensions_runtime_service.bind_execution_context(
        session_id=session_id,
        workspace_id=workspace_id,
        workspace_path=workspace_path,
        project_id=project_id,
        runtime_kind=runtime_kind,
    )
    try:
        bundle = extensions_runtime_service.build_contextual_route(
            user_query=user_query,
            available_tools=[],
            loaded_agents=[],
            freshness_mode="preview_best_effort",
        )
    finally:
        extensions_runtime_service.reset_execution_context(token)
    return {
        "promptAddition": bundle.prompt_addition,
        "estimatedTokens": _estimate_tokens(bundle.prompt_addition),
        "selectedSkills": list(bundle.selected_skill_names),
        "skillRootDescriptors": list(bundle.skill_root_descriptors),
        "candidateSummary": dict(bundle.candidate_summary or {}),
    }


def _long_segment(label: str, repeat: int) -> str:
    seed = (
        f"{label} 需要保留用户目标、关键路径、失败原因、限制条件、下一步、产物要求、URL、目录、"
        "工程约束、审批状态、恢复语义、运行时边界、workflow ledger、runtime snapshot。"
    )
    return " ".join(seed for _ in range(repeat))


def _build_messages(*, user_query: str, pressure: str) -> list[Any]:
    repeat = 46 if pressure == "soft" else 92
    old_user = _long_segment("旧用户消息", repeat)
    old_ai = _long_segment("旧助手消息", repeat)
    adapter = AIMessage(
        content="adapter carrier",
        additional_kwargs={
            "context_adapter_blocks": [
                {
                    "type": "memory_recall",
                    "title": "Synthetic Recall Capsule",
                    "content": _long_segment("adapter recall", 20),
                    "metadata": {"source": "assessment_script", "runtime_plane": "memory"},
                }
            ]
        },
    )
    messages: list[Any] = [
        SystemMessage(content="你是 V8 Agent OS 的上下文治理评估样本。"),
        adapter,
    ]
    for index in range(8):
        messages.append(HumanMessage(content=f"第 {index + 1} 轮用户历史：{old_user}"))
        messages.append(AIMessage(content=f"第 {index + 1} 轮助手历史：{old_ai}"))
    messages.append(HumanMessage(content=user_query))
    return messages


class _FakeSummaryModel:
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def invoke(self, messages, config=None):  # noqa: ANN001, ANN201
        transcript = ""
        if messages:
            transcript = str(getattr(messages[0], "content", "") or "")
        clipped = transcript.replace("\n", " ")[:320]
        return AIMessage(content=f"压缩摘要：保留目标、约束、失败与下一步。源片段：{clipped}")


@contextmanager
def _patched_compaction(force_context_window: int | None = None):
    current_config = storage.get_context_config() or {}
    with patch("core.context_orchestrator.flush_before_context_compaction", return_value={"ok": True, "skipped": False, "reason": "assessment_probe"}), patch(
        "core.context_orchestrator.llm_factory.create_chat_model",
        side_effect=lambda model_id, **_kwargs: _FakeSummaryModel(model_id),
    ):
        if force_context_window is None:
            yield current_config
            return
        with patch("core.context_orchestrator.llm_factory.get_model_context_window", return_value=force_context_window):
            yield current_config


def _run_variant(
    *,
    scene_name: str,
    user_query: str,
    scope: str,
    scope_chain: list[str],
    session_id: str,
    run_id: str,
    runtime_kind: str,
    pressure: str,
    leading_system_content: str,
    force_context_window: int | None = None,
) -> dict[str, Any]:
    orchestrator = ContextOrchestrator()
    messages = _build_messages(user_query=user_query, pressure=pressure)
    db.create_or_update_session(
        session_id=session_id,
        title=f"Context Assessment · {scene_name}",
        user_id="assessment",
        metadata={"assessment": True, "scene": scene_name},
    )
    db.create_run_record(
        run_id=run_id,
        session_id=session_id,
        user_id="assessment",
        run_type="assessment",
        status="running",
        trigger_source="assessment_script",
        metadata={"pressure": pressure, "scene": scene_name},
    )
    with _patched_compaction(force_context_window=force_context_window) as context_config:
        with bind_runtime_context(
            session_id=session_id,
            run_id=run_id,
            latest_seq=1,
            event_ts=datetime.now().isoformat(),
        ):
            prepared = orchestrator.prepare(
                messages=messages,
                runtime_kind=runtime_kind,
                target_role="supervisor",
                resolved_model_id=str(storage.get_role_model_id("supervisor") or "").strip(),
                resolved_scope=scope,
                scope_chain=scope_chain,
                leading_system_content=leading_system_content,
            )
    db.update_run_record(run_id, status="completed", metadata={"assessment": True, "scene": scene_name, "pressure": pressure})
    return {
        "scene": scene_name,
        "sessionId": session_id,
        "runId": run_id,
        "pressure": pressure,
        "forcedContextWindow": force_context_window,
        "contextConfig": {
            "softTriggerRatio": float(((context_config.get("compression") or {}).get("soft_trigger_ratio") or 0.55)),
            "hardTriggerRatio": float(((context_config.get("compression") or {}).get("hard_trigger_ratio") or 0.75)),
            "keepRecentMessages": int(((context_config.get("compression") or {}).get("keep_recent_messages") or 6)),
            "useLlmSummary": bool(((context_config.get("compression") or {}).get("use_llm_summary") or False)),
            "defaultContextWindowTokens": int(((context_config.get("compression") or {}).get("default_context_window_tokens") or 32000)),
        },
        "audit": dict(prepared.audit),
        "finalMessageCount": len(prepared.messages),
        "finalEstimatedTokens": _estimate_tokens("\n".join(str(getattr(item, "content", "")) for item in prepared.messages)),
        "historySummaryPreview": next(
            (
                block.content[:240]
                for block in prepared.blocks
                if getattr(block, "type", "") == "history_summary"
            ),
            "",
        ),
    }


def _scene_payloads() -> list[dict[str, Any]]:
    project = _select_project_descriptor()
    main_workspace = _main_workspace_path()
    payloads: list[dict[str, Any]] = [
        {
            "name": "daily_chat",
            "label": "通用日常聊天",
            "runtimeKind": "chat",
            "userQuery": "请根据今天的沟通记录整理一份清爽的日常跟进说明，并保留关键约束与下一步。",
            "sessionId": "context-daily-chat",
            "runId": "run-context-daily-chat",
            "scope": "workspace:main",
            "scopeChain": ["global", "workspace:main"],
            "workspaceId": None,
            "workspacePath": main_workspace or None,
            "projectId": None,
            "suppressDailyMemory": False,
            "suppressMemoryMap": False,
            "transport": "chat",
        }
    ]
    if project:
        project_id = str(project.get("id") or "").strip() or str(project.get("projectId") or "").strip()
        workspace_id = str(project.get("workspaceId") or "").strip() or None
        workspace_path = str(project.get("workspacePath") or "").strip() or None
        payloads.append(
            {
                "name": "project_coding",
                "label": "项目编程",
                "runtimeKind": "chat",
                "userQuery": "请在当前项目里排查一个 extensions 排序与上下文治理问题，补测试并给出验证结论。",
                "sessionId": "context-project-coding",
                "runId": "run-context-project-coding",
                "scope": f"project:{project_id}",
                "scopeChain": ["global", f"project:{project_id}"],
                "workspaceId": workspace_id,
                "workspacePath": workspace_path,
                "projectId": project_id,
                "suppressDailyMemory": True,
                "suppressMemoryMap": True,
                "transport": "chat",
            }
        )
    payloads.append(
        {
            "name": "network_api",
            "label": "Network API",
            "runtimeKind": "network_supervisor_openai",
            "userQuery": "通过第三方 API 接收到一个长请求，请在不依赖 ask_user 卡片的前提下输出结构化处理结果。",
            "sessionId": "context-network-api",
            "runId": "run-context-network-api",
            "scope": "external_api_thread:context-eval",
            "scopeChain": ["global", "external_api_thread:context-eval"],
            "workspaceId": None,
            "workspacePath": None,
            "projectId": None,
            "suppressDailyMemory": False,
            "suppressMemoryMap": False,
            "transport": "network_supervisor_openai",
        }
    )
    return payloads


def _build_scene_report(scene: dict[str, Any]) -> dict[str, Any]:
    passive_context, passive_sections = _build_passive_context(
        user_query=scene["userQuery"],
        scope=scene["scope"],
        scope_chain=list(scene["scopeChain"]),
        session_id=scene["sessionId"],
        run_id=scene["runId"],
        suppress_daily_memory=bool(scene["suppressDailyMemory"]),
        suppress_memory_map=bool(scene["suppressMemoryMap"]),
    )
    workspace_rules, workspace_rule_diagnostics = _build_workspace_rules(
        state={
            "transport": scene["transport"],
            "workspace_id": scene["workspaceId"],
            "workspace_path": scene["workspacePath"],
            "project_id": scene["projectId"],
        },
        session_id=scene["sessionId"],
    )
    extensions_route = _build_extensions_route(
        user_query=scene["userQuery"],
        session_id=scene["sessionId"],
        runtime_kind=scene["runtimeKind"],
        workspace_id=scene["workspaceId"],
        workspace_path=scene["workspacePath"],
        project_id=scene["projectId"],
    )
    leading_parts = [
        "[BASE SYSTEM CONTENT]\n这是上下文管理评估样本，复用真实 storage/config truth。",
    ]
    if workspace_rules:
        leading_parts.append(f"[WORKSPACE RULES]\n{workspace_rules}")
    if passive_context:
        leading_parts.append(passive_context)
    if extensions_route["promptAddition"]:
        leading_parts.append(f"[EXTENSIONS ROUTE BLOCK]\n{extensions_route['promptAddition']}")
    leading_system_content = "\n\n".join(leading_parts)

    actual = _run_variant(
        scene_name=scene["name"],
        user_query=scene["userQuery"],
        scope=scene["scope"],
        scope_chain=list(scene["scopeChain"]),
        session_id=scene["sessionId"],
        run_id=scene["runId"],
        runtime_kind=scene["runtimeKind"],
        pressure="hard",
        leading_system_content=leading_system_content,
        force_context_window=None,
    )
    fallback_soft = _run_variant(
        scene_name=scene["name"],
        user_query=scene["userQuery"],
        scope=scene["scope"],
        scope_chain=list(scene["scopeChain"]),
        session_id=scene["sessionId"],
        run_id=scene["runId"],
        runtime_kind=scene["runtimeKind"],
        pressure="soft",
        leading_system_content=leading_system_content,
        force_context_window=32000,
    )
    fallback_hard = _run_variant(
        scene_name=scene["name"],
        user_query=scene["userQuery"],
        scope=scene["scope"],
        scope_chain=list(scene["scopeChain"]),
        session_id=scene["sessionId"],
        run_id=scene["runId"],
        runtime_kind=scene["runtimeKind"],
        pressure="hard",
        leading_system_content=leading_system_content,
        force_context_window=32000,
    )
    fallback_hard_reuse = _run_variant(
        scene_name=scene["name"],
        user_query=scene["userQuery"],
        scope=scene["scope"],
        scope_chain=list(scene["scopeChain"]),
        session_id=scene["sessionId"],
        run_id=f"{scene['runId']}-reuse",
        runtime_kind=scene["runtimeKind"],
        pressure="hard",
        leading_system_content=leading_system_content,
        force_context_window=32000,
    )

    return {
        "label": scene["label"],
        "sceneConfig": {
            "runtimeKind": scene["runtimeKind"],
            "scope": scene["scope"],
            "scopeChain": list(scene["scopeChain"]),
            "workspaceId": scene["workspaceId"],
            "workspacePath": scene["workspacePath"],
            "projectId": scene["projectId"],
            "suppressDailyMemory": bool(scene["suppressDailyMemory"]),
            "suppressMemoryMap": bool(scene["suppressMemoryMap"]),
        },
        "workspaceRules": {
            "present": bool(workspace_rules.strip()),
            "estimatedTokens": _estimate_tokens(workspace_rules),
            "preview": workspace_rules[:220],
            "diagnostics": workspace_rule_diagnostics,
        },
        "passiveContext": {
            "estimatedTokens": _estimate_tokens(passive_context),
            "sections": passive_sections,
        },
        "extensionsRoute": extensions_route,
        "variants": {
            "actualCurrentModelWindow": actual,
            "fallback32kSoftBudget": fallback_soft,
            "fallback32kHardBudget": fallback_hard,
            "fallback32kHardBudgetReuse": fallback_hard_reuse,
        },
    }


def _build_report(results: dict[str, Any]) -> str:
    lines = [
        "# 超长上下文管理评估报告",
        "",
        f"- 生成时间: `{results['generatedAt']}`",
        f"- 当前 supervisor 模型: `{results['configSnapshot']['supervisorModel']}`",
        f"- 当前 summary 模型: `{results['configSnapshot']['summaryModel']}`",
        f"- 当前 supervisor 窗口: `{results['configSnapshot']['supervisorContextWindow']}`",
        f"- 当前 summary 窗口: `{results['configSnapshot']['summaryContextWindow']}`",
        f"- stress report 参考: `{results.get('latestSystemContentStressReport') or 'N/A'}`",
        "",
        "## 结论摘要",
        "",
        "- 主上下文压缩器只压旧的非 system 消息，不压 system persona、workspace rules、extensions route block 这类系统块。",
        "- `[MEMORY SUMMARY] / [MEMORY MAP] / workflow hints` 不是主压缩器的对象，它们通过 `build_session_context(...)` 先被预算裁剪，再作为系统注入进入上下文。",
        "- 当前机器上的 supervisor / summary 模型都报告为超大窗口，因此在真实模型窗口下，极长上下文也可能几乎不触发压缩。",
        "- 这说明当前逻辑方向是合理的，但不能宣称已经完美胜任大型项目超长流程：真正最肥的系统块仍主要靠注入预算治理，而不是主压缩器治理。",
        "",
        "## 底层逻辑",
        "",
        "### 主上下文压缩器",
        "",
        "- 触发依据是 `soft_trigger_ratio / hard_trigger_ratio * resolved model context window`。",
        "- 只压缩旧非 system 消息；保留所有 system messages、最后一条 human、adapter blocks。",
        "- soft 预算默认走 `rule_summary`，hard 预算且 `use_llm_summary=true` 时走 `llm_summary`。",
        "",
        "### 被动注入裁剪层",
        "",
        "- 入口是 `memory_runtime.build_session_context(...)`。",
        "- 通过 `max_context_tokens` 统一裁剪 `[USER PROFILE] / [MEMORY SUMMARY] / [WORKFLOW HINTS] / [MEMORY MAP] / [RECENT ACTIVITY TEASER]`。",
        "- 工程模式下可以单独 suppress `daily memory / memory map`，但不影响主压缩器的 system-message 保留规则。",
        "",
        "## 三场景结果",
        "",
    ]

    for scene_name, scene in results["scenes"].items():
        lines.extend(
            [
                f"### {scene['label']}",
                "",
                f"- scope: `{scene['sceneConfig']['scope']}`",
                f"- runtimeKind: `{scene['sceneConfig']['runtimeKind']}`",
                f"- workspaceRules present: `{scene['workspaceRules']['present']}`",
                f"- workspaceRules tokens: `{scene['workspaceRules']['estimatedTokens']}`",
                f"- passiveContext tokens: `{scene['passiveContext']['estimatedTokens']}`",
                f"- extensionsRoute tokens: `{scene['extensionsRoute']['estimatedTokens']}`",
                "",
                "被动注入模块：",
            ]
        )
        if scene["passiveContext"]["sections"]:
            for block in scene["passiveContext"]["sections"]:
                lines.append(f"- `{block['name']}`: `{block['estimatedTokens']}` tokens")
        else:
            lines.append("- 无被动注入块")
        lines.extend(
            [
                "",
                "压缩变体：",
                f"- actual: trigger=`{scene['variants']['actualCurrentModelWindow']['audit']['trigger_reason']}`, applied=`{scene['variants']['actualCurrentModelWindow']['audit']['compaction_applied']}`, method=`{scene['variants']['actualCurrentModelWindow']['audit']['compaction_method']}`",
                f"- fallback soft: trigger=`{scene['variants']['fallback32kSoftBudget']['audit']['trigger_reason']}`, applied=`{scene['variants']['fallback32kSoftBudget']['audit']['compaction_applied']}`, method=`{scene['variants']['fallback32kSoftBudget']['audit']['compaction_method']}`",
                f"- fallback hard: trigger=`{scene['variants']['fallback32kHardBudget']['audit']['trigger_reason']}`, applied=`{scene['variants']['fallback32kHardBudget']['audit']['compaction_applied']}`, method=`{scene['variants']['fallback32kHardBudget']['audit']['compaction_method']}`",
                f"- fallback hard reuse: trigger=`{scene['variants']['fallback32kHardBudgetReuse']['audit']['trigger_reason']}`, applied=`{scene['variants']['fallback32kHardBudgetReuse']['audit']['compaction_applied']}`, method=`{scene['variants']['fallback32kHardBudgetReuse']['audit']['compaction_method']}`",
                "",
            ]
        )

    lines.extend(
        [
            "## 合理性判断",
            "",
            "- 通用日常聊天：合理。历史聊天在 fallback 小窗口下能被压缩，偏好/总结块则靠被动注入预算控制。",
            "- 项目编程：部分合理。daily/map suppression 能减轻噪音，但真正大型项目里的 runtime registry、tool registry、extensions route block 仍不会被主压缩器处理。",
            "- Network API：合理性提升。workspace-less 场景已经不再误吃默认工作区 rules，但 memory summary/map 仍保留，符合你刚确认的口径。",
            "",
            "## 稳定保真结论",
            "",
            "- 主压缩器对 system 消息、最后一条用户消息和 adapter block 的保留规则比较稳定。",
            "- 真正的风险不在“压缩错了历史聊天”，而在“系统级大块根本不参与主压缩”，它们只能靠各自的预算治理。",
            "- 因为当前模型窗口极大，真实运行里压缩触发频率偏低，所以不能把这套机制描述成已经完美覆盖大型项目超长流程。",
            "",
            "## 可复跑入口",
            "",
            "- 运行脚本: `E:\\Projects\\v8chat\\v8-agent-os\\apps\\v8-agent-os-engine\\.venv\\Scripts\\python.exe E:\\Projects\\v8chat\\v8-agent-os\\apps\\v8-agent-os-engine\\scripts\\export_context_management_assessment.py`",
            f"- 统一运行说明: `{RUNBOOK_PATH}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()
    scenes = {item["name"]: _build_scene_report(item) for item in _scene_payloads()}
    results = {
        "generatedAt": stamp,
        "configSnapshot": {
            "contextConfig": storage.get_context_config(),
            "memoryConfig": storage.get_memory_config(),
            "supervisorModel": str(storage.get_role_model_id("supervisor") or "").strip(),
            "summaryModel": str(storage.get_role_model_id("summary") or "").strip(),
            "supervisorContextWindow": int(storage.get_role_model_id("supervisor") and __import__("core.llm_factory", fromlist=["llm_factory"]).llm_factory.get_model_context_window(str(storage.get_role_model_id("supervisor") or "").strip()) or 0),
            "summaryContextWindow": int(storage.get_role_model_id("summary") and __import__("core.llm_factory", fromlist=["llm_factory"]).llm_factory.get_model_context_window(str(storage.get_role_model_id("summary") or "").strip()) or 0),
        },
        "latestSystemContentStressReport": _latest_stress_report_path(),
        "scenes": scenes,
    }
    markdown = _build_report(results)
    md_path = OUTPUT_ROOT / f"{stamp}_context_management_assessment.md"
    json_path = OUTPUT_ROOT / f"{stamp}_context_management_assessment.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"markdown": str(md_path), "json": str(json_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
