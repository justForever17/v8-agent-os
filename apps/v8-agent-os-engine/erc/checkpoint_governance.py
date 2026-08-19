from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.database import db
from core.engine_config_resolver import require_engine_config, resolve_engine_config_for_role
from core.v8_agent_os_paths import CHECKPOINT_DB_PATH


CHECKPOINT_OPERATION_TABLE = "v8_checkpoint_operations"
CHECKPOINT_OPERATION_MODES = {"replay", "fork"}
CHECKPOINT_OPERATION_ACTIVE_RUN_STATES = {
    "queued",
    "running",
    "waiting_approval",
    "waiting_input",
    "waiting_external_tool",
    "paused",
    "interrupted",
}


def __getattr__(name: str):
    if name == "supervisor_runner":
        from agents.runners.supervisor_runner import supervisor_runner

        return supervisor_runner
    raise AttributeError(name)


def _get_supervisor_runner():
    from agents.runners.supervisor_runner import supervisor_runner

    return supervisor_runner


def _agent_state_fields() -> set[str]:
    from graph.supervisor import AgentState

    return set(AgentState.__annotations__)


_FORBIDDEN_STATE_PATCH_KEYS = {
    "session_id",
    "sessionId",
    "run_id",
    "runId",
    "workspace_id",
    "workspaceId",
    "workspace_path",
    "workspacePath",
    "project_id",
    "projectId",
    "resolved_scope",
    "resolvedScope",
    "safety_approval_mode",
    "safetyApprovalMode",
    "plugin_authorizations",
    "pluginAuthorizations",
    "context_session_refs",
    "contextSessionRefs",
    "session_coordination",
    "sessionCoordination",
}

