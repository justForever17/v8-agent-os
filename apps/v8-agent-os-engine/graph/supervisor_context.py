import datetime
import json
import logging
import platform

from langchain_core.messages import HumanMessage

from core.storage import storage
from core.system_base import get_engine_origin
from core.v8_agent_os_identity import render_system_identity_line
from erc.capability_registry import capability_registry


logger = logging.getLogger("v8_agent_os.supervisor")


def _build_memory_recall_block(items: list[dict]) -> tuple[str, list[dict]]:
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
                "fact": clipped,
            }
        )
        lines.append(f"- {clipped}")
    if not lines:
        return "", []
    body = "\n".join(lines)
    return f"\n\n[MEMORY RECALL]\n{body}\n[/MEMORY RECALL]", facts


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
                scope_mode="mixed",
            )
            current_scope = resolved.binding.resolved_scope
            scope_chain = resolved.scope_chain or ["global", current_scope]
        except Exception:
            pass
    elif user_query:
        from core.scope_detector import detect_scope

        current_scope = detect_scope(user_query) or "global"
        scope_chain = ["global", current_scope] if current_scope != "global" else ["global"]

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
    workspace_config = storage.get_workspace_config()
    workspace_path = workspace_config.get("agent_workspace_path", "Not configured")
    os_name = platform.system()
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    identity_line = render_system_identity_line(storage.get_system_identity())
    env_context = (
        f"<environment>\n"
        f"OS: {os_name}\n"
        f"Current Time: {current_time}\n"
        f"{identity_line}\n"
        f"Sysadmin Privileges: You operate with the full permissions of the engine process. You are AUTHORIZED to manage the system, modify global configuration files (e.g., /etc, /var), and execute system commands globally when explicitly requested by the user.\n"
        f"Local Workspace Absolute Path: {workspace_path}\n"
        f"When generating visual artifacts, media, or formal reports meant to be viewed in the Web UI, you MUST save them to the Local Workspace above.\n"
        f"To display a workspace file in the chat, return a markdown image or link using the URL format: {get_engine_origin().rstrip('/')}/workspace/YOUR_FILE_NAME\n"
        f"</environment>\n"
    )

    base_prompt = config.system_prompt or storage.get_supervisor_prompt() or (
        "You are the V8 Agent OS AI Application Architect & Assistant.\n"
        "As the orchestration engine, you should delegate complex specialized tasks to specialized agents using the `handoff_to_*` tools.\n"
        "If a required specialized agent does not exist, use `create_agent` first.\n"
    )

    memory_context = memory_runtime.build_session_context(
        user_query=user_query,
        scope=current_scope,
        scope_chain=scope_chain,
    )

    runtime_registry_context = capability_registry.build_supervisor_summary(
        user_query=user_query,
        prioritized_kinds=["chat", "computer_use", "rpa", "memory", "channel", "automation"],
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

    approval_directive = (
        "\n\n[Human Collaboration Rule]\n"
        "If you cannot proceed safely without explicit user input, approval, missing credentials, or a concrete confirmation, "
        "do not guess and do not fabricate a result. "
        "Clearly explain what confirmation, credential, or parameter is missing. "
        "For irreversible or sensitive work, prefer runtime-managed approval and handoff flows instead of trying to continue blindly.\n"
        "When web_read or web_extract returns little text but includes media, analysisHints, or visionCandidates, "
        "prefer using vision_media_analyzer with the candidate sourceUrl instead of forcing a pure text summary.\n"
        "When a platform media page hides the real media source, or the URL likely requires browser cookies/session handling, "
        "prefer download_media_for_vision first so the media can be analyzed as a stable local file.\n"
        "If download_media_for_vision returns autoChainEligible=true and the user wants the media understood, "
        "prefer enabling its auto_chain_to_vision flow or immediately follow with vision_media_analyzer using chainedVisionArgs.\n"
    )

    system_content = (
        f"{base_prompt}\n\n"
        f"{runtime_registry_context}\n\n"
        f"{specialist_agents_context}"
        f"{available_tools_context}\n"
        f"{todos_context}{memory_context}\n\n"
        f"{env_context}{approval_directive}\n"
        f"{extension_prompt_addition}{group_moderation_directive}"
    )

    return {
        "system_content": system_content,
        "memory_context": memory_context,
        "runtime_registry_context": runtime_registry_context,
        "specialist_agents_context": specialist_agents_context,
        "available_tools_context": available_tools_context,
        "todos_context": todos_context,
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

    if not user_query or not passive_injection_enabled:
        return messages

    try:
        rag_results = memory_runtime.unified_recall(
            query=user_query,
            limit=passive_top_k,
            scopes=scope_chain,
        )
        if not rag_results:
            return messages

        rag_block, fact_bundle = _build_memory_recall_block(rag_results[:passive_top_k])
        if not rag_block or not fact_bundle:
            return messages

        updated_messages = list(messages)
        for i in range(len(updated_messages) - 1, -1, -1):
            if isinstance(updated_messages[i], HumanMessage):
                old_msg = updated_messages[i]
                new_content = old_msg.content
                if isinstance(new_content, str):
                    new_content += rag_block
                elif isinstance(new_content, list):
                    new_content = new_content.copy()
                    new_content.append({"type": "text", "text": rag_block})
                next_kwargs = dict(old_msg.additional_kwargs or {})
                next_kwargs["memory_rag"] = {
                    "query": user_query,
                    "facts": fact_bundle,
                    "scope_chain": scope_chain,
                }
                updated_messages[i] = HumanMessage(
                    content=new_content,
                    additional_kwargs=next_kwargs,
                    id=old_msg.id,
                )
                break
        return updated_messages
    except Exception as e:
        logger.warning("Interceptor RAG failed: %s", e)
        return messages
