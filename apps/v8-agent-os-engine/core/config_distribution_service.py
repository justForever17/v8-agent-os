"""Durable fan-out of explicit portable templates over the trusted Network peer transport.

This journal owns distribution intent; Config Broker owns each target transaction.
No credentials, config snapshots, peer grants or source paths are transported.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from copy import deepcopy

from fastapi import HTTPException

from core.config_broker_service import ConfigBrokerError, config_broker_service
from core.config_distribution_store import DistributionStore
from core import config_distribution_templates as portable
from core.time_truth import utc_now_iso


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise HTTPException(422, "distribution_identifier_invalid")
    return value


def error_code(exc):
    if isinstance(exc, ConfigBrokerError):
        return exc.code
    detail = getattr(exc, "detail", "")
    if isinstance(detail, dict):
        return str(detail.get("failureClass") or "peer_error")
    # Never persist/forward transport exceptions which may contain endpoint data.
    return detail if isinstance(detail, str) and re.fullmatch(r"[a-z][a-z0-9_]{1,100}", detail) else "distribution_operation_failed"


class ConfigDistributionService:
    def __init__(self, *, store=None, network=None, neighbors=None, authorize=None):
        self.store = store or DistributionStore()
        self._network = network
        self._neighbors = neighbors
        self.authorize = authorize or self._authorize
        self._worker = None
        self._wake = asyncio.Event()
        self._processing = asyncio.Lock()

    @property
    def network(self):
        if self._network is None:
            from runtimes.network_supervisor.service import network_supervisor_service
            self._network = network_supervisor_service
        return self._network

    @property
    def neighbors(self):
        if self._neighbors is None:
            from runtimes.network_supervisor.neighbor import network_neighbor_service
            self._neighbors = network_neighbor_service
        return self._neighbors

    @staticmethod
    def _authorize(authority):
        from core.client_identity import get_identity_service
        service = get_identity_service()
        if service.instance()["instanceId"] != authority["issuer"] or service.owners.owner()["id"] != authority["subject"]:
            raise HTTPException(403, "distribution_owner_changed")
        if authority.get("deviceId"):
            with service.database() as conn:
                device = conn.execute("SELECT * FROM client_devices WHERE id=?", (authority["deviceId"],)).fetchone()
            if not device or device["user_id"] != authority["subject"] or device["revoked_at"] is not None or device["expires_at"] <= service.clock():
                raise HTTPException(403, "distribution_device_revoked")

    def _link(self, *, link_id=None, peer_id=None, inbound=False):
        if not self.network.get_config_model().enabled:
            raise HTTPException(403, "distribution_network_disabled")
        link = self.neighbors._link_for_peer_or_404(peer_id) if peer_id else self.neighbors._link_or_404(link_id)
        trusted = self.network._trusted_peer_map().get(link["peerId"])
        local, remote = ("companion", "primary") if inbound else ("primary", "companion")
        if not trusted or link.get("trustStatus") != "trusted" or link.get("localRole") != local or link.get("remoteRole") != remote:
            raise HTTPException(403, "distribution_primary_authority_required")
        try:
            self.network._assert_peer_scope_access(trusted, scope_hint="config_distribution", workspace_id=None, workspace_path=None)
        except HTTPException:
            raise HTTPException(403, "distribution_target_authority_changed") from None
        binding = digest({"linkId": link["linkId"], "peerId": link["peerId"], "createdAt": link.get("createdAt"),
                          "authorityVersion": (link.get("metadata") or {}).get("configAuthorityVersion", ""),
                          "scopes": trusted.allowed_scopes, "workspaces": trusted.allowed_workspaces,
                          "defaultScopes": self.network.get_config_model().trust.allowed_scopes,
                          "publicKey": trusted.public_key, "localRole": local, "remoteRole": remote})
        return link, binding

    @staticmethod
    def public(job):
        result = {key: deepcopy(job[key]) for key in ("jobId", "revision", "planDigest", "intent", "state", "templateId", "createdAt", "updatedAt")}
        result["targets"] = [{key: deepcopy(target[key]) for key in ("linkId", "peerId", "displayName", "state", "approved", "mapping", "diff", "missingRequirements", "errorCode", "receipt") if key in target} for target in job["targets"]]
        result["allowedActions"] = []
        if job["state"] not in {"completed", "cancelled", "withdrawn", "withdrawal_conflict"}:
            result["allowedActions"].append("retry")
            if job["intent"] not in {"cancel", "withdraw"} and job["state"] not in {"preparing", "applying"}:
                result["allowedActions"].append("prepare")
        if job["intent"] not in {"cancel", "withdraw"}:
            if any(row["state"] == "prepared" and not row["approved"] for row in job["targets"]):
                result["allowedActions"].append("confirm")
            if job["state"] != "completed":
                result["allowedActions"].append("cancel")
        if job["state"] not in {"withdrawn", "withdrawal_conflict"} and any(row["state"] in {"committed", "recovery_required"} or row["approved"] and row["state"] == "offline" for row in job["targets"]):
            result["allowedActions"].append("withdraw")
        return result

    def inventory(self, owner, issuer):
        peers = [{key: row.get(key) for key in ("linkId", "peerId", "displayName", "online", "localRole", "remoteRole")}
                 for row in self.neighbors.list_links()["items"] if row.get("trustStatus") == "trusted"]
        page = self.store.page(owner)
        return {"servingInstanceId": issuer, "templates": portable.templates(), "peers": peers,
                "maxTargets": 100, "pendingCount": page["pendingCount"], "jobsNextCursor": page["nextCursor"],
                "jobs": page["items"]}

    async def target_capabilities(self, link_id):
        link, _ = self._link(link_id=link_id)
        return await self._exchange(link["peerId"], "capabilities", {})

    def create(self, owner, authority, body):
        if set(body) - {"commandId", "templateId", "targets"}:
            raise HTTPException(422, "distribution_fields_forbidden")
        command = identifier(body.get("commandId"))
        template = next((row for row in portable.templates() if row["id"] == body.get("templateId")), None)
        targets = body.get("targets")
        if not template or not isinstance(targets, list) or not 1 <= len(targets) <= 100:
            raise HTTPException(422, "distribution_targets_invalid")
        self.authorize(authority)
        rows = []
        for item in targets:
            if not isinstance(item, dict) or set(item) - {"linkId", "mapping"}:
                raise HTTPException(422, "distribution_target_invalid")
            link, binding = self._link(link_id=identifier(item.get("linkId")))
            mapping = item.get("mapping", {})
            portable.validate(template["id"], template["values"], mapping, allow_unmapped=True)
            rows.append({"linkId": link["linkId"], "peerId": link["peerId"], "displayName": link.get("remoteNickname") or link["peerId"],
                         "binding": binding, "mapping": mapping, "generation": 1, "approved": False,
                         "state": "queued", "diff": [], "missingRequirements": [], "errorCode": "", "receipt": {}})
        if len({row["peerId"] for row in rows}) != len(rows):
            raise HTTPException(422, "distribution_target_duplicate")
        job_id = "distribution_" + uuid.uuid4().hex
        now = utc_now_iso()
        job = {"jobId": job_id, "owner": owner, "authority": authority, "templateId": template["id"], "values": template["values"],
               "targets": rows, "revision": 1, "planDigest": "", "state": "preparing", "intent": "prepare", "createdAt": now, "updatedAt": now}
        result = self.store.command(owner, command, digest(body), job_id, create=job)
        self._wake.set()
        return self.public(result)

    def action(self, job_id, owner, action, body, *, authority=None):
        if set(body) - {"commandId", "revision", "planDigest", "targets"} or "targets" in body and action != "prepare":
            raise HTTPException(422, "distribution_fields_forbidden")
        command = identifier(body.get("commandId"))
        def change(job):
            cleanup = action in {"cancel", "withdraw"} or action == "retry" and job["intent"] in {"cancel", "withdraw"}
            self.authorize((authority or job.get("cleanupAuthority", job["authority"])) if cleanup else job["authority"])
            if cleanup and authority:
                job["cleanupAuthority"] = authority
            if body.get("revision") != job["revision"]:
                raise HTTPException(409, "distribution_revision_stale")
            if action == "confirm":
                if job["intent"] in {"cancel", "withdraw"} or job["state"] not in {"awaiting_confirmation", "partial"} or body.get("planDigest") != job["planDigest"] or not job["planDigest"]:
                    raise HTTPException(409, "distribution_plan_stale")
                prepared = [row for row in job["targets"] if row["state"] == "prepared" and not row["approved"]]
                if not prepared:
                    raise HTTPException(409, "distribution_nothing_to_confirm")
                for row in prepared:
                    self._assert_source_target(row)
                    if row["receipt"]["expiresAt"] <= time.time():
                        raise HTTPException(409, "distribution_plan_expired")
                    row["approved"] = True
                job.update(intent="apply", state="applying")
            elif action == "prepare":
                if job["intent"] in {"cancel", "withdraw"} or job["state"] in {"preparing", "applying"}:
                    raise HTTPException(409, "distribution_job_not_preparable")
                patches = body.get("targets", [])
                if not isinstance(patches, list) or any(not isinstance(item, dict) or set(item) != {"linkId", "mapping"} for item in patches):
                    raise HTTPException(422, "distribution_mapping_invalid")
                mapped = {item["linkId"]: item["mapping"] for item in patches}
                if len(mapped) != len(patches) or set(mapped) - {row["linkId"] for row in job["targets"]}:
                    raise HTTPException(422, "distribution_mapping_invalid")
                for row in job["targets"]:
                    if row["linkId"] in mapped:
                        if row["approved"] or row["state"] in {"committed", "rolled_back", "cancelled"}:
                            raise HTTPException(409, "distribution_approved_mapping_immutable")
                        portable.validate(job["templateId"], job["values"], mapped[row["linkId"]], allow_unmapped=True)
                        row["mapping"] = mapped[row["linkId"]]
                    if row["state"] in {"committed", "rolled_back", "cancelled"}:
                        continue
                    self._assert_source_target(row)
                    # Keep the approved transaction and generation until the
                    # target proves whether it wrote. Reprepare is not amnesia.
                    if row["approved"] and row["receipt"].get("transactionId"):
                        row.update(reprepareRequested=True, state="reconciling", attempts=0, nextAttemptAt=0)
                    else:
                        row.update(generation=row["generation"] + 1, state="queued", approved=False, diff=[], receipt={}, errorCode="", missingRequirements=[], attempts=0, nextAttemptAt=0)
                job.update(intent="prepare", state="preparing", planDigest="")
            elif action == "retry":
                if job["state"] in {"completed", "withdrawn", "cancelled", "withdrawal_conflict"}:
                    raise HTTPException(409, "distribution_job_terminal")
                job["state"] = {"prepare": "preparing", "apply": "applying", "cancel": "cancelling", "withdraw": "withdrawing"}[job["intent"]]
            elif action in {"cancel", "withdraw"}:
                if job["intent"] == "withdraw" and action == "cancel":
                    raise HTTPException(409, "distribution_withdraw_in_progress")
                job.update(intent=action, state="cancelling" if action == "cancel" else "withdrawing", planDigest="")
                job["cleanupAuthority"] = authority or job["authority"]
            else:
                raise HTTPException(404, "distribution_action_unknown")
            job["revision"] += 1
        result = self.store.command(owner, command, digest({"jobId": job_id, "action": action, **body}), job_id, change=change)
        self._wake.set()
        return self.public(result)

    def _assert_source_target(self, target):
        link, binding = self._link(link_id=target["linkId"])
        if binding != target["binding"] or link["peerId"] != target["peerId"]:
            raise HTTPException(403, "distribution_target_authority_changed")

    async def _exchange(self, peer_id, action, body):
        envelope = self.network.build_envelope(message_type="config.distribution." + action, to_peer_id=peer_id,
            payload={"protocolVersion": 1, **body}, expires_in_seconds=60)
        response = await self.network._post_peer(peer_id, "peer/neighbors/messages", envelope)
        result = response["payload"]
        if result.get("protocolVersion") != 1 or result.get("peerId") != peer_id:
            raise HTTPException(409, "distribution_protocol_unsupported")
        if body and (result.get("jobId") != body.get("jobId") or result.get("generation") != body.get("generation")):
            raise HTTPException(409, "distribution_receipt_identity_mismatch")
        return result

    async def handle_envelope(self, envelope):
        self.network.verify_envelope(envelope, mark_nonce_seen=False)
        link, binding = self._link(peer_id=envelope.from_peer_id, inbound=True)
        payload = envelope.payload
        action = envelope.message_type.removeprefix("config.distribution.")
        if payload.get("protocolVersion") != 1:
            raise HTTPException(409, "distribution_protocol_unsupported")
        if action == "capabilities":
            if set(payload) != {"protocolVersion"}:
                raise HTTPException(422, "distribution_fields_forbidden")
            result = portable.capabilities()
        else:
            result = self._receive(envelope.from_peer_id, binding, action, payload)
        return self.network.build_envelope(message_type=envelope.message_type + ".ack", to_peer_id=envelope.from_peer_id,
            payload={**result, "protocolVersion": 1, "peerId": self.network.get_config_model().node.peer_id,
                     "requestMessageId": envelope.message_id}, trace=envelope.trace, expires_in_seconds=60)

    def _receive(self, peer_id, binding, action, payload):
        job_id = identifier(payload.get("jobId"))
        generation = payload.get("generation")
        if type(generation) is not int or not 1 <= generation <= 10000:
            raise HTTPException(422, "distribution_generation_invalid")
        allowed = {"protocolVersion", "jobId", "generation", "templateId", "values", "mapping"} if action == "prepare" else {"protocolVersion", "jobId", "generation", "planDigest", "targetBinding"}
        if set(payload) - allowed or action not in {"prepare", "apply", "status", "cancel", "withdraw"}:
            raise HTTPException(422, "distribution_fields_forbidden")
        key = digest([peer_id, job_id])
        record = self.store.receipt(key)
        if record and record["binding"] != binding:
            raise HTTPException(403, "distribution_target_authority_changed")
        if record and generation < record["generation"]:
            raise HTTPException(409, "distribution_generation_stale")
        if record and generation > record["generation"]:
            if action in {"cancel", "withdraw"}:
                # The sender may cancel its new generation before prepare was
                # delivered. Fence all older callbacks while preserving any
                # actual Broker write for reconciliation/rollback below.
                record["generation"] = generation
            elif action != "prepare" or record.get("everCommitted") or record["state"] not in {"prepared", "blocked", "conflict"}:
                raise HTTPException(409, "distribution_previous_plan_unresolved")
        if action == "prepare":
            template_id, values, mapping = payload.get("templateId"), payload.get("values"), payload.get("mapping", {})
            portable.validate(template_id, values, mapping, allow_unmapped=True)
            plan_input = digest([template_id, values, mapping])
            if record and generation == record["generation"]:
                if record.get("inputDigest") != plan_input:
                    raise HTTPException(409, "distribution_command_reused")
            else:
                record = {"jobId": job_id, "generation": generation, "binding": binding, "inputDigest": plan_input,
                          "templateId": template_id, "values": values, "mapping": mapping, "owner": "peer-config:" + key,
                          "state": "preparing", "receipt": {}, "diff": [], "missingRequirements": [], "errorCode": ""}
                self.store.save_receipt(key, record)
            if record["state"] == "preparing":
                self._prepare_received(key, record)
        elif record is None:
            if action not in {"cancel", "withdraw"}:
                raise HTTPException(404, "distribution_plan_not_found")
            record = {"jobId": job_id, "generation": generation, "binding": binding, "state": "cancelled", "receipt": {}, "diff": [], "errorCode": "", "missingRequirements": []}
            self.store.save_receipt(key, record)
        else:
            if action in {"apply", "status"} and (payload.get("targetBinding") != binding or payload.get("planDigest") != record["receipt"].get("planDigest")):
                raise HTTPException(409, "distribution_plan_stale")
            if action == "apply" and record["state"] in {"prepared", "applying"}:
                if record["receipt"]["expiresAt"] <= time.time() and record["state"] == "prepared":
                    record.update(state="conflict", errorCode="distribution_plan_expired")
                else:
                    record["state"] = "applying"
                    self.store.save_receipt(key, record)
                    self._broker_action(record, "commit")
                self.store.save_receipt(key, record)
            elif action in {"cancel", "withdraw"}:
                if record["state"] in {"applying", "recovery_required"}:
                    self._reconcile_received(record)
                if (record["state"] == "committed" or record.get("everCommitted") and record["state"] == "recovery_required") and action == "withdraw":
                    self._broker_action(record, "rollback")
                elif not record.get("everCommitted") and record["state"] not in {"committed", "rolled_back", "recovery_required"}:
                    record.update(state="cancelled", errorCode="")
                self.store.save_receipt(key, record)
            elif action == "status" and record["receipt"].get("transactionId") and record["state"] != "cancelled":
                self._reconcile_received(record)
                self.store.save_receipt(key, record)
        if record.get("everCommitted"):
            record["receipt"]["everCommitted"] = True
        return {key: deepcopy(record[key]) for key in ("jobId", "generation", "state", "receipt", "diff", "missingRequirements", "errorCode")}

    def _prepare_received(self, key, record):
        run_id = "distribution:" + key + ":" + str(record["generation"])
        try:
            # Recover the prepare->journal crash window via the broker's durable run identity.
            with self.store.db.get_connection() as conn:
                row = conn.execute("SELECT id FROM config_broker_transactions WHERE run_id=? AND owner_id=? ORDER BY created_at DESC LIMIT 1", (run_id, record["owner"])).fetchone()
            prepared = {"transactionId": row[0]} if row else portable.prepare(record["templateId"], record["values"], record["mapping"], owner_id=record["owner"], run_id=run_id)
            transaction = config_broker_service.get_transaction(prepared["transactionId"], owner_id=record["owner"], include_private=True)
            record.update(state="prepared", diff=portable.diff_from_transaction(record["templateId"], record["values"], record["mapping"], transaction),
                          receipt={"transactionId": transaction["transactionId"], "planDigest": transaction["planDigest"],
                                   "targetBinding": record["binding"], "expiresAt": time.time() + 900, "state": transaction["state"]})
        except (ConfigBrokerError, HTTPException) as exc:
            code = error_code(exc)
            record.update(state="blocked", errorCode=code, missingRequirements=[code])
        self.store.save_receipt(key, record)

    def _reconcile_received(self, record):
        transaction = config_broker_service.get_transaction(record["receipt"]["transactionId"], owner_id=record["owner"])
        state = transaction["state"]
        if state == "ready_to_commit":
            record["state"] = "prepared"
        else:
            record["state"] = state if state in {"committed", "rolled_back", "conflict"} else "recovery_required"
        record["receipt"]["state"] = state
        record["errorCode"] = (transaction.get("error") or {}).get("code") or ("config_transaction_stale" if state == "conflict" else "")
        if state == "committed":
            record["everCommitted"] = True
            record["receipt"]["readback"] = portable.projection(record["templateId"], record["values"], record["mapping"])
            if record["receipt"]["readback"] != {row["field"]: row["after"] for row in record["diff"]}:
                record.update(state="recovery_required", errorCode="distribution_readback_mismatch")

    def _broker_action(self, record, action):
        try:
            if action == "commit":
                transaction = config_broker_service.get_transaction(record["receipt"]["transactionId"], owner_id=record["owner"])
                if transaction["state"] == "ready_to_commit":
                    portable.assert_target_ready(record["templateId"], record["mapping"])
                    config_broker_service.commit(transaction["transactionId"], owner_id=record["owner"], user_confirmed_target=True)
            else:
                config_broker_service.rollback(record["receipt"]["transactionId"], owner_id=record["owner"])
            self._reconcile_received(record)
            if record["state"] == "committed":
                expected = {row["field"]: row["after"] for row in record["diff"]}
                if record["receipt"]["readback"] != expected:
                    record.update(state="recovery_required", errorCode="distribution_readback_mismatch")
            elif record["state"] == "rolled_back":
                record["receipt"]["readback"] = portable.projection(record["templateId"], record["values"], record["mapping"])
        except (ConfigBrokerError, HTTPException) as exc:
            code = error_code(exc)
            if code.startswith("target_local_"):
                record.update(state="blocked", errorCode=code, missingRequirements=[code])
            else:
                record.update(state="conflict" if exc.status_code == 409 else "recovery_required", errorCode=code)

    async def _process_target(self, job_id, link_id):
        job = self.store.get(job_id)
        target = next(row for row in job["targets"] if row["linkId"] == link_id)
        intent = job["intent"]
        if intent == "prepare" and not target.get("reprepareRequested") and (target["approved"] or target["state"] not in {"queued", "offline"}):
            return
        if intent == "apply" and (not target["approved"] or target["state"] in {"committed", "conflict", "blocked", "cancelled", "rolled_back"}):
            return
        if intent == "cancel" and target["state"] in {"committed", "cancelled", "rolled_back"}:
            return
        if intent == "withdraw" and target["state"] in {"rolled_back", "cancelled"}:
            return
        generation = target["generation"]
        authority = job.get("cleanupAuthority", job["authority"]) if intent in {"cancel", "withdraw"} else job["authority"]
        try:
            self.authorize(authority)
            self._assert_source_target(target)
            body = {"jobId": job_id, "generation": generation}
            action = {"prepare": "prepare", "apply": "apply", "cancel": "cancel", "withdraw": "withdraw"}[intent]
            if target.get("reprepareRequested") and intent == "prepare":
                action = "status"
            if action == "apply" and target["state"] == "recovery_required":
                action = "status"
            if action == "prepare":
                body.update(templateId=job["templateId"], values=job["values"], mapping=target["mapping"])
            else:
                body.update(planDigest=target["receipt"].get("planDigest", ""), targetBinding=target["receipt"].get("targetBinding", ""))
            result = await self._exchange(target["peerId"], action, body)
            if intent == "prepare" and target.get("reprepareRequested") and result.get("state") in {"prepared", "conflict", "blocked"}:
                # Only a verified target readback may retire the prior approval.
                # A conflict after an actual write must keep its recovery proof.
                if result.get("receipt", {}).get("everCommitted"):
                    result.update(state="recovery_required", errorCode="distribution_prior_write_requires_recovery")
                else:
                    def replan(current):
                        row = next(item for item in current["targets"] if item["linkId"] == link_id)
                        if current["intent"] == "prepare" and row["generation"] == generation:
                            row.update(generation=generation + 1, state="queued", approved=False, reprepareRequested=False, receipt={}, diff=[], errorCode="", missingRequirements=[])
                    self.store.mutate(job_id, replan)
                    return await self._process_target(job_id, link_id)
            if action == "status" and result.get("state") == "prepared":
                # Reconciliation proved no commit occurred. Continue the SAME
                # approved transaction only while cancellation has not won.
                current = self.store.get(job_id)
                if current["intent"] == "apply":
                    self.authorize(authority)
                    self._assert_source_target(target)
                    result = await self._exchange(target["peerId"], "apply", body)
            # Revalidate before accepting a remote result; cancellation cannot be erased by a late reply.
            self.authorize(authority)
            self._assert_source_target(target)
            update = {key: result[key] for key in ("state", "receipt", "diff", "missingRequirements", "errorCode")}
            update.update(attempts=0, nextAttemptAt=0, reprepareRequested=intent == "prepare" and result["state"] == "recovery_required")
        except (HTTPException, ConfigBrokerError) as exc:
            code = error_code(exc)
            state = "offline" if getattr(exc, "status_code", 500) >= 500 else "conflict" if getattr(exc, "status_code", 500) == 409 else "blocked"
            attempts = target.get("attempts", 0) + 1
            update = {"state": state, "errorCode": code, "attempts": attempts,
                      "nextAttemptAt": time.time() + min(300, 5 * 2 ** min(attempts, 6)) if state == "offline" and attempts < 8 else 0}
        def save(current):
            row = next(item for item in current["targets"] if item["linkId"] == link_id)
            if row["generation"] == generation:
                row.update(update)
        self.store.mutate(job_id, save)

    async def process_once(self):
        if self._processing.locked():
            return
        async with self._processing:
            for job in self.store.jobs():
                if job["state"] == "partial" and any(row.get("nextAttemptAt", 0) and row["nextAttemptAt"] <= time.time()
                                                     for row in job["targets"] if row["state"] == "offline"):
                    job = self.store.mutate(job["jobId"], lambda current: current.update(state={"prepare": "preparing", "apply": "applying", "cancel": "cancelling", "withdraw": "withdrawing"}[current["intent"]]))
                if job["state"] not in {"preparing", "applying", "cancelling", "withdrawing"}:
                    continue
                # Bounded fan-out; targets do not block one another on offline links.
                semaphore = asyncio.Semaphore(4)
                async def run(target):
                    async with semaphore:
                        await self._process_target(job["jobId"], target["linkId"])
                await asyncio.gather(*(run(target) for target in job["targets"]))
                def settle(current):
                    if current["intent"] != job["intent"]:
                        return
                    states = {row["state"] for row in current["targets"]}
                    if current["intent"] == "prepare":
                        current["state"] = "completed" if states == {"committed"} else "awaiting_confirmation" if "prepared" in states else "partial"
                        current["planDigest"] = digest([{key: row[key] for key in ("peerId", "binding", "generation", "state", "diff", "receipt")} for row in current["targets"]])
                        current["revision"] += 1
                    elif current["intent"] == "apply":
                        current["state"] = "completed" if states == {"committed"} else "partial"
                    elif current["intent"] == "cancel":
                        current["state"] = "cancelled" if states <= {"cancelled", "committed", "rolled_back"} else "partial"
                    else:
                        current["state"] = ("withdrawn" if states <= {"rolled_back", "cancelled"} else
                                            "withdrawal_conflict" if states <= {"rolled_back", "cancelled", "conflict"} else "partial")
                self.store.mutate(job["jobId"], settle)

    async def start(self):
        if self._worker is None:
            self._worker = asyncio.create_task(self._loop())

    async def stop(self):
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def _loop(self):
        while True:
            self._wake.clear()
            try:
                await self.process_once()
            except Exception:
                # Preserve durable intent for recovery; never synthesize success.
                import logging
                logging.getLogger(__name__).exception("Configuration distribution worker failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass


config_distribution_service = ConfigDistributionService()
