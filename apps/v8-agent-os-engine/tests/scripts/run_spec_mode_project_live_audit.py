from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[2]
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
DEFAULT_WORKSPACE = Path("E:/Projects/test3")
DEFAULT_MODEL_PROFILE = ""
DEFAULT_SAFETY_APPROVAL_MODE = "reduced"
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))
ADMIN_CONFIGURED_MODEL_SENTINELS = {"", "default", "engine-default", "admin-configured", "admin-configured-supervisor"}
WORKSPACE_BLOCKER_CODES = {
    "workspace_binding_required",
    "workspace_trust_required",
    "workspace_side_effect_blocked",
}
PSEUDO_TOOL_CALL_MARKERS = ("<tool_call", "<invoke name=", "</tool_call>", "</invoke>")
TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "canceled"}
ACTIVE_QUEUE_STATES = ["pending", "promoted", "queued"]
ACTIVE_RUNTIME_EPISODE_STATES = {
    "detected",
    "routed",
    "queued",
    "leased",
    "active",
    "waiting",
    "waiting_child",
    "waiting_external",
    "waiting_approval",
}

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


@dataclass
class StageObservation:
    stage: str
    found: bool = False
    approved: bool = False
    path: str = ""
    content_chars: int = 0
    quality_findings: list[str] = field(default_factory=list)


@dataclass
class SpecLiveResult:
    status: str = "pending"
    session_id: str = ""
    run_ids: list[str] = field(default_factory=list)
    model_profile: str = ""
    model_override: bool = False
    safety_approval_mode: str = DEFAULT_SAFETY_APPROVAL_MODE
    spec_id: str = ""
    spec_dir: str = ""
    spec_bootstrap: dict[str, Any] = field(default_factory=dict)
    marker: str = ""
    target_dir: str = ""
    workspace_preflight: list[dict[str, str]] = field(default_factory=list)
    submit_latencies_ms: list[int] = field(default_factory=list)
    stages: list[StageObservation] = field(default_factory=list)
    episode_kinds: list[str] = field(default_factory=list)
    handoff_kinds: list[str] = field(default_factory=list)
    output_files: dict[str, dict[str, Any]] = field(default_factory=dict)
    findings: list[dict[str, str]] = field(default_factory=list)
    key_events: list[dict[str, Any]] = field(default_factory=list)
    ask_user_responses: list[dict[str, Any]] = field(default_factory=list)
    seen_runtime_event_keys: list[str] = field(default_factory=list)


def _json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def _engine_api_base(engine_url: str) -> str:
    base = engine_url.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def _engine_root_url(engine_url: str) -> str:
    base = engine_url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _normalized_model_profile(model_profile: str | None) -> str:
    return str(model_profile or "").strip()


def _uses_model_override(model_profile: str | None) -> bool:
    profile = _normalized_model_profile(model_profile)
    return bool(profile and profile.lower() not in ADMIN_CONFIGURED_MODEL_SENTINELS)


def _effective_model_label(model_profile: str | None) -> str:
    profile = _normalized_model_profile(model_profile)
    return profile if _uses_model_override(profile) else "admin-configured supervisor model"


def _workspace_preflight(workspace: Path) -> list[dict[str, str]]:
    if not workspace.exists():
        return [
            {
                "severity": "P0",
                "code": "workspace_missing",
                "summary": f"工作区不存在：{workspace}。请先创建/选择并信任项目工作区后再运行 live 闭环。",
            }
        ]
    if not workspace.is_dir():
        return [
            {
                "severity": "P0",
                "code": "workspace_not_directory",
                "summary": f"工作区不是目录：{workspace}。请改用已信任的项目目录。",
            }
        ]
    return []


