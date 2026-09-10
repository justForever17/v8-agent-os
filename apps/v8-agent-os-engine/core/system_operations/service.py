from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from typing import Any, Callable

from core.security.credentials import credential_ref_store

ACTIONS = frozenset({"unlock", "run_privileged"})


class SystemOperationError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _redact_result(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]") if secret else value
    if isinstance(value, dict):
        return {key: _redact_result(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_result(item, secret) for item in value]
    return value


class SystemOperationService:
    """Credential refs, immutable approval targets and one-shot execution ledger.

    Approval itself belongs to the existing run approval owner. This service
    binds it to one operation and claims execution atomically, never replays a
    side effect merely because a graph/tool delivery was repeated.
    """

    def __init__(self, database=None, credentials=None, executor=None):
        self._database = database
        self.credentials = credentials or credential_ref_store
        self.executor = executor
        self._lock = threading.RLock()

    @property
    def database(self):
        if self._database is None:
            from core.database import db
            return db
        return self._database

    @staticmethod
    def _action(action: str) -> str:
        if action not in ACTIONS:
            raise SystemOperationError("system_operation_invalid", "不支持的系统操作。", 422)
        return action

    def credential(self, owner: str, action: str) -> dict:
        self._action(action)
        with self.database.get_connection() as conn:
            row = conn.execute("SELECT * FROM system_operation_credentials WHERE owner_id=? AND action=?", (owner, action)).fetchone()
        return dict(row) if row else {}

    def settings(self, owner: str) -> dict:
        from .platform import platform_status
        from .accounts import current_account
        profiles = {}
        for action in sorted(ACTIONS):
            item = self.credential(owner, action)
            profiles[action] = {
                "configured": bool(item and self.credentials.status(item["secret_ref"]).configured),
                "username": item.get("username", ""), "domain": item.get("domain", ""),
                "version": item.get("version", ""),
            }
        return {"profiles": profiles, "platform": platform_status(), "currentAccount": current_account()}

    def configure(self, *, owner: str, action: str, username: str, domain: str, password: str) -> dict:
        self._action(action)
        password_limit = 1024 if sys.platform == "win32" else 4096
        units = (lambda text: len(text.encode("utf-16-le")) // 2) if sys.platform == "win32" else len
        if not owner or not username.strip() or units(username) > 256 or units(domain) > 256 or not password or units(password) > password_limit or any(c in username + domain + password for c in ("\x00", "\r", "\n")):
            raise SystemOperationError("system_credential_invalid", "账户或密码格式无效。", 422)
        from .accounts import validate_account
        username, domain = validate_account(action, username, domain)
        with self._lock:
            old = self.credential(owner, action)
            reference = self.credentials.put(password, namespace="system")
            try:
                if self.credentials.resolve(reference) != password:
                    raise SystemOperationError("system_credential_roundtrip_failed", "系统凭据保存后的内容不一致，原配置未修改。", 503)
                with self.database.get_connection() as conn:
                    conn.execute("""INSERT INTO system_operation_credentials
                        (owner_id,action,username,domain,secret_ref,version,updated_at) VALUES (?,?,?,?,?,?,?)
                        ON CONFLICT(owner_id,action) DO UPDATE SET username=excluded.username,
                        domain=excluded.domain,secret_ref=excluded.secret_ref,version=excluded.version,updated_at=excluded.updated_at""",
                        (owner, action, username.strip(), domain.strip(), reference, str(uuid.uuid4()), time.time()))
                    conn.commit()
            except Exception:
                self.credentials.delete(reference)
                raise
            if old:
                self.credentials.delete(old["secret_ref"])
        return {"ok": True, "summary": "凭据已保存至系统凭据库。"}

    def remove_credential(self, owner: str, action: str) -> dict:
        with self._lock:
            old = self.credential(owner, action)
            if old:
                self.credentials.delete(old["secret_ref"])
                with self.database.get_connection() as conn:
                    conn.execute("DELETE FROM system_operation_credentials WHERE owner_id=? AND action=?", (owner, action))
                    conn.commit()
        return {"ok": True}

    def _scope(self, context: dict, tool_call_id: str) -> tuple[str, str, str]:
        from core.actor_identity import resolve_collaboration_actor
        if not resolve_collaboration_actor(runtime_context=context).is_supervisor:
            raise SystemOperationError("system_operation_supervisor_only", "受控系统操作只由主理人发起。", 403)
        owner, session, run = (str(context.get(a) or context.get(b) or "") for a, b in (("user_id", "userId"), ("session_id", "sessionId"), ("run_id", "runId")))
        stored_session = self.database.get_session(session) if session else None
        stored_run = self.database.get_run_record(run) if run else None
        if not owner or not tool_call_id or not stored_session or str(stored_session.get("user_id") or stored_session.get("userId")) != owner or not stored_run or stored_run.get("session_id") != session:
            raise SystemOperationError("system_operation_scope_mismatch", "系统操作缺少有效的用户、会话或运行归属。", 403)
        if stored_run.get("status") in {"completed", "failed", "cancelled", "canceled", "interrupted"}:
            raise SystemOperationError("system_operation_run_terminal", "该运行已结束，不能执行系统操作。")
        return owner, session, run

    def execute(self, *, payload: dict, context: dict, tool_call_id: str, authorize: Callable[[dict], None]) -> dict:
        owner, session, run = self._scope(context, tool_call_id)
        action = self._action(str(payload.get("action") or ""))
        config = self.credential(owner, action)
        if not config:
            raise SystemOperationError("system_credential_required", "请先在控制台的受控系统操作中配置对应凭据。")
        encoded = canonical_json(payload)
        identity = canonical_json([owner, session, run, tool_call_id, config["version"], encoded])
        operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        now = time.time()
        with self.database.get_connection() as conn:
            conn.execute("""INSERT OR IGNORE INTO system_operation_requests
                (id,owner_id,session_id,run_id,tool_call_id,payload_json,credential_version,state,expires_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,'prepared',?,?,?)""",
                (operation_id, owner, session, run, tool_call_id, encoded, config["version"], now + 1200, now, now))
            conn.commit()
            row = dict(conn.execute("SELECT * FROM system_operation_requests WHERE id=?", (operation_id,)).fetchone())
        if row["result_json"]:
            return json.loads(row["result_json"])
        if row["state"] != "prepared":
            raise SystemOperationError("system_operation_indeterminate", "该操作已经开始，不会重复执行；请先核对实际状态。")
        if row["expires_at"] <= now:
            raise SystemOperationError("system_operation_expired", "该操作请求已过期，请重新发起。")
        authorize({"operationId": operation_id, "payload": payload, "expiresAt": row["expires_at"]})
        # Resumption must recheck owner/run, account revision and immutable target.
        self._scope(context, tool_call_id)
        with self._lock:
            current = self.credential(owner, action)
            if current.get("version") != config["version"]:
                raise SystemOperationError("system_credential_changed", "账户配置已变化，请重新确认操作。")
            secret = self.credentials.resolve(config["secret_ref"])
            with self.database.get_connection() as conn:
                claimed = conn.execute("UPDATE system_operation_requests SET state='running',updated_at=? WHERE id=? AND state='prepared' AND expires_at>?", (time.time(), operation_id, time.time()))
                conn.commit()
            if claimed.rowcount != 1:
                raise SystemOperationError("system_operation_already_claimed", "操作已执行或过期，不会重复执行。")
        started = time.monotonic()
        try:
            if self.executor is None:
                from .platform import execute_platform_operation
                executor = execute_platform_operation
            else:
                executor = self.executor
            result = executor(payload, username=config["username"], domain=config["domain"], password=secret, operation_id=operation_id, context=context)
            # Also redact a misbehaving platform helper's output. No exception
            # strings or raw helper stderr may carry authentication material.
            result = _redact_result(result, secret)
            valid_success = isinstance(result, dict) and result.get("verified") is True
            if action == "unlock":
                valid_success = valid_success and result.get("status") in {"unlocked", "already_unlocked"} and result.get("locked") is False
            else:
                valid_success = valid_success and result.get("elevated") is True and type(result.get("exitCode")) is int and result["exitCode"] == 0
                if sys.platform == "win32":
                    valid_success = valid_success and result.get("executed") is True and result.get("processTreeStopped") is True
            if not isinstance(result, dict) or (result.get("ok") and not valid_success):
                result = {"ok": False, "code": "system_operation_unverified", "summary": "操作未取得可验证的系统结果，不能确认成功。"}
        except Exception:
            result = {"ok": False, "code": "system_operation_failed", "summary": "系统操作执行失败，请检查平台状态；不会自动重试。"}
        finally:
            secret = ""
        result.update({"operationId": operation_id, "elapsedMs": round((time.monotonic() - started) * 1000), "action": action})
        with self.database.get_connection() as conn:
            conn.execute("UPDATE system_operation_requests SET state=?,result_json=?,updated_at=? WHERE id=?", ("completed" if result.get("ok") else "failed", canonical_json(result), time.time(), operation_id))
            conn.commit()
        return result


system_operation_service = SystemOperationService()
