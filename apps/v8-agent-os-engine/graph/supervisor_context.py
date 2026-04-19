import datetime
import hashlib
import json
import logging
import platform
from pathlib import Path

from langchain_core.messages import HumanMessage

from core.storage import storage
from core.system_base import get_engine_origin
from core.v8_agent_os_identity import render_system_identity_line
from core.workspace_guard import build_workspace_path_status
from core.workspace_resolution import workspace_resolution_service
from erc.capability_registry import capability_registry


logger = logging.getLogger("v8_agent_os.supervisor")
_STABLE_SYSTEM_CONTEXT_CACHE: dict[str, dict[str, str]] = {}
_STABLE_SYSTEM_CONTEXT_CACHE_LIMIT = 64
_PASSIVE_RAG_HINT_TOKENS = (
    "remember",
    "recall",
    "history",
    "previous",
    "before",
    "again",
    "context",
    "workspace",
    "project",
    "继续",
    "之前",
    "上次",
    "记得",
    "历史",
    "上下文",
    "项目",
    "工作区",
)


def _resolved_workspace_prompt_path() -> str:
    raw_workspace_path = str(storage.get_workspace_config().get("agent_workspace_path") or "").strip()
    if raw_workspace_path:
        status = build_workspace_path_status(raw_workspace_path)
        if status.get("isLegacyResidue"):
            return str(status.get("recommendedPath") or workspace_resolution_service.get_main_workspace_path())
        return str(Path(raw_workspace_path).expanduser())
    return workspace_resolution_service.get_main_workspace_path()


def _normalize_workspace_path(value: str | None) -> str:
    raw = str(value or "").strip()
    return str(Path(raw).expanduser()) if raw else ""


def _collect_workspace_rules_roots(*, state, session_id: str | None) -> list[dict[str, str]]:
    roots: list[dict[str, str]] = []
    seen_workspace_paths: set[str] = set()

    main_workspace_path = _normalize_workspace_path(workspace_resolution_service.get_main_workspace_path())
    if main_workspace_path:
        seen_workspace_paths.add(main_workspace_path)
        roots.append(
            {
                "source": "main_workspace",
                "label": "main workspace",
                "workspacePath": main_workspace_path,
                "workspaceId": "",
                "projectId": "",
            }
        )

    descriptor = workspace_resolution_service.resolve_workspace_descriptor(
        runtime_kind="chat",
        session_id=session_id,
        explicit_workspace_id=state.get("workspace_id"),
        explicit_workspace_path=state.get("workspace_path"),
        explicit_project_id=state.get("project_id"),
    )
    scoped_workspace_path = _normalize_workspace_path(str(descriptor.get("workspaceRoot") or ""))
    if (
        scoped_workspace_path
        and scoped_workspace_path not in seen_workspace_paths
        and bool(descriptor.get("isScopedOverride"))
    ):
        roots.append(
            {
                "source": "scoped_workspace",
                "label": "scoped workspace",
                "workspacePath": scoped_workspace_path,
                "workspaceId": str(descriptor.get("workspaceId") or "").strip(),
                "projectId": str(descriptor.get("projectId") or "").strip(),
            }
        )

    return roots


def _build_workspace_rules_context(*, state, session_id: str | None) -> str:
    rendered_sections: list[str] = []
    for root in _collect_workspace_rules_roots(state=state, session_id=session_id):
        workspace_path = str(root.get("workspacePath") or "").strip()
        if not workspace_path:
            continue
        rules_dir = Path(workspace_path) / ".agents" / "rules"
        if not rules_dir.exists() or not rules_dir.is_dir():
            continue
        for rule_path in sorted(rules_dir.glob("*.md"), key=lambda item: item.name.lower()):
            if not rule_path.is_file():
                continue
            content = rule_path.read_text(encoding="utf-8").strip()
            if not content:
                continue
            header_lines = [
                f"### {rule_path.name}",
                f"Source: {root.get('label')}",
                f"Workspace: {workspace_path}",
                f"Path: {rule_path}",
            ]
            if root.get("projectId"):
                header_lines.append(f"Project ID: {root.get('projectId')}")
            if root.get("workspaceId"):
                header_lines.append(f"Workspace ID: {root.get('workspaceId')}")
            rendered_sections.append("\n".join(header_lines) + "\n\n" + content)

    if not rendered_sections:
        return ""
    return "[WORKSPACE RULES]\n" + "\n\n---\n\n".join(rendered_sections) + "\n[/WORKSPACE RULES]\n"


