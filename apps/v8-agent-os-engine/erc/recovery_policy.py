from __future__ import annotations

from typing import Any, Dict, Optional


def _normalize_run_type(run_record: Dict[str, Any] | None) -> str:
    return str((run_record or {}).get("run_type") or "").strip().lower()


def _normalize_metadata(run_record: Dict[str, Any] | None) -> Dict[str, Any]:
    return dict((run_record or {}).get("metadata") or {})


def derive_recovery_class(
    run_record: Dict[str, Any] | None,
    *,
    workflow_view: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    workflow_view = workflow_view or {}
    run_type = _normalize_run_type(run_record)
    metadata = _normalize_metadata(run_record)
    action_type = str(metadata.get("action_type") or "").strip().lower()
    trigger_source = str((run_record or {}).get("trigger_source") or metadata.get("trigger_source") or "").strip().lower()
    workflow_status = str(workflow_view.get("status") or "").strip().lower()
    run_status = str((run_record or {}).get("status") or "").strip().lower()

    recovery_class = "resubmit_only"
    reason = "该运行类型不支持真正 resume，请重新发起请求。"

    if run_type == "chat":
        recovery_class = "resume_supported"
        reason = "聊天运行支持在等待输入、等待外部工具、暂停或人工确认后继续。"
    elif run_type == "automation_agent" or (run_type == "automation" and action_type == "agent"):
        recovery_class = "resume_supported"
        reason = "基于 Agent 的自动化任务支持继续执行。"
    elif run_type in {
        "automation",
        "memory",
        "computer_use",
        "rpa",
        "cron_task",
        "hook_task",
    } or trigger_source == "cron" or trigger_source.startswith("hook"):
        recovery_class = "retry_only"
        reason = "当前运行支持重试，但不保证从原中断点继续。"

    can_resume = recovery_class == "resume_supported" and (
        run_status in {"paused", "waiting_approval", "waiting_input", "waiting_external_tool"}
        or workflow_status in {"paused", "waiting_approval", "waiting_external_tool"}
    )
    can_retry = recovery_class in {"resume_supported", "retry_only"} and run_status in {
        "failed",
        "recoverable_failed",
        "cancelled",
        "interrupted",
        "paused",
        "waiting_input",
        "waiting_approval",
        "waiting_external_tool",
    }
    can_resubmit = recovery_class == "resubmit_only" or not (can_resume or can_retry)

    return {
        "class": recovery_class,
        "reason": reason,
        "runType": run_type or None,
        "workflowStatus": workflow_status or None,
        "runStatus": run_status or None,
        "resumeSupported": recovery_class == "resume_supported",
        "canResume": bool(can_resume),
        "canRetry": bool(can_retry),
        "canResubmit": bool(can_resubmit),
    }
