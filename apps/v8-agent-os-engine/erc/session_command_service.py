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
                  title: str, task: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        from erc.session_coordination_service import _latest_human, _contains_secret

        if self.db.get_session_command_assignment_for_child(session_id):
            raise ValueError("assignment_root_only")
        if not idempotency_key.strip():
            raise ValueError("assignment_idempotency_key_required")
        latest, is_coordination = _latest_human(list(state.get("messages") or []))
        if not latest.strip() or is_coordination or _DENY_INTENT.search(latest):
            raise ValueError("assignment_user_authorization_required")
        original = self.db.get_session_command_assignment_by_idempotency(session_id, idempotency_key)
        creation_digest = _digest({"title": title, "task": task})
        if original:
            self.validate(original["assignmentId"], session_id=session_id, user_id=user_id, revision=original["revision"])
            if original["contract"]["creationDigest"] != creation_digest:
                raise ValueError("assignment_idempotency_conflict")
            return {"ok": True, "assignment": self.envelope(original), "idempotent": True}
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
                idempotency_key: str = "", after_id: str = "", after_cursor: int = 0, limit: int = 20,
                assignment_ids: list[str] | None = None, wait_for: str = "any") -> dict[str, Any]:
        try:
            session_id, user_id, run_id = self._actor(context)
            if mode == "create":
                return self._creation(session_id=session_id, user_id=user_id, context=context, state=state,
                                      title=title, task=task or {}, idempotency_key=idempotency_key)
            if mode == "list":
                rows = session_lifecycle_service.list(root_session_id=session_id, limit=limit + 1, after_id=after_id, database=self.db)
                page = rows[:limit]
                return {"ok": True, "assignments": [self.envelope(row) for row in page],
                        "nextCursor": page[-1]["assignmentId"] if len(rows) > limit else None}
            if mode == "results":
                rows = self.db.list_session_project_results(session_id, after_cursor=after_cursor, limit=limit + 1)
                if any(row.get("sourceUserId") != user_id for row in rows):
                    raise ValueError("project_result_owner_mismatch")
                page = rows[:limit]
                return {"ok": True, "results": [self.coordination.compact_ref(row) for row in page],
                        "afterCursor": after_cursor,
                        "nextCursor": page[-1]["metadata"]["resultCursor"] if page else after_cursor,
                        "hasMore": len(rows) > limit,
                        "deliveryAcknowledged": False}
            if mode == "await":
                result = self.db.register_session_result_wait(
                    session_id=session_id, run_id=run_id, user_id=user_id,
                    idempotency_key=idempotency_key, assignment_ids=assignment_ids or ([assignment_id] if assignment_id else []),
                    after_cursor=after_cursor, condition=wait_for,
                )
                return {"ok": True, "mode": "await", **result,
                        "results": [self.coordination.compact_ref(row) for row in result["results"]],
                        "summary": "等待项目任务结果。" if result["waiting"] else "所需结果已到达。"}
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
            # A peer result may inform a root's already-authorized work.
            # Its text neither creates nor revokes the persistent authority.
            if not is_coordination and _DENY_INTENT.search(latest):
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
            return {"ok": message.get("state") not in {"blocked", "failed", "cancelled", "expired"},
                    "assignment": self.envelope(row), "message": self.coordination.compact_ref(message)}
        except (ValueError, OSError) as exc:
            return {"ok": False, "tool": "session_command_broker", "error": str(exc)}

    def assignment_for_message(self, message: dict[str, Any], *, session_id: str) -> dict[str, Any]:
        persisted = self.db.get_session_coordination_message(str(message.get("messageId") or message.get("id") or ""))
        if not persisted or persisted.get("authority") != "project_assignment":
            if message.get("authority") == "project_assignment":
                raise ValueError("assignment_message_not_authorized")
            return {}
        for key in ("id", "messageId", "authority", "sourceSessionId", "targetSessionId", "sourceUserId", "content"):
            if key in message and message[key] != persisted.get(key):
                raise ValueError("assignment_message_payload_changed")
        message = persisted
        meta = message.get("metadata") or {}
        digest = _digest({"assignmentId": meta.get("assignmentId"), "revision": meta.get("assignmentRevision"),
                          "content": message["content"]})
        if digest != meta.get("assignmentCommandDigest"):
            raise ValueError("assignment_message_digest_changed")
        row = self.validate(str(meta.get("assignmentId") or ""), session_id=session_id,
                            user_id=str(message.get("sourceUserId") or message.get("source_user_id") or ""),
                            revision=int(meta.get("assignmentRevision") or 0))
        if row["childSessionId"] != session_id or row["rootSessionId"] != message.get("sourceSessionId"):
            raise ValueError("assignment_message_scope_mismatch")
        return self.envelope(row)

    def bind_run(self, message: dict[str, Any], *, run_id: str) -> None:
        assignment = self.assignment_for_message(message, session_id=message["targetSessionId"])
        if not assignment:
            return
        run = self.db.get_run_record(run_id)
        if not run or run.get("session_id") != assignment["childSessionId"] or run.get("user_id") != message["sourceUserId"]:
            raise ValueError("assignment_run_scope_mismatch")
        if run.get("status") not in {"running", "queued"} or message.get("targetRunId") != run_id:
            raise ValueError("assignment_run_not_active")
        previous = (run.get("metadata") or {}).get("sessionAssignment") or {}
        result = self.db.update_run_metadata_key_if_state(
            run_id, key="sessionAssignment", expected_state=previous.get("state") or "",
            expected_status=run["status"],
            next_value={"state": "active", "assignmentId": assignment["assignmentId"],
                        "revision": assignment["revision"], "messageId": message["messageId"]},
        )
        if not result.get("updated"):
            raise ValueError("assignment_run_binding_conflict")

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
    session_id = str(context.get("session_id") or context.get("sessionId") or "")
    user_id = str(context.get("user_id") or context.get("userId") or "")
    run_id = str(context.get("run_id") or context.get("runId") or "")
    run = db.get_run_record(run_id) if run_id else None
    binding = ((run or {}).get("metadata") or {}).get("sessionAssignment")
    if not binding:
        if context.get("project_assignment"):
            raise ValueError("assignment_run_binding_required")
        return {}
    if run.get("session_id") != session_id or run.get("user_id") != user_id or run.get("status") not in {"running", "queued"}:
        raise ValueError("assignment_run_scope_mismatch")
    service = SessionCommandService()
    message = db.get_session_coordination_message(str(binding.get("messageId") or ""))
    if not message or message.get("targetRunId") != run_id:
        raise ValueError("assignment_run_message_mismatch")
    if message.get("state") not in {"promoted", "injected", "replied"}:
        raise ValueError("assignment_run_message_inactive")
    assignment = service.assignment_for_message(message, session_id=session_id)
    if not assignment or assignment["assignmentId"] != binding.get("assignmentId") or assignment["revision"] != binding.get("revision"):
        raise ValueError("assignment_run_binding_mismatch")
    claimed = context.get("project_assignment") or {}
    if claimed and (claimed.get("assignmentId") != assignment["assignmentId"] or claimed.get("revision") != assignment["revision"]):
        raise ValueError("assignment_runtime_projection_mismatch")
    workspace = assignment["scope"]["workspacePath"]
    actual_workspace = str(context.get("workspace_path") or context.get("workspacePath") or "")
    if not actual_workspace or Path(actual_workspace).resolve() != Path(workspace):
        raise ValueError("assignment_runtime_workspace_changed")
    verified = service.execution_context(assignment, session_id=session_id, user_id=user_id)
    # A descendant's explicit narrower set remains narrower; a projection can
    # never replace a stored Capsule with a wider model-provided one.
    if str(context.get("runtime_kind") or "") in {"subagent", "delegation"}:
        requested = _paths(context.get("allowed_write_paths", context.get("allowedWritePaths", [])), workspace)
        permitted = verified["allowed_write_paths"]
        if any(not any(Path(path) == Path(parent) or (parent.endswith(os.sep) and Path(path).is_relative_to(Path(parent)))
                       for parent in permitted) for path in requested):
            raise ValueError("assignment_write_scope_exceeded")
        verified["allowed_write_paths"] = requested
        if context.get("engineering_capsule_mode") in {"read_only", "verify"}:
            verified["engineering_capsule_mode"] = context["engineering_capsule_mode"]
        for key in ("task_brief", "taskBrief", "engineering_task_capsule"):
            if key in context:
                verified[key] = context[key]
    return verified
