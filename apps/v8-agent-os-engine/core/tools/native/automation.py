from __future__ import annotations

import time
import uuid
from typing import Annotated

import psutil
from langchain_core.tools import InjectedToolCallId, tool

from core.tools.native.tool_governance import (
    _enforce_safety_decision,
    _raise_runtime_governance_exception_if_needed,
)
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import safety_guardian

__all__ = [
    "wait",
    "list_processes",
    "manage_process",
    "manage_cron",
    "manage_hook",
    "read_audit_log",
]


@tool
def wait(seconds: int, note: str = "") -> str:
    """Pause briefly for a bounded number of seconds, then continue with an optional reminder note.

    Good for:
    - Re-checking a just-submitted async task after a short delay
    - Giving installs, service startup, or file generation a short stabilization window
    - Polling a command/session/result that is expected to finish soon

    Not for:
    - Unbounded waiting
    - Managing long-running background processes
    - Scheduled or recurring work; use `manage_cron` only when the user asks for timed/recurring automation
    - Lifecycle event automation; use `manage_hook` only when the user explicitly asks to change hooks

    Arguments:
        seconds (int): Number of seconds to wait. Must be between 1 and 120.
        note (str, optional): Short reminder for what to do after waking up.
    """
    try:
        normalized_seconds = int(seconds)
    except Exception:
        return "wait 工具参数错误：seconds 必须是 1 到 120 之间的整数。"

    if normalized_seconds < 1 or normalized_seconds > 120:
        return (
            "wait 工具参数错误：seconds 仅允许 1 到 120 秒。"
            "如果需要更久，请拆成多次短等待。"
        )

    normalized_note = str(note or "").strip()
    if len(normalized_note) > 120:
        normalized_note = normalized_note[:120].rstrip()

    try:
        time.sleep(normalized_seconds)
    except Exception as e:
        return f"wait 工具执行失败：{str(e)}"

    if normalized_note:
        return f"已等待 {normalized_seconds} 秒。备注：{normalized_note}"
    return f"已等待 {normalized_seconds} 秒。"


@tool
def list_processes(name_pattern: str = None, port: int = None) -> str:
    """List running processes on the host machine.

    Arguments:
        name_pattern (str, optional): Substring to match in process name or command line.
        port (int, optional): Filter processes listening on this specific port.
    """
    try:
        results = []
        for p in psutil.process_iter(['pid', 'name', 'status', 'create_time', 'cmdline']):
            try:
                if name_pattern:
                    name = p.info.get('name', '') or ''
                    cmdline = " ".join(p.info.get('cmdline', []) or [])
                    if name_pattern.lower() not in name.lower() and name_pattern.lower() not in cmdline.lower():
                        continue

                if port:
                    found_port = False
                    for conn in p.connections(kind='inet'):
                        if conn.laddr.port == port:
                            found_port = True
                            break
                    if not found_port:
                        continue

                results.append(f"PID: {p.pid} | Name: {p.info.get('name')} | Status: {p.info.get('status')}")
                if len(results) >= 50:
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        if not results:
            return "No matching processes found."

        output = "\n".join(results)
        if len(results) >= 50:
            output += "\n...[TRUNCATED 50 MATCHES MAX]"
        return output
    except Exception as e:
        return f"Error listing processes: {str(e)}"