def _build_memory_recall_block(items: list[dict]) -> tuple[dict | None, list[dict]]:
    facts: list[dict] = []
    lines: list[str] = []
    for item in items:
        fact = str(item.get("fact") or "").strip()
        if not fact:
            continue
        clipped = (fact[:240] + "...") if len(fact) > 240 else fact
        facts.append(
            {
                "id": item.get("id"),
                "scope": item.get("scope"),
                "category": item.get("category"),
                "source": item.get("source"),
                "raw_relevance_score": item.get("raw_relevance_score"),
                "final_relevance_score": item.get("final_relevance_score"),
                "fact": clipped,
            }
        )
        lines.append(f"- {clipped}")
    if not lines:
        return None, []
    return (
        {
            "type": "memory_recall",
            "title": "记忆召回",
            "content": "\n".join(lines),
            "metadata": {
                "runtime_plane": "memory",
                "fact_count": len(facts),
                "top_scores": [
                    float(item.get("final_relevance_score") or 0.0)
                    for item in items
                    if float(item.get("final_relevance_score") or 0.0) > 0
                ],
            },
        },
        facts,
    )


def _annotate_last_human_message(
    messages,
    *,
    diagnostics: dict,
    rag_block: dict | None = None,
    fact_bundle: list[dict] | None = None,
):
    updated_messages = list(messages)
    for i in range(len(updated_messages) - 1, -1, -1):
        if not isinstance(updated_messages[i], HumanMessage):
            continue
        old_msg = updated_messages[i]
        next_kwargs = dict(old_msg.additional_kwargs or {})
        next_kwargs["memory_rag_diagnostics"] = diagnostics
        if rag_block and fact_bundle:
            context_blocks = next_kwargs.get("context_adapter_blocks")
            if isinstance(context_blocks, list):
                next_blocks = list(context_blocks)
            elif isinstance(context_blocks, dict):
                next_blocks = [context_blocks]
            else:
                next_blocks = []
            next_blocks.append(rag_block)
            next_kwargs["context_adapter_blocks"] = next_blocks
            next_kwargs["memory_rag"] = {
                "query": diagnostics.get("query"),
                "facts": fact_bundle,
                "scope_chain": diagnostics.get("scope_chain") or [],
                "threshold": diagnostics.get("threshold"),
                "top_scores": diagnostics.get("top_scores") or [],
            }
        updated_messages[i] = HumanMessage(
            content=old_msg.content,
            name=getattr(old_msg, "name", None),
            additional_kwargs=next_kwargs,
            id=old_msg.id,
        )
        break
    return updated_messages


