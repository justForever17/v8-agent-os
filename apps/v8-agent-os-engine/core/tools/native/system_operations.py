from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Annotated, Literal

from langchain_core.tools import InjectedToolCallId, tool

from core.security.credentials import CredentialStoreError
from core.system_operations.service import SystemOperationError, system_operation_service
from core.tools.native.tool_governance import _enforce_safety_decision
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import SafetyDecision, safety_guardian


@tool
def system_operations(
    action: Literal["status", "unlock", "run_privileged"] = "status",
    command: str = "",
    cwd: str = "",
    shell_dialect: str = "auto",
    timeout_seconds: int = 90,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Controlled OS operations for the Supervisor, independent of Computer Use.

    status reports actual platform readiness. unlock unlocks the current local
    console session using the user's configured OS credential; it does not type
    a password or control a desktop. run_privileged executes the exact specified
    command with elevated OS authority. Use ordinary run_system_command unless
    elevation is actually needed. Never supply or ask for passwords in messages,
    command arguments, environment or clipboard: the user configures them in
    Admin > System settings > Controlled system operations.

    Unlock and elevation use separate credentials and one-time approvals. Manual
    and reduced modes ask the user via the existing Web/Phone approval card;
    minimal mode respects the user's authorization for ordinary operations.
    OS/V8 core, authentication secrets, malicious actions, and the existing
    workspace/write contracts remain protected. Only a verified OS result counts
    as success. If an outcome is unknown, inspect status; never blindly replay.
    """
    context = get_runtime_context()
    try:
        if action == "status":
            owner = str(context.get("user_id") or context.get("userId") or "")
            settings = system_operation_service.settings(owner)
            return json.dumps({"platform": settings["platform"], "credentials": {key: value["configured"] for key, value in settings["profiles"].items()}}, ensure_ascii=False)
        if not 5 <= timeout_seconds <= 600:
            raise SystemOperationError("system_operation_deadline_invalid", "操作期限须在 5 至 600 秒之间。", 422)
        if action == "unlock" and (command or cwd):
            raise SystemOperationError("unlock_command_conflict", "解锁与执行命令是不同动作，请分别调用。", 422)
        payload: dict = {"action": action}
        if action == "run_privileged":
            if not command.strip() or len(command) > 32768 or "\x00" in command:
                raise SystemOperationError("system_command_invalid", "请提供完整、有效的指定命令。", 422)
            from core.tools.native.command import _engineering_command_scope_block, _resolve_shell_dialect, _shell_command_argv, _sandbox_launch
            from core.workspace_capability import preflight_command_workspace
            capsule_block = _engineering_command_scope_block(context, operation="system_operation", command=command)
            if capsule_block:
                return json.dumps(capsule_block, ensure_ascii=False)
            workspace = preflight_command_workspace(command, cwd=cwd or None, runtime_context=context)
            if not workspace.get("ok"):
                return json.dumps(workspace, ensure_ascii=False)
            dialect = _resolve_shell_dialect(command, shell_dialect)
            # Keep argv typed until the native platform serializes it. The
            # ordinary Popen helper returns a raw string for Windows cmd.exe.
            argv, _ = _sandbox_launch(context, _shell_command_argv(command, dialect))
            executable = shutil.which(argv[0])
            if not executable:
                raise SystemOperationError("system_shell_unavailable", "当前命令环境的 shell 不存在。")
            argv[0] = str(Path(executable).resolve(strict=True))
            resolved_cwd = str(Path(workspace.get("cwd") or Path.cwd()).resolve(strict=True))
            payload.update({"command": command, "argv": argv, "cwd": resolved_cwd, "timeoutSeconds": timeout_seconds})

        def authorize(operation: dict) -> None:
            assessment_context = {**context, "command_cwd": payload.get("cwd", "")}
            decision = safety_guardian.assess_system_command(command, runtime_context=assessment_context) if action == "run_privileged" else SafetyDecision()
            # Core/explicit deny remains a deny. All remaining privileged actions
            # ask once in manual/reduced mode, even if the ordinary command allows.
            if decision.is_allow():
                decision = SafetyDecision(verdict="review", risk_code="controlled_system_operation", governance_target="system_operation")
            decision.details = {**(decision.details or {}), "runtime_context": assessment_context, "operationId": operation["operationId"], "action": action, "command": command or "unlock current console", "cwd": payload.get("cwd", ""), "expiresAt": operation["expiresAt"]}
            allowed, reason = _enforce_safety_decision(
                decision, tool_call_id=tool_call_id,
                question=("允许解锁当前电脑屏幕吗？" if action == "unlock" else f"允许以系统权限执行以下命令吗？\n\n{command}"),
            )
            if not allowed:
                raise SystemOperationError("system_operation_blocked", reason or "该操作被具体系统边界阻止。", 403)

        result = system_operation_service.execute(payload=payload, context=context, tool_call_id=tool_call_id, authorize=authorize)
        return json.dumps(result, ensure_ascii=False)
    except SystemOperationError as exc:
        return json.dumps({"ok": False, "code": exc.code, "summary": str(exc)}, ensure_ascii=False)
    except CredentialStoreError:
        return json.dumps({"ok": False, "code": "system_credential_unavailable", "summary": "系统凭据不可用，请在控制台重新配置。"}, ensure_ascii=False)
    except (OSError, ValueError):
        return json.dumps({"ok": False, "code": "system_command_environment_invalid", "summary": "命令环境、工作目录或 shell 参数无效，未确认执行。"}, ensure_ascii=False)