@tool
def manage_process(
    pid: int,
    action: str,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Manage a running process by its PID.

    Arguments:
        pid (int): The process ID.
        action (str): The action to perform (must be 'kill' or 'terminate').
    """
    try:
        runtime_context = get_runtime_context()
        allowed, error_message = _enforce_safety_decision(
            safety_guardian.assess_process_action(pid, action, runtime_context=runtime_context),
            tool_call_id=tool_call_id,
            question=f"Safety Guardian 检测到进程操作需要确认，是否继续？\n\n动作：{action}\nPID：{pid}",
        )
        if not allowed:
            return error_message or "Safety Guardian 已阻止进程操作。"

        p = psutil.Process(pid)
        if action.lower() == 'kill':
            p.kill()
            safety_guardian.observe_post_action(
                action_family="process",
                summary=f"已强制结束进程：{pid}",
                details={"pid": pid, "action": action, "name": p.name()},
                runtime_context=runtime_context,
            )
            return f"Successfully killed process {pid} ({p.name()})."
        elif action.lower() == 'terminate':
            p.terminate()
            p.wait(timeout=3)
            safety_guardian.observe_post_action(
                action_family="process",
                summary=f"已终止进程：{pid}",
                details={"pid": pid, "action": action, "name": p.name()},
                runtime_context=runtime_context,
            )
            return f"Successfully terminated process {pid} ({p.name()})."
        else:
            return f"Invalid action: {action}. Must be 'kill' or 'terminate'."
    except psutil.NoSuchProcess:
        return f"Process with PID {pid} not found."
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error managing process: {str(e)}"


@tool
def manage_cron(
    action: str,
    job_id: str = None,
    expression: str = None,
    target: str = None,
    action_type: str = None,
    payload: dict = None,
    name: str = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Manage scheduled cron tasks in the V8Chat Engine.

    Use this only when the user explicitly asks for timed, delayed, recurring,
    or scheduled automation. Do not use cron as a substitute for `wait` during a
    normal chat turn, and do not create background jobs just because a task is
    long-running.

    Arguments:
        action (str): "list", "add", or "remove".
        job_id (str, optional): The ID of the job to remove, or a new unique ID to add.
        expression (str, optional): Standard 5-part cron expression (e.g. "0 11 * * *" for daily 11am).
        target (str, optional): The execution target. Format depends on action_type:
            - action_type="command": A shell command string (e.g. "python script.py", "echo hello").
            - action_type="python": A dotted Python module path with a `run()` function (e.g. "apps.engine.scripts.cron_nightly_memo").
            - action_type="agent": A dotted Python module path containing a LangGraph `compiled_graph` (e.g. "agents.memory_agent").
              NOTE: This must be a valid importable Python module, NOT a display name or .md filename.
            - action_type="rpa": An RPA template/draft/script/robot target (e.g. "template:github-star", "draft:<id>", "path/to/flow.robot").
        action_type (str, optional): "command", "python", "agent", or "rpa". Auto-inferred from target if omitted.
        payload (dict, optional): Keyword arguments or standard input for the target.
        name (str, optional): Human readable display name for the task.

    IMPORTANT - To schedule tasks that require the Supervisor team (with sub-agents and all tools):
        Use action_type="agent", target="supervisor", and put the task description in payload:
        Example: manage_cron(action="add", job_id="daily-news", expression="0 11 * * *",
                 target="supervisor", action_type="agent", name="每日新闻简报",
                 payload={"task": "搜索今天的科技新闻头条，生成简报", "channel_id": "weixin"})
        NOTE: You can pass a `channel_id` (例如插件声明的渠道标识，如 "weixin") inside the `payload` dictionary to automatically broadcast the finished task summary to that channel.
    """
    try:
        if action in {"add", "remove"}:
            allowed, error_message = _enforce_safety_decision(
                safety_guardian.assess_cron_mutation(action, runtime_context=get_runtime_context()),
                tool_call_id=tool_call_id,
                question=f"Safety Guardian 检测到定时任务配置变更，是否继续？\n\n动作：{action}\n任务：{job_id or name or target or 'unknown'}",
            )
            if not allowed:
                return error_message or "Safety Guardian 已阻止定时任务变更。"

        from core.storage import storage
        from core.cron_manager import cron_manager

        config = storage.get_cron_config()
        jobs = config.get("jobs", [])

        if action == "list":
            if not jobs:
                return "No cron jobs scheduled."
            ret = []
            for j in jobs:
                ret.append(f"[{j.get('id')}] {j.get('name')} | {j.get('cron_expression')} | Target: {j.get('action_target')} ({j.get('action_type', '?')})")
            return "\n".join(ret)

        elif action == "add":
            if not job_id or not expression or not target or not name:
                return "Missing required arguments for 'add' action."

            if action_type in ["command", "python", "agent", "rpa", "rpa_runtime"]:
                inferred_type = action_type
            elif target.startswith(("rpa:", "template:", "draft:", "script:", "robot:")) or target.endswith(".robot"):
                inferred_type = "rpa"
            elif target.startswith("agents.") or target.startswith("graph."):
                inferred_type = "agent"
            elif "." in target:
                inferred_type = "python"
            else:
                inferred_type = "command"

            new_job = {
                "id": job_id,
                "name": name,
                "cron_expression": expression,
                "action_type": inferred_type,
                "action_target": target,
                "payload": payload or {},
                "enabled": True
            }
            jobs.append(new_job)
            storage.save_cron_config({"jobs": jobs})
            cron_manager.sync_jobs_to_scheduler()
            safety_guardian.observe_post_action(
                action_family="cron_mutation",
                summary=f"已新增定时任务：{job_id}",
                details={"action": action, "job_id": job_id, "expression": expression, "target": target, "action_type": inferred_type},
                runtime_context=get_runtime_context(),
            )
            return f"Successfully added cron job '{name}' (type={inferred_type}, target={target})."

        elif action == "remove":
            if not job_id:
                return "Missing job_id for 'remove' action."

            filtered_jobs = [j for j in jobs if j.get("id") != job_id]
            if len(filtered_jobs) == len(jobs):
                return f"Job with ID '{job_id}' not found."

            storage.save_cron_config({"jobs": filtered_jobs})
            cron_manager.sync_jobs_to_scheduler()
            safety_guardian.observe_post_action(
                action_family="cron_mutation",
                summary=f"已删除定时任务：{job_id}",
                details={"action": action, "job_id": job_id},
                runtime_context=get_runtime_context(),
            )
            return f"Successfully removed cron job '{job_id}'."
        else:
            return "Invalid action."
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error managing cron: {str(e)}"


@tool
def manage_hook(
    action: str,
    event: str = None,
    target: str = None,
    action_type: str = None,
    name: str = None,
    payload: dict = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Manage lifecycle event hooks in the V8Chat Engine.

    Use this only when the user explicitly asks to inspect or change lifecycle
    event behavior such as on_chat_end/on_agent_start automation. Ordinary chat,
    Spec, runtime, memory lookup, or task execution should not mutate hooks.

    Arguments:
        action (str): "list" or "add".
        event (str, optional): The engine event to hook into (e.g. "on_chat_end", "on_agent_start").
        target (str, optional): The execution target. Format depends on action_type:
            - action_type="command": A shell command string.
            - action_type="python": A dotted Python module path with a `run()` function.
            - action_type="agent": A dotted Python module path containing a LangGraph `compiled_graph` (e.g. "agents.memory_agent").
              NOTE: This must be a valid importable Python module, NOT a display name or .md filename.
            - action_type="rpa": An RPA template/draft/script/robot target (e.g. "template:github-star", "draft:<id>", "path/to/flow.robot").
        action_type (str, optional): "command", "python", "agent", or "rpa". Auto-inferred from target if omitted.
        name (str, optional): Human readable display name for the hook.
        payload (dict, optional): Runtime input, variables, or execution options for the target.
    """
    try:
        if action == "add":
            allowed, error_message = _enforce_safety_decision(
                safety_guardian.assess_hook_mutation(action, runtime_context=get_runtime_context()),
                tool_call_id=tool_call_id,
                question=f"Safety Guardian 检测到生命周期 Hook 变更，是否继续？\n\n事件：{event}\n目标：{target}",
            )
            if not allowed:
                return error_message or "Safety Guardian 已阻止 Hook 变更。"

        from core.storage import storage
        config = storage.get_hooks_config()
        hooks = config.get("hooks", [])

        if action == "list":
            if not hooks:
                return "No hooks configured."
            ret = []
            for h in hooks:
                evs = h.get('events', [])
                ret.append(f"[{h.get('name')}] Events: {evs} | Target: {h.get('target')} ({h.get('type', '?')})")
            return "\n".join(ret)

        elif action == "add":
            if not event or not target or not name:
                return "Missing required arguments for 'add' action."

            if action_type in ["command", "python", "agent", "rpa", "rpa_runtime"]:
                inferred_type = action_type
            elif target.startswith(("rpa:", "template:", "draft:", "script:", "robot:")) or target.endswith(".robot"):
                inferred_type = "rpa"
            elif target.startswith("agents.") or target.startswith("graph."):
                inferred_type = "agent"
            elif "." in target:
                inferred_type = "python"
            else:
                inferred_type = "command"

            new_hook = {
                "id": str(uuid.uuid4()),
                "name": name,
                "events": [event],
                "type": inferred_type,
                "target": target,
                "payload": payload or {},
                "async": True,
                "enabled": True
            }
            hooks.append(new_hook)
            storage.save_hooks_config({"hooks": hooks})
            safety_guardian.observe_post_action(
                action_family="hook_mutation",
                summary=f"已新增 Hook：{name}",
                details={"action": action, "event": event, "target": target, "action_type": inferred_type},
                runtime_context=get_runtime_context(),
            )
            return f"Successfully added hook '{name}' for event '{event}' (type={inferred_type}, target={target})."
        else:
             return "Invalid action. Only 'list' and 'add' are supported."
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error managing hooks: {str(e)}"


@tool
def read_audit_log(limit: int = 5, source_type: str = None, status: str = None) -> str:
    """Read the system audit log to check the execution results of background tasks, chron jobs, and hooks.

    Arguments:
        limit (int): Maximum number of log entries to retrieve. Defaults to 5. Maximum 50.
        source_type (str, optional): Filter by source type (e.g. 'CRON', 'HOOK', 'SYSTEM').
        status (str, optional): Filter by status (e.g. 'SUCCESS', 'ERROR', 'SKIPPED').
    """
    try:
        from core.audit_logger import audit_logger

        limit = min(max(1, limit), 50)
        logs = audit_logger.get_logs(limit=limit, source_type=source_type, status=status)

        if not logs:
            return "No audit logs found matching the criteria."

        results = []
        for log in logs:
            ts = log.get('timestamp', '')
            src = log.get('source_type', 'UNKNOWN')
            act = log.get('action', '')
            st = log.get('status', '')
            det = log.get('details') or ''
            results.append(f"[{ts}] [{src}] {act} - {st}: {det}")

        return "\n".join(results)
    except Exception as e:
        return f"Error reading audit log: {str(e)}"