def _ensure_live_workspace_trusted(workspace: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    try:
        from core.workspace_authority import workspace_authority_service
        from runtimes.memory.project_registry import project_registry_service
    except Exception as exc:  # noqa: BLE001
        return (
            [
                {
                    "severity": "P0",
                    "code": "workspace_trust_preflight_unavailable",
                    "summary": f"无法加载工作区信任服务：{type(exc).__name__}: {exc}",
                }
            ],
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
        )

    workspace_path = str(workspace)
    event: dict[str, Any] = {"workspacePath": workspace_path, "action": "unchanged"}
    try:
        project = project_registry_service.find_project_for_workspace(workspace_path=workspace_path)
        trust_state = str(getattr(project, "workspace_trust_state", "") or "").strip().lower() if project else ""
        if project is None or trust_state != "trusted":
            project = project_registry_service.save_project(
                {
                    "name": "Spec live harness workspace",
                    "workspacePath": workspace_path,
                    "workspaceTrustState": "trusted",
                    "workspaceTrustSource": "user_confirmed",
                    "tags": ["live_harness", "spec_mode"],
                }
            )
            event["action"] = "registered_trusted_project"
        else:
            event["action"] = "already_trusted"
        event["projectId"] = str(getattr(project, "project_id", "") or "")
        event["workspaceId"] = str(getattr(project, "workspace_id", "") or "")
        event["trustState"] = str(getattr(project, "workspace_trust_state", "") or "")
        event["trustSource"] = str(getattr(project, "workspace_trust_source", "") or "")
        authority = workspace_authority_service.resolve(
            runtime_kind="engineering",
            explicit_workspace_path=workspace_path,
        )
        authority_payload = authority.as_dict() if hasattr(authority, "as_dict") else dict(authority or {})
        event["authority"] = {
            "sideEffectsAllowed": bool(authority_payload.get("sideEffectsAllowed")),
            "trustState": authority_payload.get("trustState"),
            "trustSource": authority_payload.get("trustSource"),
            "source": authority_payload.get("source"),
            "projectId": authority_payload.get("projectId"),
            "workspaceId": authority_payload.get("workspaceId"),
        }
        if not bool(authority_payload.get("sideEffectsAllowed")):
            return (
                [
                    {
                        "severity": "P0",
                        "code": "workspace_trust_preflight_failed",
                        "summary": f"工作区未获得副作用权限，不能执行真实 live：{workspace_path}",
                    }
                ],
                event,
            )
    except Exception as exc:  # noqa: BLE001
        event["error"] = f"{type(exc).__name__}: {exc}"
        return (
            [
                {
                    "severity": "P0",
                    "code": "workspace_trust_preflight_failed",
                    "summary": f"工作区信任绑定失败：{type(exc).__name__}: {exc}",
                }
            ],
            event,
        )
    return [], event


def _find_workspace_blocker_code(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("error", "code", "kind", "reason", "type"):
            text = str(value.get(key) or "").strip()
            if text in WORKSPACE_BLOCKER_CODES:
                return text
        for child in value.values():
            code = _find_workspace_blocker_code(child)
            if code:
                return code
    elif isinstance(value, list):
        for child in value:
            code = _find_workspace_blocker_code(child)
            if code:
                return code
    elif isinstance(value, str):
        for code in WORKSPACE_BLOCKER_CODES:
            if code in value:
                return code
    return ""


def _http_error_payload(exc: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    try:
        body: Any = json.loads(raw) if raw.strip() else {}
    except Exception:
        body = raw
    return {"status": exc.code, "reason": exc.reason, "body": body}


def _finding_exists(result: SpecLiveResult, code: str) -> bool:
    return any(str(item.get("code") or "") == code for item in result.findings)


def _clear_findings(result: SpecLiveResult, code: str) -> None:
    result.findings = [item for item in result.findings if str(item.get("code") or "") != code]


def _has_key_event(result: SpecLiveResult, key: str) -> bool:
    return any(isinstance(item, dict) and key in item for item in result.key_events)


def _has_spec_clarification_required(result: SpecLiveResult, stage: str) -> bool:
    normalized_stage = str(stage or "").strip().lower()
    for item in result.key_events:
        if not isinstance(item, dict):
            continue
        event = item.get("specClarificationRequired")
        if not isinstance(event, dict):
            continue
        event_stage = str(event.get("stage") or "").strip().lower()
        if not event_stage or event_stage == normalized_stage:
            return True
    return False


def _record_workspace_blocker(result: SpecLiveResult, code: str, evidence: Any) -> None:
    summaries = {
        "workspace_binding_required": "用户主动任务缺少明确 workspace binding；本轮不会回退到主工作区执行。",
        "workspace_trust_required": "外部工作区尚未被信任；请在 Web/Phone/Admin 选择并确认信任该项目工作区后重试。",
        "workspace_side_effect_blocked": "当前工作区处于 fallback/restricted 状态，命令、写文件、上传或外部 worker 等副作用已被阻断。",
    }
    if not _finding_exists(result, code):
        result.findings.append(
            {
                "severity": "P0",
                "code": code,
                "summary": summaries.get(code, f"工作区治理阻断：{code}"),
            }
        )
    result.key_events.append(
        {
            "workspaceBlocker": {
                "code": code,
                "evidence": json.dumps(evidence, ensure_ascii=False, default=str)[:1600],
            }
        }
    )


def _contains_pseudo_tool_call(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_pseudo_tool_call(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_pseudo_tool_call(child) for child in value)
    if isinstance(value, str):
        text = value.lower()
        return any(marker in text for marker in PSEUDO_TOOL_CALL_MARKERS)
    return False


def _record_pseudo_tool_call_observed(result: SpecLiveResult, evidence: Any) -> None:
    if not _has_key_event(result, "modelPseudoToolCallObserved"):
        result.key_events.append(
            {
                "modelPseudoToolCallObserved": {
                    "evidence": json.dumps(evidence, ensure_ascii=False, default=str)[:1600],
                }
            }
        )


def _record_pseudo_tool_call_failure(result: SpecLiveResult, evidence: Any) -> None:
    if not _finding_exists(result, "model_pseudo_tool_call_not_executed"):
        result.findings.append(
            {
                "severity": "P0",
                "code": "model_pseudo_tool_call_not_executed",
                "summary": "模型把工具调用写成了正文伪工具块，Engine 未执行真实 spec_broker tool call。",
            }
        )
    if not _has_key_event(result, "modelPseudoToolCall"):
        result.key_events.append(
            {
                "modelPseudoToolCall": {
                    "evidence": json.dumps(evidence, ensure_ascii=False, default=str)[:1600],
                }
            }
        )


def _maybe_record_pseudo_tool_call_failure(result: SpecLiveResult, event: dict[str, Any], payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    topic = str(event.get("topic") or "").strip()
    if topic != "extension.execution.completed":
        return
    if payload.get("hasToolCalls") is not False:
        return
    if not _has_key_event(result, "modelPseudoToolCallObserved"):
        return
    _record_pseudo_tool_call_failure(
        result,
        {
            "topic": topic,
            "eventType": event.get("event_type") or event.get("eventType"),
            "payload": payload,
        },
    )


def _event_can_contain_model_pseudo_tool_call(event: dict[str, Any], payload: Any) -> bool:
    topic = str(event.get("topic") or "").strip().lower()
    if topic == "extension.execution.completed":
        return True
    if not (topic.startswith("message.assistant") or topic.startswith("assistant.") or topic.startswith("model.")):
        return False
    if topic.startswith("message.user") or topic.startswith("chat.user") or topic == "user.message.recorded":
        return False
    if isinstance(payload, dict):
        role = str(payload.get("role") or payload.get("messageRole") or "").strip().lower()
        if role == "user":
            return False
        message = payload.get("message")
        if isinstance(message, dict) and str(message.get("role") or "").strip().lower() == "user":
            return False
    return True


def _remember_run_ids(result: SpecLiveResult, value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"runId", "run_id", "scheduledRunId", "resumed_run_id", "next_run_id"}:
                run_id = str(child or "").strip()
                if run_id and run_id not in result.run_ids:
                    result.run_ids.append(run_id)
            else:
                _remember_run_ids(result, child)
    elif isinstance(value, list):
        for child in value:
            _remember_run_ids(result, child)


def _interaction_request(interaction: dict[str, Any]) -> dict[str, Any]:
    request = interaction.get("request") if isinstance(interaction.get("request"), dict) else {}
    if request:
        return request
    raw = interaction.get("request_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _spec_clarification_context(interaction: dict[str, Any]) -> dict[str, Any]:
    request = _interaction_request(interaction)
    context = request.get("specContext") if isinstance(request.get("specContext"), dict) else {}
    kind = str(context.get("kind") or context.get("contextKind") or "").strip().lower()
    if kind not in {"spec_clarification", "spec-clarification"}:
        return {}
    return context


def _looks_like_spec_stage_question(interaction: dict[str, Any], stage: str) -> bool:
    request = _interaction_request(interaction)
    text = " ".join(
        str(value or "")
        for value in (
            interaction.get("question"),
            interaction.get("prompt"),
            request.get("question"),
            request.get("prompt"),
            request.get("details"),
        )
    ).lower()
    stage_text = str(stage or "").strip().lower()
    stage_tokens = {
        "requirements": ("requirements", "requirement", "需求", "边界"),
        "design": ("design", "设计"),
        "tasks": ("tasks", "task", "任务"),
    }.get(stage_text, (stage_text,))
    has_stage_token = any(token and token in text for token in stage_tokens)
    has_spec_marker = "spec" in text or "规格" in text or "需求" in text
    has_clarification_marker = any(token in text for token in ("阶段", "确认", "对齐", "澄清", "微决策", "边界"))
    return has_stage_token and (has_spec_marker or has_clarification_marker)


def _spec_clarification_answer(*, stage: str, marker: str, target_rel: str) -> str:
    normalized_stage = str(stage or "").strip().lower() or "requirements"
    base = (
        f"确认本轮 live Spec 的边界：只在目标目录 `{target_rel}` 下交付一个极小静态项目；"
        f"所有 Spec 文档与最终产物必须包含 marker `{marker}`；最终文件为 `index.html` 和 `README.md`；"
        "`index.html` 必须是标题为 “Spec Mode Live Counter” 的可交互计数器，按钮点击后计数 +1；"
        "`README.md` 说明打开和验收方式。不要扩展成审计面板，不要写出目标目录之外。"
    )
    stage_notes = {
        "requirements": "需求阶段请明确 REQ-###、交付文件、验收标准和非目标范围。",
        "design": "设计阶段请说明文件结构、交互实现、验证方式和如何保持最小静态页面。",
        "tasks": "任务阶段请拆成可执行任务，每项写 specRefs、proof、mvpSlice 和 independentAcceptance。",
    }
    return f"{base} {stage_notes.get(normalized_stage, '请按当前阶段写清可验收边界。')}"


def _auto_respond_pending_spec_ask_user(
    engine_url: str,
    result: SpecLiveResult,
    *,
    stage: str,
    marker: str,
    target_rel: str,
    workspace: Path,
) -> list[dict[str, Any]]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    try:
        interactions = db.list_ask_user_interactions(session_id=result.session_id, status="pending")
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    responses: list[dict[str, Any]] = []
    normalized_stage = str(stage or "").strip().lower()
    answered_interaction_ids = {
        str(item.get("interactionId") or "").strip()
        for item in result.ask_user_responses
        if str(item.get("interactionId") or "").strip()
    }
    answered_stage_count = sum(
        1
        for item in result.ask_user_responses
        if str(item.get("stage") or "").strip().lower() == normalized_stage
    )
    for interaction in interactions:
        interaction_id = str(interaction.get("id") or "").strip()
        if not interaction_id:
            continue
        if interaction_id in answered_interaction_ids:
            continue
        if answered_stage_count >= 4:
            result.key_events.append({"askUserRepeatSkipped": {"stage": stage, "reason": "stage_answer_limit_reached"}})
            continue
        context = _spec_clarification_context(interaction)
        if not context:
            if not _looks_like_spec_stage_question(interaction, stage):
                continue
            request = _interaction_request(interaction)
            request["interactionKind"] = "ask_user"
            request["approvalKind"] = "ask_user"
            request["specContext"] = {
                "kind": "spec_clarification",
                "featureName": "spec-mode-live-counter",
                "stage": stage,
                "workspacePath": str(workspace),
                **({"specId": result.spec_id} if result.spec_id else {}),
            }
            try:
                db.update_ask_user_interaction(interaction_id, status="pending", request=request)
                interaction = dict(interaction)
                interaction["request"] = request
                context = _spec_clarification_context(interaction)
                result.key_events.append(
                    {
                        "askUserSpecContextPatched": {
                            "interactionId": interaction_id,
                            "stage": stage,
                            "reason": "pending ask_user looked like Spec clarification but missed specContext",
                        }
                    }
                )
            except Exception as exc:  # noqa: BLE001
                responses.append({"interactionId": interaction_id, "stage": stage, "error": f"specContextPatch:{type(exc).__name__}: {exc}"})
                continue
        elif result.spec_id and not str(context.get("specId") or context.get("spec_id") or "").strip():
            request = _interaction_request(interaction)
            context = dict(context)
            context["specId"] = result.spec_id
            request["specContext"] = context
            try:
                db.update_ask_user_interaction(interaction_id, status="pending", request=request)
                interaction = dict(interaction)
                interaction["request"] = request
                result.key_events.append(
                    {
                        "askUserSpecContextPatched": {
                            "interactionId": interaction_id,
                            "stage": stage,
                            "specId": result.spec_id,
                            "reason": "pending ask_user had specContext but missed specId",
                        }
                    }
                )
            except Exception as exc:  # noqa: BLE001
                responses.append({"interactionId": interaction_id, "stage": stage, "error": f"specIdPatch:{type(exc).__name__}: {exc}"})
                continue
        interaction_stage = str(context.get("stage") or context.get("specStage") or "").strip().lower()
        if normalized_stage and interaction_stage and interaction_stage != normalized_stage:
            continue
        answer_stage = interaction_stage or normalized_stage or "requirements"
        answer = _spec_clarification_answer(stage=answer_stage, marker=marker, target_rel=target_rel)
        try:
            response = _json_request(
                f"{_engine_api_base(engine_url)}/ask-user/{interaction_id}/respond",
                method="POST",
                payload={
                    "reason": "spec live audit answered required clarification",
                    "response": {
                        "answer": answer,
                        "source": "spec_mode_project_live_audit",
                        "specStage": answer_stage,
                    },
                },
                timeout=12,
            )
        except Exception as exc:  # noqa: BLE001
            responses.append({"interactionId": interaction_id, "stage": answer_stage, "error": f"{type(exc).__name__}: {exc}"})
            continue
        _remember_run_ids(result, response)
        item = {
            "interactionId": interaction_id,
            "stage": answer_stage,
            "question": str(interaction.get("question") or interaction.get("prompt") or "")[:240],
            "status": "responded",
            "resumeScheduled": bool(response.get("resume_scheduled")) if isinstance(response, dict) else False,
            "specClarification": response.get("spec_clarification") if isinstance(response, dict) else None,
        }
        responses.append(item)
        result.ask_user_responses.append(item)
        answered_stage_count += 1
    return responses


def _adopt_active_spec(
    result: SpecLiveResult,
    *,
    workspace: Path,
    marker: str,
    target_rel: str,
) -> bool:
    if result.spec_id:
        return True
    found = _find_spec_by_marker_or_target(workspace, marker, target_rel)
    if not found:
        return False
    manifest = found.get("manifest") if isinstance(found, dict) else {}
    spec_id = str((manifest or {}).get("specId") or (manifest or {}).get("spec_id") or "").strip()
    if not spec_id:
        return False
    result.spec_id = spec_id
    result.spec_dir = str(found.get("specDir") or "")
    result.key_events.append({"activeSpecRecovered": {"specId": result.spec_id, "specDir": result.spec_dir}})
    return True


def _ensure_spec_clarification_via_ask_user(
    engine_url: str,
    result: SpecLiveResult,
    *,
    workspace: Path,
    marker: str,
    target_rel: str,
    stage: str,
) -> list[dict[str, Any]]:
    if not _has_spec_clarification_required(result, stage):
        return []
    if not _adopt_active_spec(result, workspace=workspace, marker=marker, target_rel=target_rel):
        result.key_events.append({"askUserHarnessClarification": {"stage": stage, "ok": False, "reason": "spec_id_unavailable"}})
        return []
    if any(item.get("stage") == stage and item.get("status") == "responded" for item in result.ask_user_responses):
        return []
    run_id = str((result.run_ids or [""])[-1] or "").strip()
    if not run_id:
        result.key_events.append({"askUserHarnessClarification": {"stage": stage, "ok": False, "reason": "run_id_unavailable"}})
        return []
    from core.database import db

    interaction_id = f"ask_live_{stage}_{uuid.uuid4().hex[:12]}"
    question = f"请确认 Spec `{result.spec_id}` 的 {stage} 阶段边界与验收口径。"
    request = {
        "interactionKind": "ask_user",
        "approvalKind": "ask_user",
        "question": question,
        "prompt": question,
        "specContext": {
            "kind": "spec_clarification",
            "specId": result.spec_id,
            "featureName": "spec-mode-live-counter",
            "stage": stage,
            "workspacePath": str(workspace),
        },
    }
    try:
        db.add_ask_user_interaction(
            interaction_id=interaction_id,
            session_id=result.session_id,
            run_id=run_id,
            tool_call_id=f"tool_live_{stage}_{uuid.uuid4().hex[:8]}",
            question=question,
            prompt=question,
            request=request,
            status="pending",
        )
    except Exception as exc:  # noqa: BLE001
        result.key_events.append({"askUserHarnessClarification": {"stage": stage, "ok": False, "reason": f"create:{type(exc).__name__}: {exc}"}})
        return []
    result.key_events.append({"askUserHarnessClarification": {"stage": stage, "ok": True, "interactionId": interaction_id, "runId": run_id}})
    responses = _auto_respond_pending_spec_ask_user(
        engine_url,
        result,
        stage=stage,
        marker=marker,
        target_rel=target_rel,
        workspace=workspace,
    )
    if responses:
        result.key_events.append({"askUserResponses": responses})
    return responses


def _bootstrap_live_spec_shell(workspace: Path, *, marker: str, target_rel: str, session_id: str) -> dict[str, Any]:
    from core.spec_service import spec_service

    feature_name = "spec-mode-live-counter"
    user_request = (
        "Spec Mode live audit bootstrap. "
        f"Live marker: {marker}. Target output directory: {target_rel}. "
        "The supervisor must still write requirements/design/tasks through spec_broker."
    )
    paths = spec_service._new_spec_paths(str(workspace), feature_name=feature_name)
    manifest = spec_service._ensure_manifest(
        paths,
        feature_name=feature_name,
        kind="feature",
        user_request=user_request,
    )
    spec_service._write_manifest(paths, manifest)
    spec_id = str(manifest.get("specId") or "")
    answer = _spec_clarification_answer(stage="requirements", marker=marker, target_rel=target_rel)
    clarification = spec_service.record_clarification(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        question="Live harness bootstrap clarification for Spec requirements boundary.",
        answer=answer,
        source_run_id=session_id,
        tool_call_id="live_harness_bootstrap",
        interaction_id=f"live_harness_bootstrap_{marker}",
        feature_name=feature_name,
    )
    return {
        "specId": spec_id,
        "specDir": str(paths.spec_dir),
        "clarification": clarification,
    }


def _wait_for_engine(engine_url: str, timeout_s: float = 20.0) -> tuple[bool, str]:
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            _json_request(f"{_engine_root_url(engine_url)}/health", timeout=3)
            return True, ""
        except Exception as exc:  # noqa: BLE001 - live diagnostics preserve connectivity error.
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.75)
    return False, last_error


def _submit(
    engine_url: str,
    *,
    session_id: str,
    workspace: Path,
    model_profile: str,
    safety_approval_mode: str,
    prompt: str,
    spec_id: str = "",
    client_tag: str,
) -> tuple[str, int, dict[str, Any]]:
    data: dict[str, Any] = {
        "conversationId": session_id,
        "clientMessageId": client_tag,
        "specMode": True,
        "safetyApprovalMode": safety_approval_mode,
        "taskPlanningMode": False,
        "plannerMode": "off",
        "plannerDispatchMode": "suggest",
    }
    if _uses_model_override(model_profile):
        data["modelProfile"] = _normalized_model_profile(model_profile)
    if spec_id:
        data["specId"] = spec_id
        data["specCommand"] = {"action": "continue", "specId": spec_id}
    else:
        data["specCommand"] = {"action": "new"}
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": client_tag,
        "stream": False,
        "workspacePath": str(workspace),
        "messages": [{"role": "user", "content": prompt}],
        "data": data,
    }
    started = time.perf_counter()
    response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    latency_ms = int((time.perf_counter() - started) * 1000)
    run_id = str(response.get("run_id") or response.get("runId") or "")
    return run_id, latency_ms, response


def _session_idle_state(session_id: str) -> tuple[bool, dict[str, Any]]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}
    try:
        runs = db.list_run_records(session_id=session_id, run_type="chat", limit=20)
        active_runs = [
            {"id": item.get("id"), "status": item.get("status")}
            for item in runs
            if str(item.get("status") or "").strip().lower() not in TERMINAL_RUN_STATES
        ]
        queue_items = db.list_chat_user_message_queue(session_id=session_id, states=ACTIVE_QUEUE_STATES, limit=20)
        active_queue = [{"id": item.get("id"), "state": item.get("state")} for item in queue_items]
        episode_items = db.list_runtime_episodes(session_id=session_id, active_only=True, limit=50)
        active_episodes = [
            {
                "id": item.get("episodeId") or item.get("id"),
                "kind": item.get("kind"),
                "state": item.get("state"),
                "runId": item.get("runId") or item.get("run_id"),
            }
            for item in episode_items
            if str(item.get("state") or "").strip().lower() in ACTIVE_RUNTIME_EPISODE_STATES
        ]
        return not active_runs and not active_queue and not active_episodes, {
            "activeRuns": active_runs,
            "activeQueue": active_queue,
            "activeRuntimeEpisodes": active_episodes,
        }
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}


def _wait_for_idle(session_id: str, *, timeout_s: int) -> tuple[bool, dict[str, Any]]:
    deadline = time.time() + max(5, timeout_s)
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        idle, state = _session_idle_state(session_id)
        last_state = state
        if idle:
            return True, state
        time.sleep(2)
    return False, last_state


def _find_spec_by_marker_or_target(workspace: Path, marker: str, target_rel: str, *, spec_id: str = "") -> dict[str, Any] | None:
    root = workspace / ".v8" / "specs"
    if not root.exists():
        return None
    newest: tuple[float, dict[str, Any]] | None = None
    normalized_target = target_rel.replace("\\", "/").lower()
    expected_spec_id = str(spec_id or "").strip()
    for manifest_path in root.glob("*/spec.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if expected_spec_id and str(manifest.get("specId") or "").strip() != expected_spec_id:
            continue
        haystack = json.dumps(manifest, ensure_ascii=False)
        for doc_path in manifest_path.parent.glob("*.md"):
            try:
                haystack += "\n" + doc_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
        normalized_haystack = haystack.replace("\\", "/").lower()
        if marker not in haystack and normalized_target not in normalized_haystack:
            continue
        mtime = manifest_path.stat().st_mtime
        payload = {"manifest": manifest, "manifestPath": str(manifest_path), "specDir": str(manifest_path.parent)}
        if newest is None or mtime > newest[0]:
            newest = (mtime, payload)
    return newest[1] if newest else None


def _wait_for_stage(workspace: Path, marker: str, target_rel: str, stage: str, *, timeout_s: int, spec_id: str = "") -> dict[str, Any] | None:
    deadline = time.time() + max(5, timeout_s)
    while time.time() < deadline:
        found = _find_spec_by_marker_or_target(workspace, marker, target_rel, spec_id=spec_id)
        if found:
            manifest = found["manifest"]
            doc = (manifest.get("documents") or {}).get(stage)
            if isinstance(doc, dict) and doc.get("relativePath"):
                return found
        time.sleep(2)
    return None


def _wait_for_stage_with_clarifications(
    engine_url: str,
    result: SpecLiveResult,
    workspace: Path,
    marker: str,
    target_rel: str,
    stage: str,
    *,
    timeout_s: int,
    spec_id: str = "",
) -> dict[str, Any] | None:
    deadline = time.time() + max(5, timeout_s)
    while time.time() < deadline:
        found = _find_spec_by_marker_or_target(workspace, marker, target_rel, spec_id=spec_id)
        if found:
            manifest = found["manifest"]
            doc = (manifest.get("documents") or {}).get(stage)
            if isinstance(doc, dict) and doc.get("relativePath"):
                return found
        responses = _auto_respond_pending_spec_ask_user(
            engine_url,
            result,
            stage=stage,
            marker=marker,
            target_rel=target_rel,
            workspace=workspace,
        )
        if responses:
            result.key_events.append({"askUserResponses": responses})
        _collect_durable(result)
        if not responses and _has_spec_clarification_required(result, stage):
            responses = _ensure_spec_clarification_via_ask_user(
                engine_url,
                result,
                workspace=workspace,
                marker=marker,
                target_rel=target_rel,
                stage=stage,
            )
            if responses:
                result.key_events.append({"askUserResponses": responses})
        if _finding_exists(result, "model_pseudo_tool_call_not_executed"):
            return None
        time.sleep(2)
    return None


def _retry_stage_after_pseudo_tool_call(
    engine_url: str,
    result: SpecLiveResult,
    *,
    workspace: Path,
    marker: str,
    target_rel: str,
    stage: str,
    model_profile: str,
    safety_approval_mode: str,
) -> bool:
    if not _finding_exists(result, "model_pseudo_tool_call_not_executed"):
        return False
    if not _adopt_active_spec(result, workspace=workspace, marker=marker, target_rel=target_rel):
        result.key_events.append({"pseudoToolRetry": {"stage": stage, "ok": False, "error": "spec_id_unavailable"}})
        return False
    _clear_findings(result, "model_pseudo_tool_call_not_executed")
    prompt = (
        "[Spec live recovery]\n"
        f"The previous `{stage}` turn emitted a textual pseudo tool call instead of a real tool call. "
        "Recover by executing exactly one real `spec_broker` tool call now.\n\n"
        f"- Existing specId: `{result.spec_id}`\n"
        f"- Stage to write: `{stage}`\n"
        f"- Live marker: `{marker}`\n"
        f"- Target output directory: `{target_rel}`\n"
        "- Do not write XML/DSML/tool-call text in the assistant message.\n"
        "- Do not use shell/file tools for Spec documents.\n"
        "- Call `spec_broker(mode='write_stage', spec_id='<existing specId>', stage='<stage>', content='<complete markdown>')` as a real tool call.\n"
        "- For tasks, each large task must have parseable `mvpSlice` and `independentAcceptance` evidence, either in its TASK detail block or in task-keyed tables/sections.\n"
    )
    try:
        run_id, latency, response = _submit(
            engine_url,
            session_id=result.session_id,
            workspace=workspace,
            model_profile=model_profile,
            safety_approval_mode=safety_approval_mode,
            prompt=prompt,
            client_tag=f"spec-live-retry-{stage}",
            spec_id=result.spec_id,
        )
    except Exception as exc:  # noqa: BLE001
        result.key_events.append({"pseudoToolRetry": {"stage": stage, "ok": False, "error": f"{type(exc).__name__}: {exc}"}})
        return False
    if run_id:
        result.run_ids.append(run_id)
    result.submit_latencies_ms.append(latency)
    result.key_events.append(
        {
            "pseudoToolRetry": {
                "stage": stage,
                "ok": True,
                "runId": run_id,
                "latencyMs": latency,
                "accepted": response.get("accepted") if isinstance(response, dict) else None,
            }
        }
    )
    _remember_run_ids(result, response)
    code = _find_workspace_blocker_code(response)
    if code:
        _record_workspace_blocker(result, code, response)
        return False
    return True


def _read_stage(workspace: Path, manifest: dict[str, Any], stage: str) -> tuple[Path | None, str]:
    doc = (manifest.get("documents") or {}).get(stage)
    if not isinstance(doc, dict):
        return None, ""
    rel = str(doc.get("relativePath") or "").strip()
    path = workspace / rel if rel else Path(str(doc.get("path") or ""))
    if not path.exists():
        return path, ""
    return path, path.read_text(encoding="utf-8", errors="ignore")


def _stage_quality(stage: str, content: str, *, marker: str, target_rel: str) -> list[str]:
    lower = content.lower()
    findings: list[str] = []
    if marker not in content:
        findings.append("marker_missing")
    if target_rel.replace("\\", "/").lower() not in lower.replace("\\", "/"):
        findings.append("target_path_missing")
    if stage == "requirements":
        if not any(token in lower for token in ("req-", "fr-", "nfr-", "bfix-")):
            findings.append("requirement_ids_missing")
        if "验收" not in content and "acceptance" not in lower and "shall" not in lower:
            findings.append("acceptance_criteria_missing")
        if "index.html" not in lower or "readme" not in lower:
            findings.append("deliverable_files_missing")
    elif stage == "design":
        if "index.html" not in lower or "readme" not in lower:
            findings.append("design_files_missing")
        if "验证" not in content and "verification" not in lower:
            findings.append("verification_strategy_missing")
    elif stage == "tasks":
        if "task-" not in lower and "tsk-" not in lower:
            findings.append("task_ids_missing")
        if not any(token in lower for token in ("req-", "fr-", "nfr-", "bfix-")):
            findings.append("task_requirement_links_missing")
    return findings


def _approve_stage(
    engine_url: str,
    *,
    session_id: str,
    spec_id: str,
    stage: str,
    comment: str,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001 - live diagnostics preserve storage errors.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    normalized_stage = str(stage or "").strip().lower()
    deadline = time.time() + max(1.0, timeout_s)
    last_error = ""
    last_response: dict[str, Any] | None = None
    while time.time() < deadline:
        try:
            pending = db.list_pending_approvals(session_id=session_id, status="pending")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        candidates: list[dict[str, Any]] = []
        for approval in pending:
            if str(approval.get("approval_kind") or "").strip() != "spec_stage_approval":
                continue
            request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
            if str(request.get("specId") or "").strip() != spec_id:
                continue
            if str(request.get("stage") or "").strip().lower() != normalized_stage:
                continue
            candidates.append(approval)
        candidates.sort(key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""), reverse=True)
        for approval in candidates:
            request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
            approval_id = str(approval.get("id") or approval.get("approval_id") or "").strip()
            if not approval_id:
                continue
            response = _json_request(
                f"{_engine_api_base(engine_url)}/approvals/{approval_id}/approve",
                method="POST",
                payload={
                    "reason": comment,
                    "response": {
                        "decision": "approved",
                        "source": "spec_mode_project_live_audit",
                        "specId": spec_id,
                        "stage": normalized_stage,
                        "comment": comment,
                    },
                },
                timeout=12,
            )
            last_response = response if isinstance(response, dict) else None
            spec_stage_approval = response.get("spec_stage_approval") if isinstance(response, dict) else None
            if isinstance(spec_stage_approval, dict) and spec_stage_approval.get("ok") is False:
                analysis = spec_stage_approval.get("analysis") if isinstance(spec_stage_approval.get("analysis"), dict) else {}
                return {
                    "ok": False,
                    "error": str(spec_stage_approval.get("kind") or spec_stage_approval.get("error") or "spec_stage_approval_failed"),
                    "approvalId": approval_id,
                    "stage": normalized_stage,
                    "response": response,
                    "hardBlockers": list(analysis.get("hardBlockers") or []),
                    "summary": str(spec_stage_approval.get("summary") or ""),
                }
            resume_scheduled = bool(response.get("resume_scheduled")) if isinstance(response, dict) else False
            if normalized_stage == "tasks" and not resume_scheduled:
                last_error = str(response.get("resume_error") or "tasks_approval_resume_not_scheduled") if isinstance(response, dict) else "tasks_approval_resume_not_scheduled"
                try:
                    from core.spec_service import spec_service

                    workspace_path = str(request.get("workspacePath") or request.get("workspace_path") or "").strip()
                    brief = spec_service.build_brief(workspace_path=workspace_path, spec_id=spec_id) if workspace_path else {}
                    pipeline = brief.get("pipelineControl") if isinstance(brief.get("pipelineControl"), dict) else {}
                    if bool(pipeline.get("runtimeExecutionAllowed")):
                        return {
                            "ok": True,
                            "approvalId": approval_id,
                            "stage": normalized_stage,
                            "response": response,
                            "resumeScheduled": False,
                            "resumeWarning": last_error,
                        }
                except Exception:
                    pass
                continue
            return {
                "ok": True,
                "approvalId": approval_id,
                "stage": normalized_stage,
                "response": response,
                "resumeScheduled": resume_scheduled,
            }
        if not candidates:
            last_error = "pending_spec_stage_approval_not_found"
        time.sleep(1.5)
    return {
        "ok": False,
        "error": last_error or "pending_spec_stage_approval_not_found",
        "specId": spec_id,
        "stage": normalized_stage,
        **({"response": last_response} if last_response else {}),
    }


def _collect_durable(result: SpecLiveResult) -> None:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        result.key_events.append({"durableLookupError": f"{type(exc).__name__}: {exc}"})
        return
    episodes: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    try:
        episodes.extend(db.list_runtime_episodes(session_id=result.session_id, limit=200))
        for run_id in result.run_ids:
            episodes.extend(db.list_runtime_episodes(run_id=run_id, limit=200))
        seen: set[str] = set()
        for episode in episodes:
            episode_id = str(episode.get("episodeId") or episode.get("id") or episode.get("needId") or "")
            if not episode_id or episode_id in seen:
                continue
            seen.add(episode_id)
            kind = str(episode.get("kind") or episode.get("runtimeKind") or "").strip()
            if kind and kind not in result.episode_kinds:
                result.episode_kinds.append(kind)
            handoffs.extend(db.list_runtime_episode_handoffs(episode_id))
        for row in handoffs:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
            kind = str((payload or {}).get("kind") or "").strip()
            if kind and kind not in result.handoff_kinds:
                result.handoff_kinds.append(kind)
        runtime_events: list[dict[str, Any]] = []
        runtime_events.extend(db.get_runtime_events(result.session_id))
        for run_id in result.run_ids:
            runtime_events.extend(db.get_runtime_events_for_run(run_id, session_id=result.session_id, limit=300))
        seen_events: set[str] = set()
        durable_seen = set(result.seen_runtime_event_keys)
        for event in runtime_events:
            event_id = str(event.get("event_id") or event.get("eventId") or event.get("id") or event.get("seq") or json.dumps(event, ensure_ascii=False, default=str)[:120])
            event_id = f"{event.get('session_id') or result.session_id}:{event.get('run_id') or ''}:{event_id}"
            if event_id in seen_events:
                continue
            seen_events.add(event_id)
            if event_id in durable_seen:
                continue
            result.seen_runtime_event_keys.append(event_id)
            durable_seen.add(event_id)
            payload = event.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    pass
            code = _find_workspace_blocker_code(payload) or _find_workspace_blocker_code(event)
            if code:
                _record_workspace_blocker(
                    result,
                    code,
                    {
                        "topic": event.get("topic"),
                        "eventType": event.get("event_type") or event.get("eventType"),
                        "payload": payload,
                    },
                )
            can_contain_model_pseudo_tool_call = _event_can_contain_model_pseudo_tool_call(event, payload)
            if can_contain_model_pseudo_tool_call and _contains_pseudo_tool_call(payload):
                _record_pseudo_tool_call_observed(
                    result,
                    {
                        "topic": event.get("topic"),
                        "eventType": event.get("event_type") or event.get("eventType"),
                        "payload": payload,
                    },
                )
            if can_contain_model_pseudo_tool_call:
                _maybe_record_pseudo_tool_call_failure(result, event, payload)
            tool = payload.get("tool") if isinstance(payload, dict) and isinstance(payload.get("tool"), dict) else {}
            tool_result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
            if tool.get("toolName") == "spec_broker" and tool_result.get("kind") == "spec_clarification_required":
                result.key_events.append(
                    {
                        "specClarificationRequired": {
                            "stage": tool_result.get("stage"),
                            "featureName": tool_result.get("featureName"),
                            "summary": tool_result.get("summary"),
                        }
                    }
                )
    except Exception as exc:  # noqa: BLE001
        result.key_events.append({"durableLookupError": f"{type(exc).__name__}: {exc}"})


def _validate_outputs(result: SpecLiveResult) -> None:
    target = Path(result.target_dir)
    for rel in ("index.html", "README.md"):
        path = target / rel
        exists = path.exists()
        content = path.read_text(encoding="utf-8", errors="ignore") if exists else ""
        result.output_files[rel] = {
            "exists": exists,
            "chars": len(content),
            "containsMarker": result.marker in content,
            "path": str(path),
        }
        if not exists:
            result.findings.append({"severity": "P0", "code": f"{rel}_missing", "summary": f"缺少交付文件 {rel}"})
        elif result.marker not in content:
            result.findings.append({"severity": "P1", "code": f"{rel}_marker_missing", "summary": f"{rel} 未包含 live marker，可能不是本轮产物"})
    index_text = (target / "index.html").read_text(encoding="utf-8", errors="ignore") if (target / "index.html").exists() else ""
    if index_text and "spec mode live counter" not in index_text.lower():
        result.findings.append({"severity": "P1", "code": "counter_title_missing", "summary": "index.html 未体现 Spec Mode Live Counter 交付目标"})
    if index_text and re.search(r"live audit|审计面板|audit dashboard", index_text, re.IGNORECASE):
        result.findings.append({"severity": "P1", "code": "audit_dashboard_drift", "summary": "index.html 被带偏成审计/报告页面，而不是计数器项目"})
    if index_text and not re.search(r"<button|onclick|addEventListener", index_text, re.IGNORECASE):
        result.findings.append({"severity": "P1", "code": "interactive_quality_missing", "summary": "index.html 缺少可判断的交互按钮/事件"})


def _write_report(result: SpecLiveResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "spec_mode_project_live_result.json"
    json_path.write_text(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=lambda value: value.__dict__), encoding="utf-8")
    lines = [
        "# Spec Mode Project Live Audit",
        "",
        f"- Status: {result.status}",
        f"- Session: `{result.session_id}`",
        f"- Model: `{result.model_profile}`",
        f"- Model override sent: `{result.model_override}`",
        f"- Safety approval mode: `{result.safety_approval_mode}`",
        f"- Spec: `{result.spec_id}`",
        f"- Spec shell bootstrap: `{bool(result.spec_bootstrap)}`",
        f"- Target: `{result.target_dir}`",
        "",
        "## Stages",
        "",
        "| Stage | Found | Approved | Chars | Findings |",
        "|---|---:|---:|---:|---|",
    ]
    for stage in result.stages:
        lines.append(f"| {stage.stage} | {stage.found} | {stage.approved} | {stage.content_chars} | {', '.join(stage.quality_findings) or '-'} |")
    lines.extend(
        [
            "",
            "## Runtime",
            "",
            f"- Episode kinds: {', '.join(result.episode_kinds) or '-'}",
            f"- Handoff kinds: {', '.join(result.handoff_kinds) or '-'}",
            "",
            "## Findings",
            "",
        ]
    )
    if result.findings:
        for finding in result.findings:
            lines.append(f"- [{finding.get('severity')}] {finding.get('code')}: {finding.get('summary')}")
    else:
        lines.append("- none")
    lines.extend(["", f"Raw JSON: `{json_path}`", ""])
    md_path = output_dir / "SPEC_MODE_PROJECT_LIVE_AUDIT_ZH.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def _run(args: argparse.Namespace) -> SpecLiveResult:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    workspace = Path(args.workspace).resolve()
    marker = f"SPEC_LIVE_{timestamp}"
    target_rel = f".v8/live-audit/spec-mode-v2/{timestamp}"
    target_dir = workspace / target_rel
    session_id = f"spec-mode-project-live-{timestamp}"
    result = SpecLiveResult(
        session_id=session_id,
        marker=marker,
        target_dir=str(target_dir),
        model_profile=_effective_model_label(args.model_profile),
        model_override=_uses_model_override(args.model_profile),
        safety_approval_mode=str(args.safety_approval_mode or DEFAULT_SAFETY_APPROVAL_MODE),
    )
    result.workspace_preflight = _workspace_preflight(workspace)
    result.findings.extend(result.workspace_preflight)
    if any(item.get("severity") == "P0" for item in result.workspace_preflight):
        result.status = "failed"
        return result
    trust_findings, trust_event = _ensure_live_workspace_trusted(workspace)
    result.workspace_preflight.extend(trust_findings)
    result.findings.extend(trust_findings)
    result.key_events.append({"workspaceTrustPreflight": trust_event})
    if any(item.get("severity") == "P0" for item in trust_findings):
        result.status = "failed"
        return result
    if bool(args.bootstrap_spec_shell):
        try:
            result.spec_bootstrap = _bootstrap_live_spec_shell(workspace, marker=marker, target_rel=target_rel, session_id=session_id)
            result.spec_id = str(result.spec_bootstrap.get("specId") or "")
            result.spec_dir = str(result.spec_bootstrap.get("specDir") or "")
            result.key_events.append({"specShellBootstrap": result.spec_bootstrap})
        except Exception as exc:  # noqa: BLE001
            result.findings.append(
                {
                    "severity": "P0",
                    "code": "spec_shell_bootstrap_failed",
                    "summary": f"{type(exc).__name__}: {exc}",
                }
            )
            result.status = "failed"
            return result

    prompt = (
        "开启 Spec Mode。请为当前工作区创建一个很小但可验收的静态项目，先只写 requirements.md 等待审批。\n"
        f"当前 live harness 已创建空 Spec shell，specId: {result.spec_id or '(none)'}。\n"
        f"Live marker: {marker}\n"
        f"目标输出目录：{target_rel}\n"
        "最终交付文件必须是：index.html 和 README.md。\n"
        "为了 live 验收，requirements/design/tasks 与最终 index.html/README.md 都必须包含 Live marker 原文。\n"
        "index.html 需要展示标题“Spec Mode Live Counter”，包含一个按钮，点击后页面计数 +1；README.md 说明如何打开和验收。\n"
        "需求 ID 必须使用 REQ-001、REQ-002 这种稳定格式，非功能要求也用 REQ-### 表达，方便后续执行引用。\n"
        "当前 live harness 已经记录 requirements 阶段的人类澄清证据；不要重复调用 ask_user。\n"
        f"请直接调用真实 spec_broker tool：mode='write_stage', spec_id='{result.spec_id}', stage='requirements'，"
        "content 参数必须包含可审批的完整 requirements Markdown 文档，不要只生成空泛模板。\n"
        "禁止把 write_native_file、run_system_command 或 DSML/XML 伪工具块写在正文里；"
        "尤其不要输出 `<tool_call>`、`<invoke name=...>` 或任何 XML/JSON 工具块正文，这会被 Engine 当成普通文本，真实工具不会执行，live 会失败。\n"
        "如果无法通过模型原生工具调用通道调用 spec_broker，请只回复 `recoverable_failed: unable_to_call_spec_broker_tool`。"
    )
    try:
        run_id, latency, response = _submit(
            args.engine_url,
            session_id=session_id,
            workspace=workspace,
            model_profile=args.model_profile,
            safety_approval_mode=args.safety_approval_mode,
            prompt=prompt,
            spec_id=result.spec_id,
            client_tag=f"{marker}-requirements",
        )
    except urllib.error.HTTPError as exc:
        payload = _http_error_payload(exc)
        code = _find_workspace_blocker_code(payload)
        if code:
            _record_workspace_blocker(result, code, payload)
        else:
            result.findings.append(
                {
                    "severity": "P0",
                    "code": "chat_submit_http_error",
                    "summary": f"chat submit HTTP {payload.get('status')}: {payload.get('reason')}",
                }
            )
            result.key_events.append({"submitHttpError": payload})
        result.status = "failed"
        return result
    except Exception as exc:  # noqa: BLE001 - live script reports entrypoint failures.
        result.findings.append(
            {
                "severity": "P0",
                "code": "chat_submit_failed",
                "summary": f"{type(exc).__name__}: {exc}",
            }
        )
        result.status = "failed"
        return result
    if run_id:
        result.run_ids.append(run_id)
    result.submit_latencies_ms.append(latency)
    result.key_events.append({"requirementsSubmit": {"latencyMs": latency, "accepted": response.get("accepted"), "runId": run_id}})
    _remember_run_ids(result, response)
    code = _find_workspace_blocker_code(response)
    if code:
        _record_workspace_blocker(result, code, response)
        result.status = "failed"
        return result
    _collect_durable(result)

    stage_payload = _wait_for_stage_with_clarifications(
        args.engine_url,
        result,
        workspace,
        marker,
        target_rel,
        "requirements",
        timeout_s=args.max_wait,
    )
    if not stage_payload:
        if _retry_stage_after_pseudo_tool_call(
            args.engine_url,
            result,
            workspace=workspace,
            marker=marker,
            target_rel=target_rel,
            stage="requirements",
            model_profile=args.model_profile,
            safety_approval_mode=args.safety_approval_mode,
        ):
            stage_payload = _wait_for_stage_with_clarifications(
                args.engine_url,
                result,
                workspace,
                marker,
                target_rel,
                "requirements",
                timeout_s=args.max_wait,
            )
    if not stage_payload:
        _collect_durable(result)
        if _has_spec_clarification_required(result, "requirements") and not any(item.get("stage") == "requirements" for item in result.ask_user_responses):
            result.findings.append(
                {
                    "severity": "P0",
                    "code": "spec_ask_user_not_requested",
                    "summary": "spec_broker 已要求 ask_user 澄清，但等待窗口内没有产生可回答的 ask_user interaction。",
                }
            )
        result.findings.append({"severity": "P0", "code": "requirements_missing", "summary": "等待窗口内未生成 requirements.md"})
        result.status = "failed"
        return result
    manifest = stage_payload["manifest"]
    result.spec_id = str(manifest.get("specId") or "")
    result.spec_dir = str(stage_payload["specDir"])

    for stage in ("requirements",):
        path, content = _read_stage(workspace, manifest, stage)
        quality = _stage_quality(stage, content, marker=marker, target_rel=target_rel)
        result.stages.append(StageObservation(stage=stage, found=bool(content), path=str(path or ""), content_chars=len(content), quality_findings=quality))
        for item in quality:
            result.findings.append({"severity": "P1", "code": f"{stage}_{item}", "summary": f"{stage} 文档质量缺口：{item}"})
    approved = _approve_stage(
        args.engine_url,
        session_id=session_id,
        spec_id=result.spec_id,
        stage="requirements",
        comment="live audit approved requirements",
    )
    result.stages[-1].approved = bool(approved.get("ok"))
    result.key_events.append({"requirementsApproval": approved})
    _remember_run_ids(result, approved)
    if not approved.get("ok"):
        result.findings.append({"severity": "P0", "code": "requirements_approval_failed", "summary": str(approved.get("error") or "requirements approval failed")})
        result.status = "failed"
        return result

    for stage in ("design", "tasks"):
        stage_payload = _wait_for_stage_with_clarifications(
            args.engine_url,
            result,
            workspace,
            marker,
            target_rel,
            stage,
            timeout_s=args.max_wait,
            spec_id=result.spec_id,
        )
        if not stage_payload:
            if _retry_stage_after_pseudo_tool_call(
                args.engine_url,
                result,
                workspace=workspace,
                marker=marker,
                target_rel=target_rel,
                stage=stage,
                model_profile=args.model_profile,
                safety_approval_mode=args.safety_approval_mode,
            ):
                stage_payload = _wait_for_stage_with_clarifications(
                    args.engine_url,
                    result,
                    workspace,
                    marker,
                    target_rel,
                    stage,
                    timeout_s=args.max_wait,
                    spec_id=result.spec_id,
                )
        if not stage_payload:
            _collect_durable(result)
            if _has_spec_clarification_required(result, stage) and not any(item.get("stage") == stage for item in result.ask_user_responses):
                result.findings.append(
                    {
                        "severity": "P0",
                        "code": "spec_ask_user_not_requested",
                        "summary": "spec_broker 已要求 ask_user 澄清，但等待窗口内没有产生可回答的 ask_user interaction。",
                    }
                )
            result.findings.append({"severity": "P0", "code": f"{stage}_missing", "summary": f"等待窗口内未生成 {stage}.md"})
            result.status = "failed"
            return result
        manifest = stage_payload["manifest"]
        path, content = _read_stage(workspace, manifest, stage)
        quality = _stage_quality(stage, content, marker=marker, target_rel=target_rel)
        observation = StageObservation(stage=stage, found=bool(content), path=str(path or ""), content_chars=len(content), quality_findings=quality)
        result.stages.append(observation)
        for item in quality:
            result.findings.append({"severity": "P1", "code": f"{stage}_{item}", "summary": f"{stage} 文档质量缺口：{item}"})
        approved = _approve_stage(
            args.engine_url,
            session_id=session_id,
            spec_id=result.spec_id,
            stage=stage,
            comment=f"live audit approved {stage}",
        )
        observation.approved = bool(approved.get("ok"))
        result.key_events.append({f"{stage}Approval": approved})
        _remember_run_ids(result, approved)
        if not approved.get("ok"):
            result.findings.append({"severity": "P0", "code": f"{stage}_approval_failed", "summary": str(approved.get("error") or f"{stage} approval failed")})
            result.status = "failed"
            return result

    _collect_durable(result)
    idle, idle_state = _wait_for_idle(session_id, timeout_s=args.max_wait)
    result.key_events.append({"executionIdle": {"ok": idle, "state": idle_state}})
    _collect_durable(result)
    if not idle:
        result.findings.append({
            "severity": "P0",
            "code": "execution_not_idle",
            "summary": "等待窗口结束时仍有 chat run、队列消息或 runtime episode 未收敛；不提前验收产物。",
        })
        result.status = "failed"
        return result
    time.sleep(2)
    _collect_durable(result)
    _validate_outputs(result)
    if "engineering" not in result.episode_kinds and not any("engineering" in item.lower() for item in result.handoff_kinds):
        result.findings.append({"severity": "P1", "code": "engineering_episode_missing", "summary": "执行阶段未观察到 Engineering episode/handoff"})
    result.status = "failed" if any(item.get("severity") == "P0" for item in result.findings) else ("degraded" if result.findings else "passed")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live Spec Mode project delivery audit.")
    parser.add_argument("--live", action="store_true", help="Submit real live chat runs.")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument(
        "--model-profile",
        default=DEFAULT_MODEL_PROFILE,
        help="Optional model_ref/profile override. Omit to use the Admin-configured supervisor model.",
    )
    parser.add_argument(
        "--safety-approval-mode",
        choices=["manual", "reduced", "minimal"],
        default=DEFAULT_SAFETY_APPROVAL_MODE,
        help="Safety approval mode for the live chat request. Default reduced matches product low-friction mode while preserving hard protections.",
    )
    parser.add_argument("--max-wait", type=int, default=480)
    parser.add_argument(
        "--no-bootstrap-spec-shell",
        dest="bootstrap_spec_shell",
        action="store_false",
        help="Disable the default empty Spec shell + clarification bootstrap and exercise the pure /spec new prompt path.",
    )
    parser.set_defaults(bootstrap_spec_shell=True)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    if not args.live:
        print(
            json.dumps(
                {
                    "live": False,
                    "workspace": args.workspace,
                    "modelProfile": _effective_model_label(args.model_profile),
                    "modelOverrideSent": _uses_model_override(args.model_profile),
                    "safetyApprovalMode": args.safety_approval_mode,
                    "bootstrapSpecShell": bool(args.bootstrap_spec_shell),
                    "note": "不传 --model-profile 时，脚本不会发送 modelProfile，Engine 会使用 Admin 已配置的 supervisor 模型。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    ok, error = _wait_for_engine(args.engine_url)
    if not ok:
        print(f"[spec-mode-live] Engine unavailable: {error}", file=sys.stderr)
        return 2
    result = _run(args)
    if args.write_report:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_REPORT_ROOT / "spec_mode_project" / timestamp
        report_path = _write_report(result, output_dir)
        print(f"[spec-mode-live] report: {report_path}")
    print(json.dumps({"status": result.status, "sessionId": result.session_id, "specId": result.spec_id, "findings": result.findings}, ensure_ascii=False, indent=2))
    return 0 if result.status in {"passed", "degraded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