def resolve_supervisor_request_context(messages, scope_resolution_service):
    user_query = ""
    current_scope = "global"
    scope_chain = ["global"]
    session_id = None
    last_human_message = None

    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        last_human_message = message
        if isinstance(message.content, str):
            user_query = message.content
        elif isinstance(message.content, list):
            user_query = " ".join(
                [item.get("text", "") for item in message.content if isinstance(item, dict) and item.get("type") == "text"]
            )

        if message.additional_kwargs and "exec_context" in message.additional_kwargs:
            payload = message.additional_kwargs.get("payload", {})
            if isinstance(payload, dict):
                if "instruction" in payload:
                    user_query = str(payload["instruction"])
                elif "message" in payload:
                    user_query = str(payload["message"])
                elif "task" in payload:
                    user_query = str(payload["task"])
                else:
                    user_query = ""

        if message.additional_kwargs:
            session_id = message.additional_kwargs.get("session_id")
        break

    if session_id:
        try:
            resolved = scope_resolution_service.resolve(
                session_id=session_id,
                conversation_id=session_id,
                user_id=(last_human_message.additional_kwargs or {}).get("user_id") if last_human_message else None,
                user_query=user_query,
                project_id=(last_human_message.additional_kwargs or {}).get("project_id") if last_human_message else None,
                workspace_id=(last_human_message.additional_kwargs or {}).get("workspace_id") if last_human_message else None,
                workspace_path=(last_human_message.additional_kwargs or {}).get("workspace_path") if last_human_message else None,
                workflow_id=(last_human_message.additional_kwargs or {}).get("workflow_id") if last_human_message else None,
                channel_type=(last_human_message.additional_kwargs or {}).get("channel_type") if last_human_message else None,
                channel_remote_id=(last_human_message.additional_kwargs or {}).get("channel_remote_id") if last_human_message else None,
                scope_hint=(last_human_message.additional_kwargs or {}).get("resolved_scope") if last_human_message else None,
                scope_mode="explicit",
            )
            current_scope = resolved.binding.resolved_scope
            scope_chain = resolved.scope_chain or ["global", current_scope]
        except Exception:
            pass

    return {
        "user_query": user_query,
        "current_scope": current_scope,
        "scope_chain": scope_chain,
        "session_id": session_id,
        "last_human_message": last_human_message,
    }


