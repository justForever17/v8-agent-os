from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.database import db
from core.security.credentials import CredentialStoreError, credential_ref_store
from core.time_truth import utc_now_iso


_ACTION_KINDS = {
    "secret_input",
    "user_presence",
    "unlock_required",
    "computer_use_handoff",
    "rpa_handoff",
}
class UiActionRequestError(RuntimeError):
    def __init__(self, message: str, *, code: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value)) if value not in (None, "") else fallback
    except Exception:
        return fallback


def _parse_time(value: str) -> datetime:
    normalized = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class UiActionRequestService:
    """One-time, user-scoped Human Surface actions.

    Only public field schemas and redacted results are persisted. Secret values
    move directly from the authenticated client request into the OS credential
    store, then the registered domain handler receives opaque references.
    """

    def create(
        self,
        *,
        kind: str,
        owner_id: str,
        session_id: str,
        run_id: str,
        title: str,
        description: str,
        target_label: str,
        fields: list[dict[str, Any]],
        handler_type: str,
        handler_ref: str,
        ttl_minutes: int = 20,
    ) -> dict[str, Any]:
        normalized_kind = str(kind or "").strip().lower()
        if normalized_kind not in _ACTION_KINDS:
            raise UiActionRequestError("不支持的 UI action 类型。", code="ui_action_kind_invalid")
        normalized_owner = str(owner_id or "").strip()
        normalized_session = str(session_id or "").strip()
        normalized_run = str(run_id or "").strip()
        if not normalized_owner:
            raise UiActionRequestError("操作请求缺少用户归属。", code="ui_action_owner_required", status_code=409)
        if not normalized_session or not normalized_run:
            raise UiActionRequestError("操作请求必须绑定当前会话和运行。", code="ui_action_runtime_scope_required", status_code=409)
        public_fields: list[dict[str, Any]] = []
        for raw in fields:
            field_id = str(raw.get("id") or "").strip()
            field_kind = str(raw.get("kind") or "text").strip().lower()
            if not field_id or field_kind not in {"secret", "text", "boolean", "choice"}:
                raise UiActionRequestError("UI action 字段定义无效。", code="ui_action_field_invalid")
            public_fields.append(
                {
                    "id": field_id,
                    "kind": field_kind,
                    "label": str(raw.get("label") or field_id),
                    "help": str(raw.get("help") or "") or None,
                    "required": bool(raw.get("required", False)),
                    "options": [str(item) for item in list(raw.get("options") or [])],
                    "autocomplete": str(raw.get("autocomplete") or "off"),
                    "binding": dict(raw.get("binding") or {}),
                }
            )
        action_id = f"ui_action_{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=max(1, min(int(ttl_minutes or 20), 120)))
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO ui_action_requests
                (id,kind,state,owner_id,session_id,run_id,title,description,target_label,fields_json,
                 handler_type,handler_ref,expires_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    action_id,
                    normalized_kind,
                    "pending",
                    normalized_owner,
                    normalized_session,
                    normalized_run,
                    str(title or "需要你的操作").strip(),
                    str(description or "").strip() or None,
                    str(target_label or "").strip() or None,
                    _json(public_fields),
                    str(handler_type or "").strip(),
                    str(handler_ref or "").strip(),
                    expires.isoformat().replace("+00:00", "Z"),
                    now.isoformat().replace("+00:00", "Z"),
                    now.isoformat().replace("+00:00", "Z"),
                ),
            )
            conn.commit()
        return self.public(action_id, owner_id=normalized_owner, session_id=normalized_session)

    def _row(self, action_id: str, *, owner_id: str = "", session_id: str = "") -> dict[str, Any]:
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM ui_action_requests WHERE id=?", (str(action_id or "").strip(),)).fetchone()
        if not row:
            raise UiActionRequestError("操作请求不存在。", code="ui_action_not_found", status_code=404)
        item = dict(row)
        stored_owner = str(item.get("owner_id") or "").strip()
        stored_session = str(item.get("session_id") or "").strip()
        if not stored_owner or str(owner_id or "").strip() != stored_owner:
            raise UiActionRequestError("操作请求不属于当前用户。", code="ui_action_owner_mismatch", status_code=403)
        if not stored_session or str(session_id or "").strip() != stored_session:
            raise UiActionRequestError("操作请求不属于当前会话。", code="ui_action_session_mismatch", status_code=403)
        if item.get("state") == "pending" and _parse_time(str(item.get("expires_at") or "")) <= datetime.now(timezone.utc):
            with db.get_connection() as conn:
                conn.execute("UPDATE ui_action_requests SET state='expired',updated_at=? WHERE id=?", (utc_now_iso(), item["id"]))
                conn.commit()
            item["state"] = "expired"
        return item

    def public(self, action_id: str, *, owner_id: str = "", session_id: str = "") -> dict[str, Any]:
        item = self._row(action_id, owner_id=owner_id, session_id=session_id)
        fields = []
        for raw in _loads(item.get("fields_json"), []):
            field = dict(raw or {})
            field.pop("binding", None)
            fields.append(field)
        return {
            "actionRequestId": item["id"],
            "sessionId": str(item.get("session_id") or ""),
            "kind": item["kind"],
            "state": item["state"],
            "title": item["title"],
            "description": item.get("description") or "",
            "targetLabel": item.get("target_label") or "",
            "fields": fields,
            "submitLabel": "保存" if item["kind"] == "secret_input" else "继续",
            "expiresAt": item["expires_at"],
            "result": _loads(item.get("result_json"), {}),
            "error": (
                {"code": item.get("error_code"), "message": item.get("error_message")}
                if item.get("error_code") or item.get("error_message")
                else None
            ),
        }

    def submit(self, action_id: str, *, values: dict[str, Any], owner_id: str, session_id: str) -> dict[str, Any]:
        item = self._row(action_id, owner_id=owner_id, session_id=session_id)
        if str(item.get("state") or "") != "pending":
            raise UiActionRequestError("该操作请求已结束，不能重复提交。", code="ui_action_already_terminal", status_code=409)
        if item.get("kind") != "secret_input" or item.get("handler_type") != "config_broker_secret":
            raise UiActionRequestError("该操作类型尚未接入执行处理器。", code="ui_action_handler_unavailable", status_code=409)
        fields = [dict(raw or {}) for raw in _loads(item.get("fields_json"), [])]
        allowed_ids = {str(field.get("id") or "") for field in fields}
        unknown = sorted(str(key) for key in values if str(key) not in allowed_ids)
        if unknown:
            raise UiActionRequestError("提交包含未声明字段。", code="ui_action_unknown_field")
        normalized_values: dict[str, str] = {}
        for field in fields:
            field_id = str(field.get("id") or "")
            value = str(values.get(field_id) or "")
            if field.get("required") and not value:
                raise UiActionRequestError(f"{field.get('label') or field_id} 不能为空。", code="ui_action_required_field_missing")
            if len(value) > 16_384:
                raise UiActionRequestError("凭据长度超出限制。", code="ui_action_value_too_large")
            normalized_values[field_id] = value
        claimed_at = utc_now_iso()
        with db.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE ui_action_requests SET submitted_at=?,updated_at=? WHERE id=? AND state='pending' AND submitted_at IS NULL",
                (claimed_at, claimed_at, item["id"]),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise UiActionRequestError("该操作正在提交，请勿重复操作。", code="ui_action_submit_in_progress", status_code=409)
        created_refs: list[str] = []
        bindings: list[dict[str, Any]] = []
        try:
            for field in fields:
                field_id = str(field.get("id") or "")
                value = normalized_values[field_id]
                if not value:
                    continue
                binding = dict(field.get("binding") or {})
                namespace = str(binding.get("namespace") or "plugin")
                reference = credential_ref_store.put(value, namespace=namespace)
                created_refs.append(reference)
                bindings.append(
                    {
                        "id": field_id,
                        "secretRef": reference,
                        "target": binding.get("target"),
                        "targetName": binding.get("targetName"),
                    }
                )
            from core.config_broker_service import config_broker_service

            result = config_broker_service.attach_credentials_and_commit(
                str(item.get("handler_ref") or ""),
                bindings=bindings,
                owner_id=owner_id,
            )
            next_state = "submitted" if result.get("ok") else "failed"
            with db.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE ui_action_requests
                    SET state=?,result_json=?,error_code=?,error_message=?,submitted_at=?,updated_at=?
                    WHERE id=?
                    """,
                    (
                        next_state,
                        _json({"ok": bool(result.get("ok")), "state": result.get("state"), "summary": result.get("summary")}),
                        ((result.get("error") or {}).get("code") if not result.get("ok") else None),
                        ((result.get("error") or {}).get("message") if not result.get("ok") else None),
                        utc_now_iso(),
                        utc_now_iso(),
                        item["id"],
                    ),
                )
                conn.commit()
            return self.public(item["id"], owner_id=owner_id, session_id=session_id)
        except Exception as exc:
            for reference in created_refs:
                try:
                    credential_ref_store.delete(reference)
                except Exception:
                    pass
            code = getattr(exc, "code", "ui_action_submit_failed")
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE ui_action_requests SET submitted_at=NULL,error_code=?,error_message=?,updated_at=? WHERE id=? AND state='pending'",
                    (str(code), str(exc), utc_now_iso(), item["id"]),
                )
                conn.commit()
            if isinstance(exc, UiActionRequestError):
                raise
            if isinstance(exc, CredentialStoreError):
                raise UiActionRequestError(str(exc), code="credential_store_unavailable", status_code=503) from exc
            raise UiActionRequestError(str(exc), code=str(code), status_code=getattr(exc, "status_code", 409)) from exc


ui_action_request_service = UiActionRequestService()


__all__ = ["UiActionRequestError", "UiActionRequestService", "ui_action_request_service"]
