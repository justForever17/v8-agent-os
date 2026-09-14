from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from core.actor_identity import resolve_collaboration_actor
from core.database import db
from core.engineering_capsule import ensure_engineering_task_capsule
from core.realtime_protocol import utc_now_iso
from erc.session_lifecycle_service import session_lifecycle_service


_CREATE_INTENT = re.compile(
    r"(?:创建|建立|新建|开设|开启|开).{0,35}(?:会话|任务)|"
    r"(?:create|start|open|establish).{0,50}(?:session|task)", re.I,
)
_DENY_INTENT = re.compile(
    r"(?:不要|不准|禁止|别|取消|do\s+not|don't|never).{0,18}"
    r"(?:创建|建立|新建|派|继续|create|start|delegate|continue)", re.I,
)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _scope(binding: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(binding.get("workspace_path") or "")).expanduser().resolve()
    if not binding.get("workspace_path") or binding.get("status") != "active" or not path.is_dir():
        raise ValueError("assignment_workspace_not_ready")
    stat = path.stat()
    return {
        "projectId": binding.get("project_id"), "workspaceId": binding.get("workspace_id"),
        "workspacePath": str(path), "resolvedScope": binding.get("resolved_scope"),
        "directoryIdentity": [stat.st_dev, stat.st_ino],
    }


def _paths(values: Any, workspace: str) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("assignment_path_set_required")
    root = Path(workspace).resolve()
    result = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or any(c in value for c in "*?"):
            raise ValueError("assignment_path_invalid")
        target = (root / value).resolve()
        if not target.is_relative_to(root):
            raise ValueError("assignment_path_outside_workspace")
        # Store concrete identities, retaining directory intent for future children.
        normalized = str(target) + (os.sep if value.endswith(("/", "\\")) or target.is_dir() else "")
        if normalized not in result:
            result.append(normalized)
    return result