_ROUTE_CONTEXT_OPERATIONS_KEY = "$operations"
_ROUTE_CONTEXT_AUTHORITY_KEYS = {
    "session_id",
    "sessionId",
    "run_id",
    "runId",
    "user_id",
    "userId",
    "workspace_id",
    "workspaceId",
    "workspace_path",
    "workspacePath",
    "workspaceBinding",
    "project_id",
    "projectId",
    "resolved_scope",
    "resolvedScope",
    "repository_root",
    "repositoryRoot",
    "original_workspace_path",
    "originalWorkspacePath",
    "worktree_root",
    "worktreeRoot",
    "worktree_id",
    "worktreeId",
    "sandbox_lease_id",
    "sandboxLeaseId",
    "sandbox_policy",
    "sandboxPolicy",
    "sandbox_policy_digest",
    "sandboxPolicyDigest",
    "delegation_id",
    "delegationId",
    "parent_delegation_id",
    "parentDelegationId",
    "root_delegation_id",
    "rootDelegationId",
    "delegation_depth",
    "delegationDepth",
    "agent_id",
    "agentId",
    "actor_role",
    "actorRole",
    "runtime_kind",
    "runtimeKind",
    "root_episode_id",
    "rootEpisodeId",
    "root_run_id",
    "rootRunId",
    "safety_approval_mode",
    "safetyApprovalMode",
    "plugin_authorizations",
    "pluginAuthorizations",
    "context_session_refs",
    "contextSessionRefs",
    "session_coordination",
    "sessionCoordination",
    "runtimeAccess",
    "runtimeAllowed",
    "runtimeExecutionAllowed",
}
_ROUTE_CONTEXT_NESTED_AUTHORITY_KEYS = {
    "schemaVersion",
    "schema_version",
    "engineeringTaskCapsule",
    "engineering_task_capsule",
    "toolPolicy",
    "tool_policy",
    "allowedTools",
    "allowed_tools",
    "forbiddenTools",
    "forbidden_tools",
    "noTools",
    "no_tools",
    "runtimeAccess",
    "runtime_access",
    "pluginReferences",
    "plugin_references",
    "pluginAuthorizations",
    "plugin_authorizations",
    "grantGroups",
    "grant_groups",
    "sideEffectPolicy",
    "side_effect_policy",
    "delegationPolicy",
    "delegation_policy",
    "allowChildDelegation",
    "allow_child_delegation",
    "childDelegationBudget",
    "child_delegation_budget",
    "writeSet",
    "write_set",
    "readSet",
    "read_set",
    "writeRequired",
    "write_required",
    "readOnly",
    "read_only",
    "writeSetPartitions",
    "write_set_partitions",
    "requiredCapabilities",
    "required_capabilities",
    "constraints",
    "behaviorScope",
    "behavior_scope",
    "budget",
    "executionBudget",
    "execution_budget",
    "targetAgentName",
    "target_agent_name",
    "preferredAgentId",
    "preferred_agent_id",
    "selectedBaselineTools",
    "selectedRuntimeTools",
    "resolvedToolNames",
}
_ROUTE_CONTEXT_COLLABORATION_COLLECTION_KEYS = {
    "capabilityEpisodes",
    "handoffRefs",
    "effectiveHandoffRefs",
    "runtimeDeliveryDiagnostics",
}
_ROUTE_CONTEXT_TASK_COLLECTION_KEYS = {"taskBriefs", "workerBriefs"}
_ROUTE_CONTEXT_TASK_OBJECT_KEYS = {"taskBrief", "routeBrief", "workerBrief"}
_ROUTE_CONTEXT_POLICY_SUBTREE_KEYS = {
    "engineeringTaskCapsule",
    "engineering_task_capsule",
    "toolPolicy",
    "tool_policy",
    "delegationPolicy",
    "delegation_policy",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _encode_state_patch(value: dict[str, Any]) -> str:
    from erc.checkpoint_security import build_checkpoint_serializer, strict_checkpoint_serializer

    serializer = build_checkpoint_serializer()
    strict_checkpoint_serializer(serializer).assert_write_safe(value, root="checkpoint_governance.state_patch")
    serialization_type, payload = serializer.dumps_typed(value)
    return _json_dump(
        {
            "version": 1,
            "type": serialization_type,
            "payload": base64.b64encode(payload).decode("ascii"),
        }
    )


def _decode_state_patch(value: str) -> dict[str, Any]:
    envelope = json.loads(value or "{}")
    if not isinstance(envelope, dict):
        raise CheckpointGovernanceError("checkpoint statePatch envelope is invalid.")
    if {"type", "payload"}.issubset(envelope):
        from erc.checkpoint_security import build_checkpoint_serializer

        try:
            payload = base64.b64decode(str(envelope["payload"]).encode("ascii"), validate=True)
        except (ValueError, UnicodeError) as exc:
            raise CheckpointGovernanceError("checkpoint statePatch payload is invalid.") from exc
        restored = build_checkpoint_serializer().loads_typed((str(envelope["type"]), payload))
        if not isinstance(restored, dict):
            raise CheckpointGovernanceError("checkpoint statePatch root is invalid.")
        return restored
    # Compatibility for operation drafts created by an earlier unshipped build.
    return envelope


class CheckpointGovernanceError(RuntimeError):
    pass


class CheckpointGovernanceService:
    """Approval-gated replay/fork without exposing raw checkpoint state."""

    def __init__(self, checkpoint_path: Path = CHECKPOINT_DB_PATH) -> None:
        self._checkpoint_path = checkpoint_path
        self._schema_lock = threading.RLock()
        self._scheduled_lock = threading.RLock()
        self._scheduled: set[str] = set()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._checkpoint_path, timeout=120)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=120000")
        return conn

    def _ensure_schema(self) -> None:
        with self._schema_lock, self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {CHECKPOINT_OPERATION_TABLE} (
                    operation_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    source_thread_id TEXT NOT NULL,
                    source_checkpoint_id TEXT NOT NULL,
                    source_checkpoint_fingerprint TEXT NOT NULL,
                    target_session_id TEXT,
                    target_thread_id TEXT,
                    user_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    as_node TEXT NOT NULL,
                    state_patch_json TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{CHECKPOINT_OPERATION_TABLE}_source "
                f"ON {CHECKPOINT_OPERATION_TABLE}(source_session_id, created_at DESC)"
            )
            conn.commit()

    def _checkpoint_fingerprint(self, thread_id: str, checkpoint_id: str, checkpoint_ns: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT parent_checkpoint_id, type, checkpoint, metadata
                FROM checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                """,
                (thread_id, checkpoint_ns, checkpoint_id),
            ).fetchone()
        if row is None:
            raise CheckpointGovernanceError("指定的 checkpoint 不存在。")
        digest = hashlib.sha256()
        digest.update(str(row["parent_checkpoint_id"] or "").encode("utf-8"))
        digest.update(str(row["type"] or "").encode("utf-8"))
        digest.update(bytes(row["checkpoint"] or b""))
        digest.update(bytes(row["metadata"] or b""))
        return digest.hexdigest()

    @staticmethod
    def _source_thread_id(session_id: str) -> str:
        binding = db.get_session_scope_binding(session_id) or {}
        thread_id = str(binding.get("thread_id") or "").strip()
        if thread_id:
            return thread_id
        for run in db.list_run_records(session_id=session_id, limit=100):
            thread_id = str(run.get("thread_id") or "").strip()
            if thread_id:
                return thread_id
        return session_id

    @staticmethod
    def _assert_source_idle(session_id: str, *, exclude_run_id: str = "") -> None:
        active = [
            run
            for run in db.list_run_records(session_id=session_id, limit=200)
            if str(run.get("id") or "") != exclude_run_id
            and str(run.get("status") or "").strip().lower() in CHECKPOINT_OPERATION_ACTIVE_RUN_STATES
        ]
        if active:
            raise CheckpointGovernanceError("源任务仍在运行或等待人工处理，checkpoint 分支操作已阻断。")

    @staticmethod
    def _normalize_state_patch(mode: str, patch: Any) -> dict[str, Any]:
        if patch in (None, {}):
            return {}
        if mode != "fork" or not isinstance(patch, dict):
            raise CheckpointGovernanceError("只有 fork 允许提交结构化 statePatch。")
        unknown = set(patch) - _agent_state_fields()
        if unknown:
            raise CheckpointGovernanceError(f"statePatch 包含未知状态字段: {', '.join(sorted(unknown))}")
        forbidden = set(patch) & _FORBIDDEN_STATE_PATCH_KEYS
        if forbidden:
            raise CheckpointGovernanceError(f"statePatch 不得修改权限或身份字段: {', '.join(sorted(forbidden))}")
        normalized = dict(patch)
        if "current_route_context" not in normalized:
            return normalized
        route_patch = normalized.get("current_route_context")
        if not isinstance(route_patch, dict):
            raise CheckpointGovernanceError("statePatch.current_route_context 必须是结构化对象。")
        normalized_route_patch = dict(route_patch)
        raw_operations = normalized_route_patch.get(_ROUTE_CONTEXT_OPERATIONS_KEY, [])
        if not isinstance(raw_operations, list):
            raise CheckpointGovernanceError("current_route_context.$operations 必须是操作列表。")
        operations: list[dict[str, Any]] = []
        for index, raw_operation in enumerate(raw_operations):
            if not isinstance(raw_operation, dict):
                raise CheckpointGovernanceError(f"route context 操作 {index} 必须是结构化对象。")
            if set(raw_operation) != {"op", "path"} or str(raw_operation.get("op") or "").strip() != "clear":
                raise CheckpointGovernanceError(f"route context 操作 {index} 只支持 typed clear(op/path)。")
            raw_path = raw_operation.get("path")
            if isinstance(raw_path, str):
                path = [raw_path]
            elif isinstance(raw_path, list):
                path = list(raw_path)
            else:
                path = []
            if not path or any(not isinstance(segment, str) or not segment.strip() for segment in path):
                raise CheckpointGovernanceError(f"route context 操作 {index} 的 path 必须是非空字符串路径。")
            normalized_path = [segment.strip() for segment in path]
            if _ROUTE_CONTEXT_OPERATIONS_KEY in normalized_path:
                raise CheckpointGovernanceError(f"route context 操作 {index} 不得修改操作信封。")
            operations.append({"op": "clear", "path": normalized_path})
        if raw_operations or _ROUTE_CONTEXT_OPERATIONS_KEY in normalized_route_patch:
            normalized_route_patch[_ROUTE_CONTEXT_OPERATIONS_KEY] = operations
        normalized["current_route_context"] = normalized_route_patch
        return normalized

    @staticmethod
    def _infer_as_node(snapshot: Any, requested: str = "", graph: Any = None) -> str:
        metadata = dict(getattr(snapshot, "metadata", None) or {})
        writes = metadata.get("writes") if isinstance(metadata.get("writes"), dict) else {}
        candidates = [str(key) for key in writes if str(key) not in {"__start__", "__input__"}]
        normalized_requested = str(requested or "").strip()
        if normalized_requested:
            if graph is not None:
                graph_nodes = {str(node) for node in graph.get_graph().nodes}
                if normalized_requested not in graph_nodes and normalized_requested != "__start__":
                    raise CheckpointGovernanceError("asNode 不是当前 Supervisor 图中的有效节点。")
            elif candidates and normalized_requested not in candidates:
                raise CheckpointGovernanceError("asNode 与 checkpoint 的实际写入节点不一致。")
            return normalized_requested
        if len(candidates) == 1:
            return candidates[0]
        if str(metadata.get("source") or "").strip().lower() == "input":
            return "__start__"
        next_nodes = list(getattr(snapshot, "next", ()) or ())
        if len(next_nodes) == 1 and graph is not None:
            graph_view = graph.get_graph()
            predecessors = {
                str(edge.source)
                for edge in graph_view.edges
                if str(edge.target) == str(next_nodes[0]) and str(edge.source) != "__start__"
            }
            if len(predecessors) == 1:
                return next(iter(predecessors))
        # Modern LangGraph checkpoints no longer guarantee a metadata.writes map.
        # An empty value means "let update_state infer the latest writer".  We
        # intentionally do not guess when a production Supervisor graph has
        # parallel predecessors; an ambiguous inference will fail closed inside
        # LangGraph instead of resuming from a fabricated node.
        return ""

    async def plan(
        self,
        *,
        mode: str,
        source_session_id: str,
        source_checkpoint_id: str,
        user_id: str = "",
        state_patch: dict[str, Any] | None = None,
        as_node: str = "",
    ) -> dict[str, Any]:
        self._ensure_schema()
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in CHECKPOINT_OPERATION_MODES:
            raise CheckpointGovernanceError("mode 只支持 replay 或 fork。")
        session_id = str(source_session_id or "").strip()
        checkpoint_id = str(source_checkpoint_id or "").strip()
        session = db.get_session(session_id)
        if not session:
            raise CheckpointGovernanceError("源任务不存在。")
        owner_id = str(session.get("user_id") or "anonymous")
        if str(user_id or "").strip() and str(user_id).strip() != owner_id:
            raise CheckpointGovernanceError("checkpoint 操作不能跨用户执行。")
        self._assert_source_idle(session_id)
        thread_id = self._source_thread_id(session_id)
        source_fingerprint = self._checkpoint_fingerprint(thread_id, checkpoint_id)
        patch = self._normalize_state_patch(normalized_mode, state_patch)

        engine_config = require_engine_config(
            resolve_engine_config_for_role("supervisor"),
            role="supervisor",
        )
        graph, _ = await _get_supervisor_runner().build_graph(engine_config)
        source_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }
        snapshot = await graph.aget_state(source_config)
        resolved_as_node = self._infer_as_node(snapshot, requested=as_node, graph=graph)
        operation_id = f"checkpoint_op_{uuid.uuid4().hex}"
        run_id = f"run_{operation_id}"
        approval_id = f"approval_{operation_id}"
        target_session_id = session_id if normalized_mode == "replay" else f"checkpoint-fork-{uuid.uuid4().hex}"
        target_thread_id = thread_id if normalized_mode == "replay" else target_session_id
        plan_payload = {
            "mode": normalized_mode,
            "sourceSessionId": session_id,
            "sourceThreadId": thread_id,
            "sourceCheckpointId": checkpoint_id,
            "sourceCheckpointFingerprint": source_fingerprint,
            "targetSessionId": target_session_id,
            "targetThreadId": target_thread_id,
            "asNode": resolved_as_node,
            "statePatch": patch,
        }
        plan_digest = hashlib.sha256(_json_dump(plan_payload).encode("utf-8")).hexdigest()
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {CHECKPOINT_OPERATION_TABLE}
                    (operation_id, mode, state, source_session_id, source_thread_id,
                     source_checkpoint_id, source_checkpoint_fingerprint,
                     target_session_id, target_thread_id, user_id, run_id, approval_id,
                     as_node, state_patch_json, plan_digest, created_at, updated_at)
                VALUES (?, ?, 'awaiting_approval', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    normalized_mode,
                    session_id,
                    thread_id,
                    checkpoint_id,
                    source_fingerprint,
                    target_session_id,
                    target_thread_id,
                    owner_id,
                    run_id,
                    approval_id,
                    resolved_as_node,
                    _encode_state_patch(patch),
                    plan_digest,
                    now,
                    now,
                ),
            )
            conn.commit()

        db.create_run_record(
            run_id,
            session_id,
            thread_id=thread_id,
            user_id=owner_id,
            run_type=f"checkpoint_{normalized_mode}",
            status="waiting_approval",
            trigger_source="checkpoint_governance",
            metadata={"operationId": operation_id, "planDigest": plan_digest},
        )
        db.add_pending_approval(
            approval_id,
            session_id,
            run_id,
            f"checkpoint_{normalized_mode}",
            "pending",
            {
                "operationId": operation_id,
                "approvalKind": f"checkpoint_{normalized_mode}",
                "summary": "将从历史恢复点重新执行后续步骤。后续模型调用、工具和人工中断都会再次发生。",
                "sourceCheckpoint": checkpoint_id,
                "targetSessionId": target_session_id,
                "planDigest": plan_digest,
                "risk": "high",
            },
        )
        return self.get_operation(operation_id)

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        self._ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {CHECKPOINT_OPERATION_TABLE} WHERE operation_id = ?",
                (str(operation_id or "").strip(),),
            ).fetchone()
        if row is None:
            raise CheckpointGovernanceError("checkpoint 操作不存在。")
        item = dict(row)
        result = json.loads(item.get("result_json") or "{}")
        return {
            "operationId": item["operation_id"],
            "mode": item["mode"],
            "state": item["state"],
            "sourceSessionId": item["source_session_id"],
            "sourceCheckpointId": item["source_checkpoint_id"],
            "targetSessionId": item.get("target_session_id"),
            "runId": item["run_id"],
            "approvalId": item["approval_id"],
            "planDigest": item["plan_digest"],
            "result": result,
            "errorCode": item.get("error_code"),
            "errorMessage": item.get("error_message"),
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    def _operation_row(self, operation_id: str) -> dict[str, Any]:
        self._ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {CHECKPOINT_OPERATION_TABLE} WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise CheckpointGovernanceError("checkpoint 操作不存在。")
        item = dict(row)
        item["state_patch"] = _decode_state_patch(item.get("state_patch_json") or "{}")
        return item

    def _update_operation(
        self,
        operation_id: str,
        *,
        state: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE {CHECKPOINT_OPERATION_TABLE}
                SET state=?, result_json=COALESCE(?, result_json), error_code=?,
                    error_message=?, updated_at=?
                WHERE operation_id=?
                """,
                (
                    state,
                    _json_dump(result) if result is not None else None,
                    error_code,
                    error_message,
                    _utc_now(),
                    operation_id,
                ),
            )
            conn.commit()

    @staticmethod
    def _route_context_authority(
        values: dict[str, Any],
        *,
        session_id: str,
        run_id: str,
        user_id: str = "",
        scope_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_route = dict(values.get("current_route_context") or {})
        authority = {
            key: source_route[key]
            for key in _ROUTE_CONTEXT_AUTHORITY_KEYS
            if key in source_route
        }
        for key in _ROUTE_CONTEXT_AUTHORITY_KEYS:
            if key not in authority and key in values:
                authority[key] = values[key]

        def bind(aliases: tuple[str, ...], value: Any) -> None:
            if value is None or (isinstance(value, str) and not value.strip()):
                return
            for alias in aliases:
                authority[alias] = value

        def bind_existing(aliases: tuple[str, ...]) -> None:
            for alias in aliases:
                if alias in authority:
                    bind(aliases, authority[alias])
                    return

        binding = dict(scope_binding or {})
        for aliases in (
            ("delegation_id", "delegationId"),
            ("parent_delegation_id", "parentDelegationId"),
            ("root_delegation_id", "rootDelegationId"),
            ("delegation_depth", "delegationDepth"),
            ("agent_id", "agentId"),
            ("actor_role", "actorRole"),
            ("runtime_kind", "runtimeKind"),
            ("root_episode_id", "rootEpisodeId"),
            ("root_run_id", "rootRunId"),
        ):
            bind_existing(aliases)
        bind(("session_id", "sessionId"), session_id)
        bind(("run_id", "runId"), run_id)
        bind(("user_id", "userId"), user_id or binding.get("user_id") or binding.get("userId"))
        bind(
            ("workspace_id", "workspaceId"),
            binding.get("workspace_id") or binding.get("workspaceId"),
        )
        bind(
            ("workspace_path", "workspacePath"),
            binding.get("workspace_path") or binding.get("workspacePath"),
        )
        bind(("project_id", "projectId"), binding.get("project_id") or binding.get("projectId"))
        bind(
            ("resolved_scope", "resolvedScope"),
            binding.get("resolved_scope") or binding.get("resolvedScope"),
        )
        if isinstance(source_route.get("workspaceBinding"), dict):
            workspace_binding = dict(source_route["workspaceBinding"])
            workspace_binding.update(
                {
                    "workspaceId": binding.get("workspace_id") or binding.get("workspaceId"),
                    "projectId": binding.get("project_id") or binding.get("projectId"),
                    "activeWorkspaceRoot": binding.get("workspace_path") or binding.get("workspacePath"),
                }
            )
            authority["workspaceBinding"] = {
                key: value
                for key, value in workspace_binding.items()
                if value is not None and value != ""
            }
        authority.update(
            {
                "plugin_authorizations": [],
                "pluginAuthorizations": [],
                "context_session_refs": [],
                "contextSessionRefs": [],
                "session_coordination": {},
                "sessionCoordination": {},
            }
        )
        return authority

    @staticmethod
    def _merge_route_context_patch(
        base: dict[str, Any],
        patch: dict[str, Any] | None,
        authority: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        merged = deepcopy(base)
        diagnostics: list[dict[str, Any]] = []
        normalized_patch = dict(patch or {})
        operations = list(normalized_patch.pop(_ROUTE_CONTEXT_OPERATIONS_KEY, []) or [])

        if patch == {}:
            diagnostics.append(
                {
                    "type": "checkpoint_route_context_empty_patch_preserved",
                    "operation": "merge",
                    "path": "current_route_context",
                    "resolution": "source_context_preserved",
                }
            )

        def authority_conflict(key: str, operation: str) -> None:
            diagnostics.append(
                {
                    "type": "checkpoint_route_context_authority_conflict",
                    "operation": operation,
                    "path": f"current_route_context.{key}",
                    "resolution": "engine_authoritative_value_preserved",
                    "authoritativeValuePresent": key in authority,
                }
            )

        def protected_path(path: list[str]) -> bool:
            if not path:
                return False
            if len(path) == 1:
                return path[0] in _ROUTE_CONTEXT_AUTHORITY_KEYS

            def task_object(candidate: list[str]) -> bool:
                if not candidate:
                    return False
                return candidate[-1] in _ROUTE_CONTEXT_TASK_OBJECT_KEYS or (
                    candidate[-1].startswith("item-")
                    and any(
                        segment in _ROUTE_CONTEXT_TASK_COLLECTION_KEYS
                        for segment in candidate[:-1]
                    )
                )

            if any(
                segment in _ROUTE_CONTEXT_AUTHORITY_KEYS
                for segment in path[1:]
            ) and any(
                segment in _ROUTE_CONTEXT_TASK_OBJECT_KEYS
                or segment in _ROUTE_CONTEXT_TASK_COLLECTION_KEYS
                for segment in path[:-1]
            ):
                return True
            if task_object(path[:-1]) and path[-1] in _ROUTE_CONTEXT_NESTED_AUTHORITY_KEYS:
                return True
            for index, segment in enumerate(path):
                if (
                    segment in _ROUTE_CONTEXT_POLICY_SUBTREE_KEYS
                    and task_object(path[:index])
                ):
                    return True
            return False

        def nested_authority_conflict(path: list[str], operation: str) -> None:
            diagnostics.append(
                {
                    "type": "checkpoint_route_context_authority_conflict",
                    "operation": operation,
                    "path": ".".join(["current_route_context", *path]),
                    "resolution": "source_execution_authority_preserved",
                    "authoritativeValuePresent": True,
                }
            )

        def collaboration_identity(key: str, value: Any) -> str:
            if not isinstance(value, dict):
                return ""
            if key == "capabilityEpisodes":
                identity = str(
                    value.get("episodeId")
                    or value.get("episode_id")
                    or value.get("needId")
                    or value.get("need_id")
                    or value.get("id")
                    or ""
                ).strip()
                return f"episode:{identity}" if identity else ""
            if key in {"handoffRefs", "effectiveHandoffRefs"}:
                identity = str(
                    value.get("handoffId")
                    or value.get("handoffRefId")
                    or value.get("id")
                    or ""
                ).strip()
                return f"handoff:{identity}" if identity else ""
            key_groups = {
                "runtimeDeliveryDiagnostics": (
                    "diagnosticId",
                    "id",
                    "resultRef",
                    "episodeId",
                ),
            }
            for identity_key in key_groups.get(key, ("id",)):
                identity = str(value.get(identity_key) or "").strip()
                if identity:
                    return f"diagnostic:{identity}"
            return ""

        def empty_overwrite_operation(value: Any) -> str:
            if isinstance(value, str) and not value.strip():
                return "set_empty_string"
            if isinstance(value, dict) and not value:
                return "set_empty_object"
            if isinstance(value, (list, tuple, set)) and not value:
                return "set_empty_list"
            return ""

        def carries_information(value: Any) -> bool:
            if value is None:
                return False
            return not bool(empty_overwrite_operation(value))

        def merge_mapping(target: dict[str, Any], overlay: dict[str, Any], path: list[str]) -> None:
            for key, value in overlay.items():
                if not path and key in _ROUTE_CONTEXT_AUTHORITY_KEYS:
                    if key not in authority or value != authority.get(key):
                        authority_conflict(key, "set")
                    continue
                item_path = [*path, key]
                path_text = ".".join(["current_route_context", *item_path])
                if path and protected_path(item_path):
                    nested_authority_conflict(item_path, "set")
                    continue
                if value is None:
                    diagnostics.append(
                        {
                            "type": "checkpoint_route_context_implicit_clear_ignored",
                            "operation": "set_null",
                            "path": path_text,
                            "resolution": "explicit_clear_required",
                        }
                    )
                    continue
                empty_operation = empty_overwrite_operation(value)
                if (
                    empty_operation
                    and key in target
                    and carries_information(target.get(key))
                ):
                    diagnostics.append(
                        {
                            "type": "checkpoint_route_context_implicit_clear_ignored",
                            "operation": empty_operation,
                            "path": path_text,
                            "resolution": "explicit_clear_required",
                        }
                    )
                    continue
                current = target.get(key)
                if (
                    key in _ROUTE_CONTEXT_COLLABORATION_COLLECTION_KEYS
                    and isinstance(value, list)
                    and isinstance(current, list)
                    and value
                ):
                    merged_items = deepcopy(current)
                    indexes_by_identity = {
                        identity: index
                        for index, item in enumerate(merged_items)
                        if (identity := collaboration_identity(key, item))
                    }
                    source_count = len(merged_items)
                    for incoming in value:
                        identity = collaboration_identity(key, incoming)
                        existing_index = indexes_by_identity.get(identity) if identity else None
                        if existing_index is not None and isinstance(incoming, dict):
                            existing = merged_items[existing_index]
                            if isinstance(existing, dict):
                                updated = deepcopy(existing)
                                merge_mapping(
                                    updated,
                                    incoming,
                                    [*item_path, f"item-{existing_index}"],
                                )
                                merged_items[existing_index] = updated
                                continue
                        if incoming not in merged_items:
                            merged_items.append(deepcopy(incoming))
                            if identity:
                                indexes_by_identity[identity] = len(merged_items) - 1
                    target[key] = merged_items
                    diagnostics.append(
                        {
                            "type": "checkpoint_route_context_collection_merged",
                            "operation": "merge_by_identity",
                            "path": path_text,
                            "sourceCount": source_count,
                            "patchCount": len(value),
                            "resultCount": len(merged_items),
                            "resolution": "source_items_preserved",
                        }
                    )
                    continue
                if key in _ROUTE_CONTEXT_TASK_COLLECTION_KEYS and isinstance(value, list):
                    current_items = list(current) if isinstance(current, list) else []
                    merged_items = deepcopy(current_items)

                    def task_identity(item: Any) -> str:
                        if not isinstance(item, dict):
                            return ""
                        return str(
                            item.get("taskBriefId")
                            or item.get("task_brief_id")
                            or item.get("id")
                            or ""
                        ).strip()

                    indexes_by_identity = {
                        identity: index
                        for index, item in enumerate(merged_items)
                        if (identity := task_identity(item))
                    }
                    for incoming in value:
                        identity = task_identity(incoming)
                        existing_index = indexes_by_identity.get(identity) if identity else None
                        if isinstance(incoming, dict):
                            if existing_index is not None and isinstance(
                                merged_items[existing_index], dict
                            ):
                                candidate = deepcopy(merged_items[existing_index])
                                candidate_index = existing_index
                            else:
                                candidate = {}
                                candidate_index = len(merged_items)
                            merge_mapping(
                                candidate,
                                incoming,
                                [*item_path, f"item-{candidate_index}"],
                            )
                            if existing_index is not None:
                                merged_items[existing_index] = candidate
                            elif candidate:
                                merged_items.append(candidate)
                                if identity:
                                    indexes_by_identity[identity] = len(merged_items) - 1
                        elif incoming not in merged_items:
                            merged_items.append(deepcopy(incoming))
                    target[key] = merged_items
                    diagnostics.append(
                        {
                            "type": "checkpoint_route_context_collection_merged",
                            "operation": "merge_task_contracts_by_identity",
                            "path": path_text,
                            "sourceCount": len(current_items),
                            "patchCount": len(value),
                            "resultCount": len(merged_items),
                            "resolution": "cognition_merged_execution_authority_preserved",
                        }
                    )
                    continue
                if isinstance(value, dict):
                    nested = dict(current) if isinstance(current, dict) else {}
                    merge_mapping(nested, value, item_path)
                    if value or key not in target:
                        target[key] = nested
                    continue
                target[key] = value

        merge_mapping(merged, normalized_patch, [])

        for operation in operations:
            path = list(operation.get("path") or [])
            root_key = str(path[0])
            if root_key in _ROUTE_CONTEXT_AUTHORITY_KEYS:
                authority_conflict(root_key, "clear")
                continue
            if protected_path(path):
                nested_authority_conflict(path, "clear")
                continue
            cursor = merged
            traversable = True
            for segment in path[:-1]:
                child = cursor.get(segment)
                if not isinstance(child, dict):
                    traversable = False
                    break
                cursor = child
            applied = traversable and path[-1] in cursor
            if applied:
                cursor.pop(path[-1], None)
            diagnostics.append(
                {
                    "type": "checkpoint_route_context_clear",
                    "operation": "clear",
                    "path": ".".join(["current_route_context", *path]),
                    "applied": applied,
                    "resolution": "removed" if applied else "already_absent",
                }
            )

        merged.update(authority)
        return merged, diagnostics

    @classmethod
    def _identity_patch(
        cls,
        values: dict[str, Any],
        *,
        session_id: str,
        run_id: str,
        user_id: str = "",
        scope_binding: dict[str, Any] | None = None,
        route_context_authority: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        route_context = dict(values.get("current_route_context") or {})
        route_context.update(
            route_context_authority
            or cls._route_context_authority(
                values,
                session_id=session_id,
                run_id=run_id,
                user_id=user_id,
                scope_binding=scope_binding,
            )
        )
        return {
            "session_id": session_id,
            "sessionId": session_id,
            "run_id": run_id,
            "runId": run_id,
            "current_route_context": route_context,
            "plugin_authorizations": [],
            "pluginAuthorizations": [],
            "context_session_refs": [],
            "contextSessionRefs": [],
            "session_coordination": {},
            "sessionCoordination": {},
        }

    @staticmethod
    def _create_fork_session(operation: dict[str, Any], source_session: dict[str, Any]) -> None:
        target_session_id = str(operation["target_session_id"])
        metadata = dict(source_session.get("metadata") or {})
        metadata.update(
            {
                "checkpointFork": True,
                "checkpointForkSourceSessionId": operation["source_session_id"],
                "checkpointForkSourceCheckpointId": operation["source_checkpoint_id"],
                "checkpointForkOperationId": operation["operation_id"],
            }
        )
        db.create_or_update_session(
            target_session_id,
            f"{source_session.get('title') or '任务'} · 分支",
            user_id=str(source_session.get("user_id") or "anonymous"),
            agent_id=source_session.get("agent_id"),
            metadata=metadata,
        )
        binding = db.get_session_scope_binding(str(operation["source_session_id"]))
        if binding:
            copied = dict(binding)
            copied.update(
                {
                    "session_id": target_session_id,
                    "conversation_id": target_session_id,
                    "thread_id": target_session_id,
                    "scope_source": "checkpoint_fork_user_approved",
                    "status": "active",
                }
            )
            db.upsert_session_scope_binding(copied)

    async def execute_approved(self, operation_id: str) -> dict[str, Any]:
        operation = self._operation_row(operation_id)
        approval = db.get_pending_approval(str(operation["approval_id"]))
        if not approval or str(approval.get("status") or "") != "approved":
            raise CheckpointGovernanceError("checkpoint 操作尚未获得人工批准。")
        if operation["state"] in {"completed", "waiting_input"}:
            return self.get_operation(operation_id)
        self._assert_source_idle(
            str(operation["source_session_id"]),
            exclude_run_id=str(operation["run_id"]),
        )
        if self._checkpoint_fingerprint(
            str(operation["source_thread_id"]),
            str(operation["source_checkpoint_id"]),
        ) != str(operation["source_checkpoint_fingerprint"]):
            raise CheckpointGovernanceError("源 checkpoint 在批准后发生变化，计划已失效。")

        self._update_operation(operation_id, state="running")
        try:
            engine_config = require_engine_config(
                resolve_engine_config_for_role("supervisor"),
                role="supervisor",
            )
            graph, _ = await _get_supervisor_runner().build_graph(engine_config)
            source_config = {
                "configurable": {
                    "thread_id": operation["source_thread_id"],
                    "checkpoint_ns": "",
                    "checkpoint_id": operation["source_checkpoint_id"],
                },
                "recursion_limit": 100,
            }
            snapshot = await graph.aget_state(source_config)
            source_values = dict(getattr(snapshot, "values", None) or {})
            target_session_id = str(operation["target_session_id"])
            source_session = db.get_session(str(operation["source_session_id"])) or {}
            scope_binding = db.get_session_scope_binding(str(operation["source_session_id"])) or {}
            route_context_authority = self._route_context_authority(
                source_values,
                session_id=target_session_id,
                run_id=str(operation["run_id"]),
                user_id=str(source_session.get("user_id") or scope_binding.get("user_id") or ""),
                scope_binding=scope_binding,
            )
            identity_patch = self._identity_patch(
                source_values,
                session_id=target_session_id,
                run_id=str(operation["run_id"]),
                user_id=str(source_session.get("user_id") or scope_binding.get("user_id") or ""),
                scope_binding=scope_binding,
                route_context_authority=route_context_authority,
            )
            state_patch = dict(operation["state_patch"])
            route_patch = (
                state_patch.pop("current_route_context")
                if "current_route_context" in state_patch
                else None
            )
            route_context, state_patch_diagnostics = self._merge_route_context_patch(
                dict(source_values.get("current_route_context") or {}),
                route_patch,
                route_context_authority,
            )
            identity_patch["current_route_context"] = route_context
            if operation["mode"] == "fork":
                self._create_fork_session(operation, source_session)
                next_values = {**source_values, **state_patch, **identity_patch}
                update_options = (
                    {"as_node": str(operation["as_node"])} if str(operation["as_node"]).strip() else {}
                )
                branch_config = await graph.aupdate_state(
                    {
                        "configurable": {
                            "thread_id": operation["target_thread_id"],
                            "checkpoint_ns": "",
                        },
                        "recursion_limit": 100,
                    },
                    next_values,
                    **update_options,
                )
            else:
                update_options = (
                    {"as_node": str(operation["as_node"])} if str(operation["as_node"]).strip() else {}
                )
                branch_config = await graph.aupdate_state(
                    source_config,
                    identity_patch,
                    **update_options,
                )

            invoke_config = {**branch_config, "recursion_limit": 100}
            await graph.ainvoke(None, invoke_config)
            latest = await graph.aget_state(
                {"configurable": {"thread_id": operation["target_thread_id"], "checkpoint_ns": ""}}
            )
            next_nodes = list(getattr(latest, "next", ()) or ())
            waiting = bool(next_nodes)
            state = "waiting_input" if waiting else "completed"
            result = {
                "summary": "历史恢复点已建立分支，后续执行已暂停等待人工处理。"
                if waiting
                else "历史恢复点分支已执行完成。",
                "targetSessionId": target_session_id,
                "targetCheckpointId": str(
                    ((getattr(latest, "config", None) or {}).get("configurable") or {}).get("checkpoint_id") or ""
                ),
                "nextNodes": next_nodes,
                "reexecuted": True,
                "statePatchDiagnostics": state_patch_diagnostics,
            }
            self._update_operation(operation_id, state=state, result=result)
            db.update_run_record(str(operation["run_id"]), status=state)
            return self.get_operation(operation_id)
        except Exception as exc:
            self._update_operation(
                operation_id,
                state="failed",
                error_code=type(exc).__name__,
                error_message=str(exc)[:500],
            )
            db.update_run_record(
                str(operation["run_id"]),
                status="failed",
                error_message=f"{type(exc).__name__}: {exc}"[:1000],
            )
            raise

    def schedule_approved_operation(self, operation_id: str) -> dict[str, Any]:
        normalized = str(operation_id or "").strip()
        with self._scheduled_lock:
            if normalized in self._scheduled:
                return {"scheduled": False, "reason": "already_scheduled", "operationId": normalized}
            self._scheduled.add(normalized)

        def worker() -> None:
            try:
                asyncio.run(self.execute_approved(normalized))
            finally:
                with self._scheduled_lock:
                    self._scheduled.discard(normalized)

        threading.Thread(
            target=worker,
            name=f"checkpoint-governance-{normalized[-8:]}",
            daemon=True,
        ).start()
        return {"scheduled": True, "operationId": normalized}

    def reject_operation(self, operation_id: str, *, reason: str = "user_rejected") -> dict[str, Any]:
        operation = self._operation_row(operation_id)
        self._update_operation(
            operation_id,
            state="cancelled",
            result={"summary": "用户取消了 checkpoint 分支操作。", "reason": reason},
        )
        db.update_run_record(str(operation["run_id"]), status="cancelled", error_message=reason)
        return self.get_operation(operation_id)


checkpoint_governance_service = CheckpointGovernanceService()


__all__ = [
    "CheckpointGovernanceError",
    "CheckpointGovernanceService",
    "checkpoint_governance_service",
]
