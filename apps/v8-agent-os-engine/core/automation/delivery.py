"""Durable trigger delivery over the existing run/admission/side-effect owners."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone

from core.automation.definitions import enabled, fingerprint
from core.database import db
from core.storage import storage
from erc.runtime_context import bind_runtime_context

logger = logging.getLogger(__name__)


class AutomationDeliveryService:
    def __init__(self):
        self._active = {}
        self._inline = {}
        self._loop = None
        self._terminal_cursor = ""
        self._terminal_scan_done = False
        self._unknown_run_cursor = None

    @staticmethod
    def _project_run_status(run_id, *, previous_status, reason, terminal=False):
        from erc.workflow_ledger import workflow_ledger_service
        from runtimes.automation.runtime import automation_runtime

        run = db.get_run_record(run_id) or {}
        status = run.get("status")
        if not status:
            return
        workflow_ledger_service.sync_run_status(run_id, run_status=status, reason=reason)
        # Project current persisted truth even if a cancellation or late worker
        # won after our CAS; a stale poll must not emit its old waiting state.
        latest = db.get_run_record(run_id) or {}
        if latest.get("status") != status:
            workflow_ledger_service.sync_run_status(run_id, run_status=latest.get("status"), reason="automation_concurrent_transition")
            return
        handle = automation_runtime.attach_run(run_id)
        if handle:
            handle.emit("run.state.changed", {"from_status": previous_status, "to_status": status, "reason": reason})
            if terminal and status in {"completed", "failed", "cancelled"}:
                handle.emit(f"run.{status}", {"status": status, "reason": reason})
            handle.refresh_chat_snapshot()

    @staticmethod
    def _mark_run_waiting_for_external_outcome(item, *, reason):
        """Loss of execution ownership is waiting, never proof of process exit."""
        from erc.run_service import run_service

        if not item:
            return False
        result = run_service.transition_automation_run(item, status="waiting_external_tool", reason=reason)
        if result.get("updated"):
            AutomationDeliveryService._project_run_status(
                item["execution_run_id"], previous_status=result["previousStatus"],
                reason="automation_worker_termination_unproven",
            )
        return bool(result.get("updated"))

    @staticmethod
    def settle_run(kwargs, run_handle, *, status, reason=None, error=None, receipt=None):
        """Called by the actual executor at its terminal boundary, not by Admin."""
        from erc.run_service import run_service

        delivery_id = kwargs.get("automation_delivery_id")
        if not delivery_id:
            if status in {"success", "skipped_duplicate"}:
                run_handle.complete(reason=reason or "automation_finished", node="automation_runtime")
            elif status == "cancelled":
                run_service.transition_run(run_handle.run_id, status="cancelled", error_message=error)
            else:
                run_handle.fail(error, node="automation_runtime")
            return status
        item = db.get_automation_delivery(delivery_id)
        if not item or item["execution_run_id"] != run_handle.run_id:
            return str((db.get_run_record(run_handle.run_id) or {}).get("status") or "failed")
        desired = "completed" if status in {"success", "skipped_duplicate"} else status
        result = run_service.transition_automation_run(
            item, status=desired, owner_id=kwargs.get("automation_delivery_owner"),
            receipt_owner_id=receipt.owner_id if receipt else None, error_message=error, reason=reason,
        )
        if not result.get("updated"):
            AutomationDeliveryService._mark_run_waiting_for_external_outcome(item, reason="late_worker_after_owner_loss")
            return str((db.get_run_record(run_handle.run_id) or {}).get("status") or "failed")
        AutomationDeliveryService._project_run_status(
            run_handle.run_id, previous_status=result.get("previousStatus"),
            reason=reason or error or f"automation_{result['status']}",
            terminal=result["status"] in {"completed", "failed", "cancelled"},
        )
        return status if result["status"] == desired else result["status"]

    @staticmethod
    def enqueue(*, kind, source_event_id, source_session_id, source_run_id, event_payload, entries):
        from runtimes.automation.runtime import automation_runtime

        # Source sessions retain their original identity. Only an unbound system
        # trigger gets a dedicated source session.
        if not db.get_session(source_session_id):
            user_id = str((entries[0]["kwargs"] if entries else {}).get("user_id") or "system")
            db.create_or_update_session(source_session_id, title=f"{kind.title()} automation", user_id=user_id)
        persisted_source_run = source_run_id if source_run_id and db.get_run_record(source_run_id) else None
        source_session = db.get_session(source_session_id) or {}
        source_record = db.get_run_record(persisted_source_run) if persisted_source_run else None
        if source_record and source_record.get("session_id") != source_session_id:
            raise ValueError("automation source run/session binding conflict")
        source_scope = {"user_id": (source_record or {}).get("user_id") or source_session.get("user_id")}
        for key in ("project_id", "workspace_id"):
            source_scope[key] = ((source_record or {}).get("metadata") or {}).get(key) or (source_session.get("metadata") or {}).get(key)
        deliveries = []
        for entry in entries:
            definition = entry["definition"]
            definition_id = str(definition["id"])
            delivery_id = "delivery-" + fingerprint([source_event_id, kind, definition_id])
            kwargs = dict(entry["kwargs"])
            for key, owner in source_scope.items():
                if owner and kwargs.get(key) and str(owner) != str(kwargs[key]):
                    raise ValueError("automation source ownership conflict")
                if owner and not kwargs.get(key):
                    kwargs[key] = owner
            run_id = "automation-" + fingerprint(delivery_id)[:32]
            session_id = automation_runtime.resolve_session_id(
                action_type=entry["action_type"], target=entry["target"],
                trigger_source=kwargs.get("trigger"), kwargs=kwargs,
            )
            kwargs.update(run_id=run_id, session_id=session_id)
            kwargs["automation_delivery_id"] = delivery_id
            phase, reason, receipt_key = "pending", None, None
            # A legacy dispatch marker proves neither admission nor an external
            # outcome. Existing executions are reconciled, never blindly replayed.
            if source_run_id and source_event_id.startswith("terminal-hook:"):
                legacy = db.find_automation_source_runs(source_run_id=source_run_id, target=entry["target"])
                if legacy:
                    phase, reason = "unknown", "legacy_execution_requires_reconciliation"
                    run_id = legacy[0]["id"]
                    kwargs["run_id"] = run_id
                    kwargs["session_id"] = legacy[0]["session_id"]
                    receipts = db.get_automation_execution_receipts(run_id)
                    if len(legacy) == 1 and len(receipts) == 1:
                        receipt_key = receipts[0]["idempotency_key"]
                        if receipts[0]["state"] in {"completed", "failed"}:
                            phase, reason = receipts[0]["state"], "legacy_execution_receipt"
            deliveries.append({
                "delivery_id": delivery_id, "definition_kind": kind,
                "definition_id": definition_id, "definition_revision": definition["definitionRevision"],
                "execution_run_id": run_id, "phase": phase, "last_error": reason,
                "receipt_key": receipt_key,
                "envelope": {
                    "action_type": entry["action_type"], "target": entry["target"], "payload": entry["payload"],
                    "is_async": entry["is_async"], "kwargs": kwargs,
                },
            })
        result = db.enqueue_automation_deliveries(
            source_event={
                "event_id": source_event_id, "session_id": source_session_id, "run_id": persisted_source_run,
                "topic": "automation.source.received", "ts": datetime.now(timezone.utc).isoformat(),
                "source": {"component": "automation_delivery", "runtime": "automation"},
                "payload": {**event_payload, "sourceRunId": source_run_id},
            },
            deliveries=deliveries,
        )
        for item in result:
            if item["phase"] == "unknown":
                AutomationDeliveryService._report_attention(item)
        return result

    @staticmethod
    def _report_attention(item):
        from core.action_executor import ActionExecutor
        envelope = item["envelope"]
        ActionExecutor._log_audit_event(
            item["definition_kind"], envelope["kwargs"].get("task_name") or item["definition_id"],
            envelope["target"], "review_required",
            f"Delivery {item['delivery_id']}: {item.get('last_error') or 'outcome unknown'}; reconcile before retry.",
        )

    @staticmethod
    def _owner_scope(item):
        from core.memory_maintenance_contract import SYSTEM_MEMORY_MAINTENANCE_JOB_ID, SYSTEM_MEMORY_MAINTENANCE_TARGET

        envelope = item["envelope"]
        original = envelope["kwargs"]
        candidates = {key: set() for key in ("user_id", "project_id", "workspace_id")}
        def collect(value):
            if not isinstance(value, dict):
                return
            for key in candidates:
                alias = {"user_id": "userId", "project_id": "projectId", "workspace_id": "workspaceId"}[key]
                owner = str(value.get(key) or value.get(alias) or "").strip()
                if owner:
                    candidates[key].add(owner)
        collect(original)
        session_ids = {item["source_session_id"]}
        binding_conflict = False
        for run_id in (item.get("source_run_id"), item["execution_run_id"]):
            record = db.get_run_record(run_id) if run_id else None
            if not record:
                continue
            if run_id == item.get("source_run_id") and record.get("session_id") != item["source_session_id"]:
                binding_conflict = True
            collect(record)
            metadata = record.get("metadata") or {}
            collect(metadata)
            collect(metadata.get("kwargs") if isinstance(metadata, dict) else None)
            if record.get("session_id"):
                session_ids.add(record["session_id"])
        for session_id in session_ids:
            session = db.get_session(session_id)
            if session:
                collect(session)
                collect(session.get("metadata"))
        system_definition = (
            item["definition_kind"] == "cron" and item["definition_id"] == SYSTEM_MEMORY_MAINTENANCE_JOB_ID
            and envelope["action_type"] == "python" and envelope["target"] == SYSTEM_MEMORY_MAINTENANCE_TARGET
        )
        system_owned = system_definition or "system" in candidates["user_id"]
        conflict = binding_conflict or any(len(owners) > 1 for owners in candidates.values())
        return {
            "system": system_owned, "unresolved": conflict or not candidates["user_id"],
            "scope": {key: next(iter(owners)) if len(owners) == 1 else None for key, owners in candidates.items()},
        }

    @staticmethod
    def list_for_admin(*, limit=25, after=None, ownership="system,unresolved"):
        from core.observability_db import redact_observability_text
        accepted = set(str(ownership).split(","))
        if not accepted or not accepted <= {"system", "user", "unresolved"} or not 1 <= int(limit) <= 100:
            raise ValueError("invalid delivery query")
        cursor = None
        if after:
            try:
                if len(after) > 1024:
                    raise ValueError()
                cursor = json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
                if not isinstance(cursor, list) or len(cursor) != 2 or any(not isinstance(value, str) or not value or len(value) > 160 for value in cursor):
                    raise ValueError()
            except Exception:
                raise ValueError("invalid delivery cursor") from None
        selected = []
        # Each read is an indexed keyset page. Filtering uses the same canonical
        # owner resolver as writes, before determining the visible next page.
        while len(selected) <= limit:
            rows = db.page_unknown_automation_deliveries(after=cursor, limit=128)
            if not rows:
                break
            for item in rows:
                cursor = [item["created_at"], item["delivery_id"]]
                owner = AutomationDeliveryService._owner_scope(item)
                owner_kind = "system" if owner["system"] else "unresolved" if owner["unresolved"] else "user"
                if owner_kind not in accepted:
                    continue
                receipt = db.get_side_effect_receipt(item["receipt_key"]) if item["receipt_key"] else None
                receipt_state = str((receipt or {}).get("state") or "missing")
                if receipt_state not in {"indeterminate", "claimed", "completed", "failed"}:
                    receipt_state = "missing"
                name = str(item["envelope"]["kwargs"].get("task_name") or item["definition_id"])
                selected.append({
                    "deliveryId": item["delivery_id"], "definitionId": item["definition_id"],
                    "definitionName": redact_observability_text(name).replace("\n", " ")[:160],
                    "kind": item["definition_kind"], "phase": "unknown", "ownership": owner_kind,
                    "source": {"eventId": item["source_event_id"], "runId": item.get("source_run_id"), "sessionId": item["source_session_id"]},
                    "createdAt": item["created_at"], "updatedAt": item["updated_at"], "admittedAt": item.get("admitted_at"),
                    "receiptState": receipt_state,
                    "evidenceSummary": "已有执行记录，外部结果尚未确认；请先核对目标系统。" if receipt else "旧执行记录缺少结果凭据；请核对外部结果后记录人工观察。",
                })
                if len(selected) > limit:
                    break
            if len(rows) < 128:
                break
        has_more = len(selected) > limit
        items = selected[:limit]
        next_cursor = None
        if has_more:
            next_cursor = base64.urlsafe_b64encode(json.dumps([items[-1]["createdAt"], items[-1]["deliveryId"]]).encode()).decode().rstrip("=")
        return {"items": items, "limit": limit, "hasMore": has_more, "nextCursor": next_cursor}

    @staticmethod
    def _assert_request_owner(item, context):
        ownership = AutomationDeliveryService._owner_scope(item)
        if ownership["system"] or ownership["unresolved"]:
            raise PermissionError(
                "automation_admin_reconciliation_required: 系统任务或归属待确认，请前往 Admin 的自动化页面核对结果。"
            )
        # Tool arguments cannot supply this context. Corroborate the injected
        # caller with the persisted chat/session owner; labels such as ADMIN or
        # user_id=system never grant global configuration authority.
        caller_session_id = str(context.get("session_id") or "")
        caller_run_id = str(context.get("run_id") or "")
        caller_run = db.get_run_record(caller_run_id) if caller_run_id else None
        if caller_run:
            if caller_session_id and caller_run.get("session_id") != caller_session_id:
                raise PermissionError("delivery reconciliation caller binding mismatch")
            caller_session_id = str(caller_run.get("session_id") or "")
        caller_session = db.get_session(caller_session_id) if caller_session_id else None
        caller_user = str((caller_run or {}).get("user_id") or (caller_session or {}).get("user_id") or "").strip()
        if not caller_user or caller_user == "system" or str(context.get("user_id") or "").strip() != caller_user:
            raise PermissionError("delivery reconciliation caller ownership is unverified")
        for key, owner in ownership["scope"].items():
            if owner and str(context.get(key) or "").strip() != owner:
                raise PermissionError("delivery reconciliation scope mismatch")

    @staticmethod
    def authorize_reconciliation(*, delivery_id, kind, context):
        item = db.get_automation_delivery(delivery_id)
        if not item or item["definition_kind"] != kind:
            raise ValueError("delivery not found in this automation kind")
        AutomationDeliveryService._assert_request_owner(item, context)
        return item

    @staticmethod
    def reconcile(*, delivery_id, kind, outcome, evidence, context):
        item = AutomationDeliveryService.authorize_reconciliation(delivery_id=delivery_id, kind=kind, context=context)
        return AutomationDeliveryService._reconcile_authorized(item, outcome=outcome, evidence=evidence)

    @staticmethod
    def reconcile_from_admin(*, delivery_id, outcome, evidence, authenticated_owner):
        """Control-plane caller only, after Admin login/relay authentication."""
        if not str(authenticated_owner or "").strip():
            raise PermissionError("authenticated Admin identity required")
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError("reconciliation requires observed outcome evidence")
        item = db.get_automation_delivery(delivery_id)
        if not item:
            raise ValueError("delivery not found")
        return AutomationDeliveryService._reconcile_authorized(
            item, outcome=outcome, evidence={**dict(evidence or {}), "reconciledByAdmin": authenticated_owner},
        )

    @staticmethod
    def _reconcile_authorized(item, *, outcome, evidence):
        delivery_id, kind = item["delivery_id"], item["definition_kind"]
        if item["phase"] != "unknown":
            raise ValueError(f"delivery is {item['phase']}; only unknown outcomes can be reconciled")
        if outcome not in {"completed", "failed"} or not isinstance(evidence, dict) or not evidence:
            raise ValueError("reconciliation requires observed outcome evidence")
        receipt = db.get_side_effect_receipt(item["receipt_key"]) if item["receipt_key"] else None
        if item["receipt_key"] and (not receipt or receipt["state"] not in {"indeterminate", outcome}):
            raise ValueError("effect receipt is active or conflicts with this outcome")
        # This is liveness correction for an already unknown execution, not a
        # terminal transition or an assertion that the submitted evidence is true.
        AutomationDeliveryService._mark_run_waiting_for_external_outcome(item, reason="outcome_reconciliation")
        if receipt and receipt["state"] == "indeterminate":
            if not db.reconcile_side_effect_receipt(idempotency_key=item["receipt_key"], outcome=outcome, evidence=evidence):
                raise RuntimeError("effect receipt reconciliation rejected")
        if not db.reconcile_automation_delivery(delivery_id, outcome=outcome, evidence=evidence):
            raise RuntimeError("delivery changed during reconciliation")
        from core.action_executor import ActionExecutor
        ActionExecutor._log_audit_event(kind, f"Reconciled {delivery_id}", item["envelope"]["target"], outcome)
        return f"Delivery '{delivery_id}' reconciled as {outcome}; no action was replayed."

    @staticmethod
    def _current_definition(item):
        if item["definition_kind"] == "cron":
            config = storage.get_cron_config()
            desired = next((value for value in config.get("jobs", []) if str(value.get("id")) == item["definition_id"]), None)
            if item["envelope"]["kwargs"].get("automation_manual_trigger"):
                return desired
            if not desired or not enabled(desired):
                return None
            definitions = (config.get("appliedPlan") or {}).get("jobs", [])
        else:
            definitions = storage.get_hooks_config().get("hooks", [])
        return next((value for value in definitions if str(value.get("id")) == item["definition_id"]), None)

    @classmethod
    def definition_is_current(cls, item):
        definition = cls._current_definition(item)
        allow_disabled = bool(item["envelope"]["kwargs"].get("automation_allow_disabled"))
        return bool(definition and (enabled(definition) or allow_disabled) and definition.get("definitionRevision") == item["definition_revision"])

    def verify_binding(self, kwargs, run_handle):
        delivery_id = kwargs.get("automation_delivery_id")
        if not delivery_id:
            return
        item = db.get_automation_delivery(delivery_id)
        if (not item or item["owner_id"] != kwargs.get("automation_delivery_owner")
                or item["execution_run_id"] != run_handle.run_id
                or item["envelope"]["kwargs"]["session_id"] != run_handle.session_id):
            raise ValueError("automation_delivery_binding_conflict")
        if item["phase"] not in {"claimed", "blocked"}:
            raise asyncio.CancelledError("automation delivery is no longer admitted for this attempt")
        if not AutomationDeliveryService.definition_is_current(item):
            db.cancel_automation_delivery(delivery_id, reason="definition_changed_before_admission")
            raise asyncio.CancelledError("automation definition changed before admission")
        self._inline[delivery_id] = kwargs["automation_delivery_owner"]

    @staticmethod
    def admitted(kwargs):
        delivery_id = kwargs.get("automation_delivery_id")
        if delivery_id and not db.transition_automation_delivery(
            delivery_id, owner_id=kwargs["automation_delivery_owner"],
            expected_phases=("claimed", "blocked"), phase="admitted",
        ):
            raise asyncio.CancelledError("automation delivery admission lost ownership")

    @staticmethod
    def start_running(kwargs, run_handle, trigger_source):
        if not kwargs.get("automation_delivery_id"):
            run_handle.transition("running", reason=trigger_source, node="automation_runtime")
            return
        from erc.run_service import run_service
        from erc.workflow_ledger import workflow_ledger_service
        changed = run_service.transition_run_if_status(
            run_handle.run_id, expected_statuses={"queued", "running"}, status="running",
        )
        if not changed.get("updated"):
            raise asyncio.CancelledError("automation run was cancelled before execution")
        workflow_ledger_service.sync_run_status(run_handle.run_id, run_status="running", reason=trigger_source)
        run_handle.descriptor.status = "running"
        run_handle.emit("run.state.changed", {"from_status": changed.get("previousStatus"), "to_status": "running", "reason": trigger_source})

    @staticmethod
    def executing(kwargs, receipt):
        delivery_id = kwargs.get("automation_delivery_id")
        if not delivery_id:
            return
        item = db.get_automation_delivery(delivery_id)
        if not AutomationDeliveryService.definition_is_current(item):
            db.transition_automation_delivery(delivery_id, owner_id=kwargs["automation_delivery_owner"],
                expected_phases=("admitted",), phase="cancelled", error="definition_changed_before_execution")
            raise asyncio.CancelledError("automation definition changed before execution")
        if not db.transition_automation_delivery(
            delivery_id, owner_id=kwargs["automation_delivery_owner"],
            expected_phases=("admitted",), phase="executing", receipt_key=receipt.idempotency_key,
        ):
            raise asyncio.CancelledError("automation delivery execution lost ownership")

    @staticmethod
    def link_receipt(kwargs, receipt):
        if kwargs.get("automation_delivery_id"):
            db.transition_automation_delivery(kwargs["automation_delivery_id"], owner_id=kwargs["automation_delivery_owner"],
                expected_phases=("admitted",), phase="admitted", receipt_key=receipt.idempotency_key)

    def finished(self, kwargs, *, status, error):
        delivery_id = kwargs.get("automation_delivery_id")
        if not delivery_id:
            return
        if self._inline.get(delivery_id) == kwargs.get("automation_delivery_owner"):
            self._inline.pop(delivery_id, None)
        item = db.get_automation_delivery(delivery_id)
        if not item or item["owner_id"] != kwargs.get("automation_delivery_owner"):
            return
        phase = item["phase"]
        if phase not in {"claimed", "admitted", "executing", "blocked"}:
            return
        run = db.get_run_record(item["execution_run_id"]) or {}
        receipt = db.get_side_effect_receipt(item["receipt_key"]) if item["receipt_key"] else None
        if receipt and receipt["state"] == "completed":
            target = "completed"
        elif status == "cancelled" or run.get("status") == "cancelled":
            target = "cancelled"
        elif status in {"success", "skipped_duplicate"}:
            target = "unknown"
        elif status in {"waiting_approval", "review_required", "pending_approval", "paused", "waiting_input"}:
            target = "unknown" if receipt and receipt["state"] == "indeterminate" else "blocked"
        elif phase in {"claimed", "admitted"} and (status == "rejected" or (status == "failed" and kwargs.get("automation_retryable_failure", True))):
            target = "pending"
        else:
            target = "failed"
        db.transition_automation_delivery(
            delivery_id, owner_id=kwargs["automation_delivery_owner"], expected_phases=(phase,),
            phase=target, error=error,
        )

    def execute_inline(self, item):
        from core.action_executor import ActionExecutor
        if not self.definition_is_current(item):
            db.cancel_automation_delivery(item["delivery_id"], reason="definition_changed_or_disabled")
            return
        owner = uuid.uuid4().hex
        claimed = db.claim_automation_delivery(item["delivery_id"], owner_id=owner)
        if not claimed:
            return
        envelope = claimed["envelope"]
        kwargs = dict(envelope["kwargs"], automation_delivery_owner=owner)
        self._inline[item["delivery_id"]] = owner
        try:
            with bind_runtime_context(hook_chain=kwargs.pop("hook_chain", [])):
                ActionExecutor._execute_sync(envelope["action_type"], envelope["target"], envelope["payload"], kwargs)
        finally:
            self._inline.pop(item["delivery_id"], None)

    async def _execute(self, item, owner):
        from core.action_executor import ActionExecutor
        from erc.run_service import run_service

        envelope = item["envelope"]
        kwargs = dict(envelope["kwargs"], automation_delivery_owner=owner)
        try:
            prior = db.get_run_record(item["execution_run_id"])
            if prior and prior["status"] in {"failed", "interrupted"}:
                run_service.transition_run_if_status(
                    item["execution_run_id"], expected_statuses={prior["status"]}, status="queued",
                    metadata={"automationRetry": "pre_execution_owner_lost"},
                )
            with bind_runtime_context(hook_chain=kwargs.pop("hook_chain", [])):
                if envelope["action_type"] == "agent":
                    await ActionExecutor._execute_agent_async(envelope["target"], envelope["payload"], **kwargs)
                else:
                    await asyncio.to_thread(
                        ActionExecutor._execute_sync, envelope["action_type"], envelope["target"], envelope["payload"], kwargs,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Automation delivery %s failed", item["delivery_id"])
        finally:
            self._active.pop(item["delivery_id"], None)

    def kick(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self.kick)
            return
        self._loop = loop
        # Bounded pending work only. The application scheduler owns repeated wakes.
        for item in db.list_automation_deliveries(phases=("pending",), limit=32):
            if not self.definition_is_current(item):
                db.cancel_automation_delivery(item["delivery_id"], reason="definition_changed_or_disabled")
                continue
            owner = uuid.uuid4().hex
            claimed = db.claim_automation_delivery(item["delivery_id"], owner_id=owner)
            if not claimed:
                continue
            task = loop.create_task(self._execute(claimed, owner))
            self._active[item["delivery_id"]] = (task, owner)

    async def tick(self):
        for delivery_id, (task, owner) in list(self._active.items()):
            if not task.done():
                db.renew_automation_delivery(delivery_id, owner_id=owner)
        for delivery_id, owner in list(self._inline.items()):
            db.renew_automation_delivery(delivery_id, owner_id=owner)
        for result in db.reconcile_automation_deliveries(limit=64):
            if result["phase"] == "unknown":
                item = db.get_automation_delivery(result["delivery_id"])
                self._mark_run_waiting_for_external_outcome(item, reason="outcome_unknown")
                self._report_attention(item)
            elif result["phase"] in {"completed", "failed"}:
                self._mark_run_waiting_for_external_outcome(
                    db.get_automation_delivery(result["delivery_id"]), reason=f"receipt_{result['phase']}",
                )
        # Finish a bounded cursor pass even when earlier unknown rows remain
        # unresolved; taking the same first 64 rows would starve later runs.
        unknown = db.page_unknown_automation_deliveries(after=self._unknown_run_cursor, limit=64)
        for item in unknown:
            self._mark_run_waiting_for_external_outcome(item, reason="outcome_unknown")
        self._unknown_run_cursor = ([unknown[-1]["created_at"], unknown[-1]["delivery_id"]]
                                    if len(unknown) == 64 else None)
        if not self._terminal_scan_done:
            rows = db.list_automation_terminal_sources(after_run_id=self._terminal_cursor, limit=64)
            from core.terminal_post_run import terminal_post_run_service
            all_received = True
            for row in rows:
                if not terminal_post_run_service._run_non_memory_hooks(session_id=row["session_id"], run_id=row["id"]):
                    all_received = False
                    break
                self._terminal_cursor = row["id"]
            self._terminal_scan_done = all_received and len(rows) < 64
        self.kick()

    def start(self, scheduler):
        self._loop = asyncio.get_running_loop()
        self._terminal_cursor = ""
        self._terminal_scan_done = False
        scheduler.add_job(self.tick, "interval", seconds=5, id="_automation_delivery_recovery", replace_existing=True,
                          max_instances=1, coalesce=True, next_run_time=datetime.now(timezone.utc))
        self.kick()


automation_delivery_service = AutomationDeliveryService()