def build_supervisor_system_content(
    *,
    state,
    config,
    user_query: str,
    current_scope: str,
    scope_chain: list[str],
    session_id: str | None,
    messages,
    loaded_agents: list[dict],
    supervisor_tools: list,
    memory_runtime,
    extension_prompt_addition: str = "",
):
    workspace_path = _resolved_workspace_prompt_path()
    os_name = platform.system()
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    identity_line = render_system_identity_line(storage.get_system_identity())
    base_prompt = config.system_prompt or storage.get_supervisor_prompt() or (
        "You are the V8 Agent OS AI Application Architect & Assistant.\n"
        "As the orchestration engine, you should delegate complex specialized tasks to specialized agents using the `handoff_to_*` tools.\n"
        "If a required specialized agent does not exist, use `create_agent` first.\n"
    )

    stable_signature = hashlib.sha1(
        json.dumps(
            {
                "basePrompt": base_prompt,
                "identityLine": identity_line,
                "workspacePath": str(workspace_path),
                "osName": os_name,
                "engineOrigin": get_engine_origin().rstrip("/"),
                "agents": [
                    {
                        "id": str(agent.get("id") or "").strip(),
                        "name": str(agent.get("name") or "").strip(),
                        "description": str(agent.get("description") or "").strip(),
                    }
                    for agent in list(loaded_agents or [])
                    if isinstance(agent, dict)
                ],
                "tools": [
                    {
                        "name": str(getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")) or "").strip(),
                        "description": str(getattr(tool_ref, "description", getattr(tool_ref, "__doc__", "")) or "").strip().split("\n")[0],
                    }
                    for tool_ref in list(supervisor_tools or [])
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    cached_stable = _STABLE_SYSTEM_CONTEXT_CACHE.get(stable_signature)
    if cached_stable is None:
        env_static_context = (
            f"OS: {os_name}\n"
            f"{identity_line}\n"
            "Sysadmin Privileges: You operate with the full permissions of the engine process. "
            "You are AUTHORIZED to manage the system, modify global configuration files (e.g., /etc, /var), "
            "and execute system commands globally when explicitly requested by the user.\n"
            f"Local Workspace Absolute Path: {workspace_path}\n"
            "When generating visual artifacts, media, or formal reports meant to be viewed in the Web UI, "
            "you MUST save them to the Local Workspace above.\n"
            "Do NOT expose raw local filesystem paths, raw /api/workspace/files links, or raw <img>/<video>/<audio> HTML in the final reply. "
            "Reference generated media naturally in prose and rely on the runtime artifact/resource pipeline for rendering.\n"
        )
        available_tools_context = "--- SUPERVISOR DIRECT TOOL REGISTRY ---\n"
        available_tools_context += "下面只列出你当前可直接调用的工具。模块级任务优先参考 Runtime 能力卡片来路由，而不是硬记所有模块细节。\n"
        for tool_ref in supervisor_tools:
            tool_name = getattr(tool_ref, "name", tool_ref.__name__ if hasattr(tool_ref, "__name__") else "")
            if not tool_name:
                continue
            tool_desc = getattr(tool_ref, "description", getattr(tool_ref, "__doc__", "")).strip().split("\n")[0]
            available_tools_context += f"- {tool_name}: {tool_desc}\n"
        available_tools_context += "---------------------------------------\n"

        specialist_agents_context = "--- SPECIALIST AGENT REGISTRY ---\n"
        specialist_agents = [agent for agent in loaded_agents if agent.get("id") != "supervisor"]
        if specialist_agents:
            for agent in specialist_agents:
                specialist_agents_context += (
                    f"- {agent.get('name') or agent.get('id')} ({agent.get('id')}): "
                    f"{agent.get('description') or 'No description'} | tools={len(agent.get('tools') or [])}\n"
                )
        else:
            specialist_agents_context += "- 暂无已注册的专业子 Agent，可按需使用 create_agent 创建。\n"
        specialist_agents_context += "--------------------------------\n"

        cached_stable = {
            "envStaticContext": env_static_context,
            "availableToolsContext": available_tools_context,
            "specialistAgentsContext": specialist_agents_context,
        }
        _STABLE_SYSTEM_CONTEXT_CACHE[stable_signature] = cached_stable
        if len(_STABLE_SYSTEM_CONTEXT_CACHE) > _STABLE_SYSTEM_CONTEXT_CACHE_LIMIT:
            for key in list(_STABLE_SYSTEM_CONTEXT_CACHE.keys())[: len(_STABLE_SYSTEM_CONTEXT_CACHE) - _STABLE_SYSTEM_CONTEXT_CACHE_LIMIT]:
                _STABLE_SYSTEM_CONTEXT_CACHE.pop(key, None)

    env_context = (
        "<environment>\n"
        f"Current Time: {current_time}\n"
        f"{cached_stable['envStaticContext']}"
        "</environment>\n"
    )

    memory_context = memory_runtime.build_session_context(
        user_query=user_query,
        scope=current_scope,
        scope_chain=scope_chain,
    )
    workspace_rules_context = _build_workspace_rules_context(state=state, session_id=session_id)

    runtime_registry_context = capability_registry.build_supervisor_summary(
        user_query=user_query,
        prioritized_kinds=["chat", "computer_use", "rpa", "memory", "channel", "automation"],
    )

    available_tools_context = cached_stable["availableToolsContext"]
    specialist_agents_context = cached_stable["specialistAgentsContext"]

    todos_context = ""
    raw_todos = state.get("todos", [])
    if raw_todos:
        from .task_context import resolve_todos

        todos_data = resolve_todos(raw_todos)
        task_info = todos_data.get("task_info", {})
        resolved = todos_data.get("items", [])

        if task_info.get("name"):
            storage.save_active_todos(task_info, resolved)

        if resolved:
            icon_map = {"done": "✓", "in_progress": "→", "pending": " ", "skipped": "⊘"}
            lines = ["--- TASK PLAN ---"]
            if task_info.get("name"):
                lines.append(f"Task Name: {task_info['name']}")
            for i, item in enumerate(resolved):
                icon = icon_map.get(item.get("status", "pending"), " ")
                lines.append(f"  [{icon}] #{i}: {item.get('text', '???')}")
            if task_info.get("isStale"):
                lines.append("")
                lines.append("⚠️ 当前任务计划已长时间未更新。若工作仍在继续，请优先更新 todos 状态或重写计划。")
            lines.append("-----------------")

            all_done = all(item.get("status") in ("done", "skipped") for item in resolved)
            if all_done:
                lines.extend(
                    [
                        "",
                        "🏁 所有任务已全部完成！",
                        "你必须在本轮回复中输出一段详尽的工作汇报总结：",
                        "1. 对每个完成的任务进行简要回顾",
                        "2. 涉及到的文件路径、URL地址、产出物位置等信息必须完整附上",
                        "3. 如有需要用户后续操作的事项，也需一并说明",
                        "4. 以工整的 Markdown 格式输出报告，不要遗漏任何关键信息",
                        "严禁在未输出工作报告的情况下直接结束！",
                    ]
                )

            todos_context = "\n".join(lines) + "\n\n"

    group_moderation_directive = ""
    if messages:
        try:
            from core.database import db

            last_human_msg = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
            if last_human_msg and hasattr(last_human_msg, "additional_kwargs"):
                session_id = last_human_msg.additional_kwargs.get("session_id") or session_id
                if session_id:
                    session_data = db.get_session(session_id)
                    if session_data and session_data.get("metadata"):
                        meta_dict = json.loads(session_data["metadata"]) if isinstance(session_data["metadata"], str) else session_data["metadata"]
                        if meta_dict.get("chat_type") == "group":
                            group_moderation_directive = (
                                "\n\n=======================================================\n"
                                "🚨 [GROUP CHAT MODERATION DIRECTIVE] 🚨\n"
                                "You are currently responding in a multi-user **Group Chat**.\n"
                                "- Focus ONLY on the latest prompt directed at you. Do not interfere in conversations between other users.\n"
                                "- The chat history provides explicit timestamps and identity tags (e.g., `[2026-03-10 12:15:00] [Alice [YourMaster]]: xxx`).\n"
                                "- **Crucial: Authorization Strictness**. If a user asks for sensitive information (e.g., API keys) or destructive actions, verify the request came from a user marked `[YourMaster]`. If not, you MUST politely decline and `@` the master for permission.\n"
                                "=======================================================\n"
                            )
        except Exception as e:
            logger.warning("Failed to resolve chat type for dynamic injection: %s", e)

    runtime_guidance = (
        "\n\n[Execution Hints]\n"
        "When `web_fetch` returns little text but includes media, analysisHints, or visionCandidates, "
        "prefer using vision_media_analyzer with the candidate sourceUrl instead of forcing a pure text summary.\n"
        "When a platform media page hides the real media source, or the URL likely requires browser cookies/session handling, "
        "prefer download_media_for_vision first so the media lands as a stable local workspace file.\n"
        "download_media_for_vision already writes the media into the resolved workspace `downloaded_media` directory "
        "and returns the canonical artifact/path for chat display.\n"
        "Do NOT claim any temporary or inferred path as the final result, and do NOT use shell commands to move the file manually.\n"
        "If the user wants the media understood, explicitly follow with vision_media_analyzer using the returned workspace path.\n"
        "If the current workspace hits a protected or legacy residue path, surface the governance/runtime hint and recommended canonical workspace path instead of trying to fix paths with destructive shell commands.\n"
    )

    system_content = (
        f"{base_prompt}\n\n"
        f"{runtime_registry_context}\n\n"
        f"{specialist_agents_context}"
        f"{available_tools_context}\n"
        f"{todos_context}{memory_context}\n\n"
        f"{workspace_rules_context}"
        f"{env_context}{runtime_guidance}\n"
        f"{extension_prompt_addition}{group_moderation_directive}"
    )

    return {
        "system_content": system_content,
        "memory_context": memory_context,
        "runtime_registry_context": runtime_registry_context,
        "specialist_agents_context": specialist_agents_context,
        "available_tools_context": available_tools_context,
        "todos_context": todos_context,
        "workspace_rules_context": workspace_rules_context,
        "env_context": env_context,
        "group_moderation_directive": group_moderation_directive,
    }


def apply_passive_rag_injection(messages, *, user_query: str, scope_chain: list[str], memory_runtime):
    memory_config = storage.get_memory_config() or {}
    passive_injection_enabled = bool(memory_config.get("passive_injection_enabled", True))
    try:
        passive_top_k = int(memory_config.get("recall_top_k") or 1)
    except (TypeError, ValueError):
        passive_top_k = 1
    passive_top_k = max(1, min(passive_top_k, 3))

    human_turns = sum(1 for message in messages if isinstance(message, HumanMessage))
    normalized_query = str(user_query or "").strip().lower()
    has_recall_cue = any(token in normalized_query for token in _PASSIVE_RAG_HINT_TOKENS)
    try:
        retrieval_threshold = float(memory_config.get("retrieval_threshold"))
    except (TypeError, ValueError, KeyError):
        retrieval_threshold = 0.20
    retrieval_threshold = max(0.0, min(retrieval_threshold, 1.0))
    passive_gate = max(retrieval_threshold, 0.35)
    diagnostics = {
        "query": user_query,
        "scope_chain": list(scope_chain or []),
        "threshold": passive_gate,
        "configured_threshold": retrieval_threshold,
        "top_scores": [],
        "injection_allowed": False,
        "reject_reason": "",
        "has_recall_cue": has_recall_cue,
        "human_turns": human_turns,
    }
    if not user_query or not passive_injection_enabled:
        diagnostics["reject_reason"] = "passive_injection_disabled_or_empty_query"
        return _annotate_last_human_message(messages, diagnostics=diagnostics)
    if human_turns <= 1 and not has_recall_cue:
        diagnostics["reject_reason"] = "insufficient_conversational_continuity"
        return _annotate_last_human_message(messages, diagnostics=diagnostics)
    if len(normalized_query) < 24 and not has_recall_cue:
        diagnostics["reject_reason"] = "query_too_short_without_recall_cue"
        return _annotate_last_human_message(messages, diagnostics=diagnostics)
    if len(scope_chain or []) <= 1 and len(normalized_query.split()) < 4 and not has_recall_cue:
        diagnostics["reject_reason"] = "scope_too_sparse_without_recall_cue"
        return _annotate_last_human_message(messages, diagnostics=diagnostics)

    try:
        rag_results = memory_runtime.unified_recall(
            query=user_query,
            limit=passive_top_k,
            scopes=scope_chain,
        )
        if not rag_results:
            diagnostics["reject_reason"] = "no_recall_results"
            return _annotate_last_human_message(messages, diagnostics=diagnostics)

        top_scores = [float(item.get("final_relevance_score") or item.get("relevance_score") or 0.0) for item in rag_results]
        diagnostics["top_scores"] = top_scores
        top1 = top_scores[0] if top_scores else 0.0
        second_score = top_scores[1] if len(top_scores) > 1 else 0.0
        if top1 < passive_gate:
            diagnostics["reject_reason"] = "top_score_below_passive_gate"
            return _annotate_last_human_message(messages, diagnostics=diagnostics)
        if not has_recall_cue and len(top_scores) > 1 and second_score < max(retrieval_threshold, 0.15):
            diagnostics["reject_reason"] = "score_distribution_too_sparse"
            return _annotate_last_human_message(messages, diagnostics=diagnostics)

        rag_block, fact_bundle = _build_memory_recall_block(rag_results[:passive_top_k])
        if not rag_block or not fact_bundle:
            diagnostics["reject_reason"] = "recall_block_empty"
            return _annotate_last_human_message(messages, diagnostics=diagnostics)
        rag_block.setdefault("metadata", {})
        rag_block["metadata"]["threshold"] = passive_gate

        diagnostics["injection_allowed"] = True
        return _annotate_last_human_message(
            messages,
            diagnostics=diagnostics,
            rag_block=rag_block,
            fact_bundle=fact_bundle,
        )
    except Exception as e:
        logger.warning("Interceptor RAG failed: %s", e)
        diagnostics["reject_reason"] = f"rag_injection_failed:{e}"
        return _annotate_last_human_message(messages, diagnostics=diagnostics)
