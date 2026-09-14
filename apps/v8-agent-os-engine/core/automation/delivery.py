"""Durable trigger delivery over the existing run/admission/side-effect owners."""
from __future__ import annotations

import asyncio
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

    @staticmethod
    def enqueue(*, kind, source_event_id, source_session_id, source_run_id, event_payload, entries):
        from runtimes.automation.runtime import automation_runtime

        # Source sessions retain their original identity. Only an unbound system
        # trigger gets a dedicated source session.
        if not db.get_session(source_session_id):
            user_id = str((entries[0]["kwargs"] if entries else {}).get("user_id") or "system")
            db.create_or_update_session(source_session_id, title=f"{kind.title()} automation", user_id=user_id)
        persisted_source_run = source_run_id if source_run_id and db.get_run_record(source_run_id) else None
        deliveries = []
        for entry in entries:
            definition = entry["definition"]
            definition_id = str(definition["id"])
            delivery_id = "delivery-" + fingerprint([source_event_id, kind, definition_id])
            kwargs = dict(entry["kwargs"])
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
    def reconcile(*, delivery_id, kind, outcome, evidence, context):
        item = db.get_automation_delivery(delivery_id)
        if not item or item["definition_kind"] != kind:
            raise ValueError("delivery not found in this automation kind")
        if item["phase"] != "unknown":
            raise ValueError(f"delivery is {item['phase']}; only unknown outcomes can be reconciled")
        for key in ("user_id", "project_id", "workspace_id"):
            owner = item["envelope"]["kwargs"].get(key)
            if owner and str(context.get(key) or "") != str(owner):
                raise PermissionError("delivery reconciliation scope mismatch")
        if outcome not in {"completed", "failed"} or not isinstance(evidence, dict) or not evidence:
            raise ValueError("reconciliation requires observed outcome evidence")
        if item["receipt_key"]:
            receipt = db.get_side_effect_receipt(item["receipt_key"])
            if receipt and receipt["state"] == "indeterminate":
                if not db.reconcile_side_effect_receipt(idempotency_key=item["receipt_key"], outcome=outcome, evidence=evidence):
                    raise RuntimeError("effect receipt reconciliation rejected")
            elif not receipt or receipt["state"] != outcome:
                raise ValueError("effect receipt is active or conflicts with this outcome")
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
                self._report_attention(db.get_automation_delivery(result["delivery_id"]))
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