class SessionCommandService:
    """Project authorization for the existing session coordination transport.

    Only the assignment relation is new persistence. Messages, admission, runs,
    user intent, scope and Engineering Capsules retain their existing owners.
    """

    def __init__(self, *, database=None, coordination=None):
        self.db = database or db
        self.coordination = coordination

    def _actor(self, context: dict[str, Any]) -> tuple[str, str, str]:
        session_id = str(context.get("session_id") or context.get("sessionId") or "")
        user_id = str(context.get("user_id") or context.get("userId") or "")
        run_id = str(context.get("run_id") or context.get("runId") or "")
        actor = resolve_collaboration_actor(runtime_context=context)
        session = self.db.get_session(session_id)
        run = self.db.get_run_record(run_id)
        if not actor.is_supervisor or actor.runtime_kind not in {"chat", "supervisor"}:
            raise ValueError("assignment_supervisor_required")
        if not session or not user_id or session.get("user_id") != user_id:
            raise ValueError("assignment_owner_mismatch")
        if not run or run.get("session_id") != session_id or run.get("user_id") != user_id:
            raise ValueError("assignment_run_scope_mismatch")
        if run.get("status") not in {"running", "queued"}:
            raise ValueError("assignment_run_not_active")
        return session_id, user_id, run_id

    @staticmethod
    def envelope(row: dict[str, Any]) -> dict[str, Any]:
        contract = row["contract"]
        return {
            "schemaVersion": "v8.session_assignment.v1",
            "assignmentId": row["assignmentId"], "rootSessionId": row["rootSessionId"],
            "childSessionId": row["childSessionId"], "revision": row["revision"],
            "status": row["status"], "authorizationRef": contract["authorizationRef"],
            "requirementRevision": contract["requirementRevision"],
            "scopeRevision": _digest(contract["scope"]), "scope": deepcopy(contract["scope"]),
            "permittedControls": list(contract["permittedControls"]),
            "taskBrief": deepcopy(contract["taskBrief"]),
            "userInstruction": contract["userInstruction"],
            "safetyApprovalMode": contract["safetyApprovalMode"],
        }

    def validate(self, assignment_id: str, *, session_id: str, user_id: str, revision: int) -> dict[str, Any]:
        row = self.db.get_session_command_assignment(assignment_id)
        if not row or session_id not in {row["rootSessionId"], row["childSessionId"]}:
            raise ValueError("assignment_relation_required")
        if not user_id or row["userId"] != user_id:
            raise ValueError("assignment_owner_mismatch")
        if row["status"] != "active":
            raise ValueError("assignment_revoked")
        if row["revision"] != revision:
            raise ValueError("assignment_revision_changed")
        contract = row["contract"]
        for target in (row["rootSessionId"], row["childSessionId"]):
            session = self.db.get_session(target)
            if not session or session.get("user_id") != user_id:
                raise ValueError("assignment_owner_mismatch")
            if _scope(self.db.get_session_scope_binding(target) or {}) != contract["scope"]:
                raise ValueError("assignment_scope_revision_changed")
        child = self.db.get_session(row["childSessionId"]) or {}
        if self.db.session_command_user_revision(row["childSessionId"]) != contract["targetUserRevision"]:
            raise ValueError("assignment_target_revision_changed")
        if (child.get("metadata") or {}).get("sessionCommandUserRevision", 0) != contract["targetIngressRevision"]:
            raise ValueError("assignment_target_revision_changed")
        workspace = contract["scope"]["workspacePath"]
        for key in ("readSet", "writeSet"):
            if _paths(contract["taskBrief"][key], workspace) != contract["taskBrief"][key]:
                raise ValueError("assignment_path_identity_changed")
        return row

    def _creation(self, *, session_id: str, user_id: str, context: dict[str, Any], state: dict[str, Any],
                  title: str, task: dict[str, Any], idempotency_key: str, authorization_quote: str) -> dict[str, Any]:
        from erc.session_coordination_service import _latest_human, _contains_secret

        if self.db.get_session_command_assignment_for_child(session_id):
            raise ValueError("assignment_root_only")
        if not idempotency_key.strip():
            raise ValueError("assignment_idempotency_key_required")
        latest, is_coordination = _latest_human(list(state.get("messages") or []))
        if is_coordination or _DENY_INTENT.search(latest):
            raise ValueError("assignment_user_authorization_required")
        original = self.db.get_session_command_assignment_by_idempotency(session_id, idempotency_key)
        creation_digest = _digest({"title": title, "task": task})
        if original:
            self.validate(original["assignmentId"], session_id=session_id, user_id=user_id, revision=original["revision"])
            if original["contract"]["creationDigest"] != creation_digest:
                raise ValueError("assignment_idempotency_conflict")
            return {"ok": True, "assignment": self.envelope(original), "idempotent": True}
        if not authorization_quote or authorization_quote not in latest or not _CREATE_INTENT.search(authorization_quote):
            raise ValueError("assignment_user_authorization_required")
        if _contains_secret(latest):
            raise ValueError("assignment_secret_in_charter")
        binding = self.db.get_session_scope_binding(session_id) or {}
        scope = _scope(binding)
        allowed = {"goal", "readSet", "writeSet", "expectedOutputs", "acceptanceContract", "constraints", "verificationMatrix", "proofExpectations"}
        if set(task) - allowed:
            raise ValueError("assignment_task_unknown_fields")
        if not str(task.get("goal") or "").strip() or not task.get("expectedOutputs") or not task.get("acceptanceContract"):
            raise ValueError("assignment_task_contract_required")
        for key in ("readSet", "writeSet"):
            if key not in task:
                raise ValueError("assignment_path_set_required")
        brief = deepcopy(task)
        for key in ("readSet", "writeSet"):
            brief[key] = _paths(brief[key], scope["workspacePath"])
        if any(key in context for key in ("allowed_write_paths", "allowedWritePaths")):
            parent_paths = _paths(context.get("allowed_write_paths", context.get("allowedWritePaths", [])), scope["workspacePath"])
            if any(not any(Path(path) == Path(parent) or (parent.endswith(os.sep) and Path(path).is_relative_to(Path(parent))) for parent in parent_paths) for path in brief["writeSet"]):
                raise ValueError("assignment_write_scope_exceeded")
        assignment_id = "assignment_" + uuid.uuid4().hex
        brief["taskBriefId"] = assignment_id
        brief["workspacePath"] = scope["workspacePath"]
        brief["readOnly"] = not bool(brief["writeSet"])
        brief = ensure_engineering_task_capsule(brief)
        capsule = brief.get("engineeringTaskCapsule") or {}
        if not capsule or capsule.get("contractStatus") != "valid":
            raise ValueError("assignment_capsule_invalid")
        contract = {
            "creationDigest": creation_digest, "scope": scope, "taskBrief": brief,
            "authorizationRef": "user-instruction:" + session_id + ":" + _digest(latest),
            "requirementRevision": _digest(latest), "userInstruction": latest,
            "targetUserRevision": self.db.session_command_user_revision(""), "targetIngressRevision": 0,
            "permittedControls": ["continue", "revoke"],
            "safetyApprovalMode": str(context.get("safety_approval_mode") or context.get("safetyApprovalMode") or "manual"),
        }
        created = session_lifecycle_service.create(
            {"title": title, "userId": user_id, "metadata": {"rootSessionId": session_id, "assignmentId": assignment_id}},
            binding=binding, database=self.db,
            assignment={"assignmentId": assignment_id, "rootSessionId": session_id,
                        "idempotencyKey": idempotency_key, "contract": contract},
        )
        row = created["assignment"]
        return {"ok": True, "assignment": self.envelope(row), "idempotent": created["idempotent"]}

    def command(self, *, mode: str, context: dict[str, Any], state: dict[str, Any], assignment_id: str = "",
                revision: int = 0, title: str = "", task: dict[str, Any] | None = None, content: str = "",
                idempotency_key: str = "", authorization_quote: str = "", after_id: str = "", limit: int = 20) -> dict[str, Any]:
        try:
            session_id, user_id, run_id = self._actor(context)
            if mode == "create":
                return self._creation(session_id=session_id, user_id=user_id, context=context, state=state,
                                      title=title, task=task or {}, idempotency_key=idempotency_key, authorization_quote=authorization_quote)
            if mode == "list":
                rows = session_lifecycle_service.list(root_session_id=session_id, limit=limit + 1, after_id=after_id, database=self.db)
                page = rows[:limit]
                return {"ok": True, "assignments": [self.envelope(row) for row in page],
                        "nextCursor": page[-1]["assignmentId"] if len(rows) > limit else None}
            if mode == "revoke":
                if not self.db.revoke_session_command_assignment(assignment_id, user_id=user_id, session_id=session_id, revision=revision):
                    raise ValueError("assignment_revoke_scope_or_revision_mismatch")
                return {"ok": True, "assignment": self.envelope(self.db.get_session_command_assignment(assignment_id))}
            if mode != "continue":
                raise ValueError("assignment_unsupported_mode")
            row = self.validate(assignment_id, session_id=session_id, user_id=user_id, revision=revision)
            if row["rootSessionId"] != session_id:
                raise ValueError("assignment_root_only")
            from erc.session_coordination_service import _latest_human, _contains_secret
            latest, is_coordination = _latest_human(list(state.get("messages") or []))
            if is_coordination or _DENY_INTENT.search(latest):
                raise ValueError("assignment_user_authorization_required")
            if not idempotency_key or not content.strip() or _contains_secret(content):
                raise ValueError("assignment_content_or_idempotency_invalid")
            digest = _digest({"assignmentId": assignment_id, "revision": revision, "content": content})
            message_key = f"assignment:{assignment_id}:{idempotency_key}"
            message = self.db.get_session_coordination_message_by_idempotency(message_key)
            if message and (message.get("metadata") or {}).get("assignmentCommandDigest") != digest:
                raise ValueError("assignment_idempotency_conflict")
            if not message:
                message = self.db.add_session_coordination_message(
                    message_id="coord_" + uuid.uuid4().hex, thread_id=assignment_id, message_type="request",
                    source_session_id=session_id, target_session_id=row["childSessionId"],
                    source_run_id=run_id, target_run_id=None, source_user_id=user_id,
                    intent="request", authority="project_assignment", content=content, summary=content,
                    context={}, evidence_refs=[], reply_to_message_id=None, reply_status=None, hop_count=1, max_hops=2,
                    state="queued", idempotency_key=message_key, authorized_at=utc_now_iso(),
                    metadata={"assignmentId": assignment_id, "assignmentRevision": revision,
                              "assignmentCommandDigest": digest, "replyRequired": True},
                )
            self.coordination._emit_transition(message, "session_coordination.queued")
            message = self.coordination.dispatch_message(message["id"]) or message
            return {"ok": True, "assignment": self.envelope(row), "message": self.coordination.compact_ref(message)}
        except (ValueError, OSError) as exc:
            return {"ok": False, "tool": "session_command_broker", "error": str(exc)}

    def assignment_for_message(self, message: dict[str, Any], *, session_id: str) -> dict[str, Any]:
        if message.get("authority") != "project_assignment":
            return {}
        meta = message.get("metadata") or {}
        row = self.validate(str(meta.get("assignmentId") or ""), session_id=session_id,
                            user_id=str(message.get("sourceUserId") or message.get("source_user_id") or ""),
                            revision=int(meta.get("assignmentRevision") or 0))
        if row["childSessionId"] != session_id or row["rootSessionId"] != message.get("sourceSessionId"):
            raise ValueError("assignment_message_scope_mismatch")
        return self.envelope(row)

    def execution_context(self, assignment: dict[str, Any], *, session_id: str, user_id: str) -> dict[str, Any]:
        row = self.validate(str(assignment.get("assignmentId") or ""), session_id=session_id,
                            user_id=user_id, revision=int(assignment.get("revision") or 0))
        if row["childSessionId"] != session_id:
            raise ValueError("assignment_execution_target_mismatch")
        envelope = self.envelope(row)
        brief = envelope["taskBrief"]
        capsule = brief["engineeringTaskCapsule"]
        return {"project_assignment": envelope, "task_brief": brief, "taskBrief": brief,
                "engineering_task_capsule": capsule, "engineering_capsule_mode": capsule["executionMode"],
                "allowed_write_paths": list(capsule["writeSet"])}


def validate_assignment_execution_context(context: dict[str, Any]) -> dict[str, Any]:
    assignment = context.get("project_assignment")
    if not assignment:
        return {}
    return SessionCommandService().execution_context(
        assignment, session_id=str(context.get("session_id") or context.get("sessionId") or ""),
        user_id=str(context.get("user_id") or context.get("userId") or ""),
    )
