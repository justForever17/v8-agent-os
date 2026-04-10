"""
Supervisor Team Cron Bridge Script
===================================
This script allows Cron jobs to trigger the Supervisor team to execute arbitrary tasks.
It replicates the config resolution logic from routes.py to build a standalone EngineConfig,
then invokes the Supervisor graph with a HumanMessage containing the task description.

Usage in Cron config (`~/.v8-agent-os/config.json#cron`):
{
    "action_type": "python",
    "action_target": "scripts.cron_supervisor_task",
    "payload": {
        "task": "搜索今天的科技新闻头条，生成简报并发送到飞书群"
    }
}
"""
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing

from core.graph_stream_watchdog import (
    GraphStreamIdleTimeoutError,
    GraphStreamWatchdogState,
    next_graph_stream_event,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("CronSupervisorTask")


def _run_coro_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def run(action_payload: dict = None, payload: dict = None, **kwargs):
    """
    Entry point called by ActionExecutor.
    ActionExecutor passes `action_payload` (and legacy `payload`) from the cron config.
    """
    # Merge payload sources (ActionExecutor may use either key)
    p = payload or action_payload or {}
    session_id = None
    run_id = None
    timeout_seconds = p.get("timeout", 600)
    task_description = str(
        p.get("task")
        or p.get("instruction")
        or p.get("message")
        or ""
    ).strip()

    if not task_description:
        logger.error("No 'task' field found in payload. Nothing to execute.")
        return

    logger.info(f"Supervisor Cron Task received: {task_description[:100]}...")

    try:
        from core.storage import StorageManager
        from core.engine_config_resolver import resolve_engine_config_for_role
        from api.models import EngineConfig
        from agents.runners.supervisor_runner import supervisor_runner
        from graph.supervisor import AgentState
        from langchain_core.messages import HumanMessage
        from core.chat_output_extractor import extract_text_and_reasoning
        from core.database import db
        from core.runtime_contexts import (
            build_automation_context_blocks,
            build_automation_task_envelope,
            build_automation_scope,
            build_cron_session_id,
            build_hook_session_id,
            build_job_memory,
            build_recent_run_summaries,
            coerce_json_dict,
        )
        from erc.models import RuntimeSource
        from erc.runtime_context import bind_runtime_context
        from runtimes.automation.runtime import automation_runtime
        from runtimes.memory.scope_resolution import scope_resolution_service, session_scope_binding_service

        storage = StorageManager()

        # === Resolve EngineConfig ===
        config = resolve_engine_config_for_role(
            "automation",
            fallback_provider="openai",
            fallback_model="gpt-4o",
        )["engine_config"]

        logger.info(f"Resolved config: provider={config.provider}, model={config.model_name}")

        # === Build and invoke the Supervisor graph with durable checkpointer ===
        graph = _run_coro_sync(supervisor_runner.build_graph(config))

        import uuid
        
        trigger_source = kwargs.get("trigger", "cron")
        is_hook = trigger_source.startswith("hook")
        
        prefix = "hook" if is_hook else "cron"
        display_name = "System Hook" if is_hook else "Cron System"
        title_prefix = "[Hook Trigger]" if is_hook else "[Cron Task]"
        system_marker = "[系统自动化触发器] 以下是由 Hook 事件拦截器触发的隐式任务，请立即执行：\n\n" if is_hook else "[系统定时任务] 以下是一个由 Cron 系统自动触发的定时任务，请立即执行：\n\n"
        automation_id = kwargs.get("cron_job_id") or kwargs.get("event_name") or p.get("job_id") or task_description[:40]
        inherited_run_id = str(kwargs.get("run_id") or "").strip() or None
        inherited_session_id = str(kwargs.get("session_id") or "").strip() or None
        session_id = inherited_session_id or (build_hook_session_id(automation_id) if is_hook else build_cron_session_id(automation_id))
        run_id = inherited_run_id or f"{prefix}_{uuid.uuid4().hex}"
        ctx_config = storage.get_context_config()
        rec_limit = ctx_config.get("recursion_limit", 500)
        graph_config = {"configurable": {"thread_id": session_id}, "recursion_limit": rec_limit}

        existing_session = db.get_session(session_id)
        existing_metadata = coerce_json_dict(existing_session.get("metadata")) if existing_session else {}
        previous_messages = db.get_messages(session_id)
        automation_policy = dict((ctx_config.get("runtime_adapters") or {}).get("automation") or {})
        recent_limit = int(automation_policy.get("recent_run_limit") or 3)
        job_memory_limit = int(automation_policy.get("job_memory_limit") or 6)
        recent_summaries = build_recent_run_summaries(previous_messages, limit=recent_limit)
        job_memory = existing_metadata.get("job_memory") or build_job_memory(previous_messages, limit=job_memory_limit)
        context_blocks = build_automation_context_blocks(
            recent_summaries=recent_summaries,
            job_memory=job_memory,
        )
        existing_binding = session_scope_binding_service.get_binding(session_id)
        fallback_scope_hint = p.get("scope_hint") or build_automation_scope(prefix, automation_id)

        channel_id = p.get("channel_id")
        channel_instruction = ""
        if channel_id:
            logger.info(f"Injecting channel_id '{channel_id}' instruction into cron message.")
            channel_instruction = (
                f"[OUTBOUND CHANNEL]\n"
                f"You have been awoken by a scheduled Cron job. You MUST use your external messaging or channel tool "
                f"to broadcast your final summary or result to the channel ID '{channel_id}' before finishing.\n"
                f"[/OUTBOUND CHANNEL]"
            )

        cron_task_message = (
            f"{system_marker}"
            + build_automation_task_envelope(
                trigger_label=title_prefix,
                task_description=task_description,
                payload=p,
                channel_instruction=channel_instruction,
            )
        )

        db.create_or_update_session(
            session_id=session_id,
            title=f"{title_prefix} {task_description[:30]}...",
            user_id=f"system_{prefix}",
            metadata={
                **existing_metadata,
                "source": prefix,
                "automation_id": str(automation_id),
                "task_name": task_description,
                "channel_id": channel_id,
                "job_memory": job_memory,
                "recent_run_summaries": recent_summaries,
            }
        )
        scope_result = scope_resolution_service.resolve(
            session_id=session_id,
            conversation_id=session_id,
            user_id=f"system_{prefix}",
            user_query=task_description,
            project_id=p.get("project_id"),
            workspace_id=p.get("workspace_id"),
            workspace_path=p.get("workspace_path"),
            scope_hint=fallback_scope_hint,
            scope_mode="explicit" if p.get("project_id") or p.get("workspace_id") or p.get("scope_hint") else "mixed",
            run_id=run_id,
        )
        db.update_session_metadata(
            session_id,
            {
                "project_id": scope_result.binding.project_id,
                "workspace_id": scope_result.binding.workspace_id,
                "workspace_path": scope_result.binding.workspace_path,
                "resolved_scope": scope_result.binding.resolved_scope,
                "scope_source": scope_result.binding.scope_source,
            },
        )
        state = AgentState(
            messages=[
                HumanMessage(
                    content=cron_task_message,
                    additional_kwargs={
                        "exec_context": kwargs,
                        "payload": p,
                        "session_id": session_id,
                        "user_id": f"system_{prefix}",
                        "project_id": scope_result.binding.project_id,
                        "workspace_id": scope_result.binding.workspace_id,
                        "workspace_path": scope_result.binding.workspace_path,
                        "resolved_scope": scope_result.binding.resolved_scope,
                        "scope_source": scope_result.binding.scope_source,
                        "scope_chain": scope_result.scope_chain,
                        "context_adapter_blocks": context_blocks,
                        "automation": {
                            "kind": prefix,
                            "id": str(automation_id),
                            "session_id": session_id,
                            "run_id": run_id,
                        },
                    },
                )
            ]
        )
        run_handle = automation_runtime.attach_run(str(run_id)) if run_id else None
        created_local_run = False
        if run_handle is None:
            run_handle = automation_runtime.begin_run(
                action_type="agent",
                target="supervisor",
                payload=p,
                trigger_source=trigger_source,
                is_async=False,
                kwargs={
                    **dict(kwargs or {}),
                    "session_id": session_id,
                    "run_id": run_id,
                    "user_id": f"system_{prefix}",
                    "task_name": task_description,
                    "cron_job_id": kwargs.get("cron_job_id"),
                    "event_name": kwargs.get("event_name"),
                    "channel_id": channel_id,
                    "project_id": scope_result.binding.project_id,
                    "workspace_id": scope_result.binding.workspace_id,
                    "workspace_path": scope_result.binding.workspace_path,
                    "resolved_scope": scope_result.binding.resolved_scope,
                    "scope_source": scope_result.binding.scope_source,
                },
            )
            run_handle.emit(
                "run.created",
                {
                    "run_id": run_handle.run_id,
                    "transport": prefix,
                    "trigger_source": trigger_source,
                    "action_type": "agent",
                    "action_target": "supervisor",
                    "automation_id": str(automation_id),
                    "resolved_scope": scope_result.binding.resolved_scope,
                    "project_id": scope_result.binding.project_id,
                },
            )
            created_local_run = True

        def _persist_runtime_event(topic: str, payload: dict, *, node: str, agent_id: str | None = "supervisor"):
            return run_handle.emit(
                topic,
                payload,
                source=RuntimeSource(
                    plane="engine",
                    component="automation_runtime",
                    node=node,
                    agent_id=agent_id,
                ),
            )

        if not scope_result.reused_existing_binding:
            scope_evidence = dict(scope_result.evidence or {})
            _persist_runtime_event(
                "scope.binding.updated" if existing_binding else "scope.binding.created",
                {
                    "session_id": session_id,
                    "project_id": scope_result.binding.project_id,
                    "workspace_id": scope_result.binding.workspace_id,
                    "resolved_scope": scope_result.binding.resolved_scope,
                    "scope_source": scope_result.binding.scope_source,
                    "scope_chain": scope_result.scope_chain,
                    "rebind_reason": str(scope_evidence.get("rebind_reason") or "").strip() or None,
                    "previous_scope": str(scope_evidence.get("previous_scope") or "").strip() or None,
                    "next_scope": str(scope_evidence.get("next_scope") or "").strip() or None,
                    "scope_anchor_comparison": scope_evidence.get("scope_anchor_comparison") if isinstance(scope_evidence.get("scope_anchor_comparison"), dict) else None,
                },
                node="scope_resolution",
                agent_id=None,
            )

        user_message_id = str(uuid.uuid4())
        db.add_message(
            msg_id=user_message_id,
            session_id=session_id,
            role="user",
            content=cron_task_message,
            metadata={
                "run_id": run_id,
                "trigger_source": trigger_source,
                "automation_id": str(automation_id),
                "project_id": scope_result.binding.project_id,
                "workspace_id": scope_result.binding.workspace_id,
                "resolved_scope": scope_result.binding.resolved_scope,
            },
            agent_name=display_name
        )
        _persist_runtime_event(
            "message.user.recorded",
            {
                "message_id": user_message_id,
                "content": cron_task_message,
                "automation_id": str(automation_id),
                "resolved_scope": scope_result.binding.resolved_scope,
            },
            node="automation_runtime",
            agent_id=None,
        )

        # === Execute graph using astream_events (same path as Web chat) ===
        # Using ainvoke causes subtle parallel execution issues with LangGraph's pregel engine.
        # astream_events is the battle-tested path used by routes.py for Web chat.
        import asyncio
        from core.action_executor import ActionExecutor as _AE

        async def _run_graph():
            """Async coroutine that mirrors the routes.py execution pattern."""
            final_output = ""
            current_agent = "supervisor"
            streamed_model_run_ids: set[str] = set()
            watchdog = GraphStreamWatchdogState()

            event_stream = graph.astream_events(state, config=graph_config, version="v2")
            async with aclosing(event_stream):
                stream_iter = event_stream.__aiter__()
                while True:
                    try:
                        event = await next_graph_stream_event(
                            stream_iter,
                            state=watchdog,
                            session_id=session_id,
                            run_id=run_id,
                            on_timeout=lambda payload: _persist_runtime_event(
                                "run.watchdog.stream_idle_timeout",
                                payload,
                                node="stream_watchdog",
                                agent_id=None,
                            ),
                        )
                    except StopAsyncIteration:
                        break
                    try:
                        kind = event["event"]
                        name = event.get("name", "")
                        data = event.get("data", {})
                        tags = event.get("tags", [])

                        # Track agent transitions (same logic as routes.py)
                        if kind == "on_chain_start" and name not in ["LangGraph", "__start__", "supervisor_tools"] and not name.endswith("_tools"):
                            if "graph:step" in tags or name == "supervisor":
                                if name != current_agent:
                                    current_agent = name
                                    logger.info(f"Agent switch: {current_agent}")

                        # Collect final text output from LLM streaming
                        elif kind == "on_chat_model_stream":
                            chunk = data.get("chunk")
                            if chunk:
                                text_delta, _ = extract_text_and_reasoning(chunk)
                                if text_delta:
                                    watchdog.note_text_progress()
                                    streamed_model_run_ids.add(event.get("run_id", ""))
                                    final_output += text_delta
                        elif kind == "on_chat_model_end":
                            model_run_id = event.get("run_id", "")
                            if model_run_id in streamed_model_run_ids:
                                continue
                            final_output_candidate = data.get("output")
                            text_delta, _ = extract_text_and_reasoning(final_output_candidate)
                            if text_delta:
                                watchdog.note_text_progress()
                                final_output += text_delta

                        # Log tool calls
                        elif kind == "on_tool_start":
                            watchdog.note_tool_start(event.get("run_id", ""))
                            tool_name = event.get("name", "unknown")
                            logger.info(f"Tool call: {tool_name}")
                        elif kind == "on_tool_end":
                            watchdog.note_tool_end(event.get("run_id", ""))
                    finally:
                        watchdog.finish_event(event)

            return final_output

        supervisor_profile = storage.get_agent_runtime_profile("supervisor")
        _persist_runtime_event(
            "agent.started",
            {
                "agent": {
                    "id": "supervisor",
                    "name": supervisor_profile.get("name") or "智能主管",
                    "roleLabel": supervisor_profile.get("roleLabel") or "主理人",
                    "avatar": supervisor_profile.get("avatar") or "",
                }
            },
            node="supervisor",
        )

        # Submit to the main event loop (where MCP connections live)
        main_loop = _AE._main_loop
        runtime_context = dict(
            runtime_kind=f"{prefix}_task",
            trigger_source=trigger_source,
            session_id=session_id,
            run_id=run_id,
            user_id=f"system_{prefix}",
            project_id=scope_result.binding.project_id,
            workspace_id=scope_result.binding.workspace_id,
            resolved_scope=scope_result.binding.resolved_scope,
        )

        with bind_runtime_context(**runtime_context):
            if main_loop and main_loop.is_running():
                logger.info(f"Submitting to main event loop via astream_events (timeout={timeout_seconds}s)...")
                future = asyncio.run_coroutine_threadsafe(_run_graph(), main_loop)
                result = future.result(timeout=timeout_seconds)
            else:
                # Fallback: CLI manual run
                logger.warning("No main event loop available, falling back to asyncio.run()...")
                result = asyncio.run(_run_graph())

        if result:
            logger.info(f"Supervisor response (truncated): {result[:500]}")
            from api.routes import _get_agent_profile
            agent_profile = _get_agent_profile("supervisor")
            assistant_message_id = str(uuid.uuid4())
            db.add_message(
                msg_id=assistant_message_id,
                session_id=session_id,
                role="assistant",
                content=result,
                metadata={
                    "run_id": run_id,
                    "trigger_source": trigger_source,
                    "automation_id": str(automation_id),
                    "project_id": scope_result.binding.project_id,
                    "workspace_id": scope_result.binding.workspace_id,
                    "resolved_scope": scope_result.binding.resolved_scope,
                },
                agent_id="supervisor",
                agent_name=agent_profile["name"],
                agent_avatar=agent_profile["avatar"],
                agent_role_label=agent_profile["roleLabel"]
            )
            _persist_runtime_event(
                "run.text.delta",
                {"type": "text_chunk", "content": result, "message_id": assistant_message_id},
                node="supervisor",
            )

        updated_messages = db.get_messages(session_id)
        updated_recent_summaries = build_recent_run_summaries(updated_messages, limit=recent_limit)
        updated_job_memory = build_job_memory(updated_messages, limit=job_memory_limit)
        db.update_session_metadata(
            session_id,
            {
                "source": prefix,
                "automation_id": str(automation_id),
                "task_name": task_description,
                "channel_id": channel_id,
                "job_memory": updated_job_memory,
                "recent_run_summaries": updated_recent_summaries,
                "project_id": scope_result.binding.project_id,
                "workspace_id": scope_result.binding.workspace_id,
                "workspace_path": scope_result.binding.workspace_path,
                "resolved_scope": scope_result.binding.resolved_scope,
                "scope_source": scope_result.binding.scope_source,
            },
        )
        if created_local_run:
            run_handle.complete(reason="automation_finished", node="automation_runtime")
        else:
            run_handle.refresh_chat_snapshot()

        logger.info("Supervisor Cron Task completed successfully.")
        return result

    except GraphStreamIdleTimeoutError as e:
        logger.error(f"Supervisor Cron Task graph stream stalled: {e}")
        if run_id and 'run_handle' in locals() and run_handle is not None and 'created_local_run' in locals() and created_local_run:
            run_handle.fail(str(e), node="automation_runtime")
        raise
    except TimeoutError:
        logger.error(f"Supervisor Cron Task timed out after {timeout_seconds}s")
        try:
            if run_id and 'run_handle' in locals() and run_handle is not None and 'created_local_run' in locals() and created_local_run:
                run_handle.fail(f"Supervisor Cron Task timed out after {timeout_seconds}s", node="automation_runtime")
        except Exception:
            pass
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Supervisor Cron Task failed: {e}")
        try:
            if not session_id or not run_id:
                raise RuntimeError("cron runtime session not initialized")
            if 'run_handle' in locals() and run_handle is not None and 'created_local_run' in locals() and created_local_run:
                run_handle.fail(str(e), node="automation_runtime")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    # For manual testing (no main loop running, will use asyncio.run fallback)
    run(payload={"task": "请汇报当前系统状态"})

