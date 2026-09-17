"""Network transport ledger. Runtime episodes still own scheduling and handoff.

The transport never retries an action. Reconnection only queries persisted IDs.
Every admission/receipt is fenced in the canonical identity database transaction.
"""
from __future__ import annotations

import json
import threading

from core.client_identity.executors import ExecutorIdentities
from .protocol import (CAPABILITIES, GESTURES, LEASE_MS, MAX_TTL_MS, MUTATING, TERMINAL,
                       ExecutorError, action, canonical, digest, grants, identifier, integer, require)


class ExecutorService:
    def __init__(self, identity, *, runtime_database=None):
        self.identity = identity
        self.runtime_database = runtime_database
        self.identities = ExecutorIdentities(identity)
        with identity.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS executor_connections (
                    device_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL DEFAULT 0,
                    grant_revision INTEGER NOT NULL DEFAULT 1, grants TEXT NOT NULL DEFAULT '[]',
                    hello TEXT, lease_until INTEGER NOT NULL DEFAULT 0, online INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS executor_commands (
                    command_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL,
                    receipt_seq INTEGER NOT NULL DEFAULT 0, receipt TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    reconciled INTEGER NOT NULL DEFAULT 0, reconciliation_note TEXT,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS executor_commands_device ON executor_commands(device_id, created_at);
                CREATE TABLE IF NOT EXISTS executor_receipts (
                    command_id TEXT NOT NULL, seq INTEGER NOT NULL, body TEXT NOT NULL,
                    received_at INTEGER NOT NULL, PRIMARY KEY(command_id, seq));
            """)
        from .media import ExecutorMedia
        self.media = ExecutorMedia(self)

    def now(self) -> int:
        return int(self.identity.clock() * 1000)

    def _connection(self, db, device):
        db.execute("INSERT OR IGNORE INTO executor_connections(device_id) VALUES (?)", (device,))
        return db.execute("SELECT * FROM executor_connections WHERE device_id=?", (device,)).fetchone()

    def _identity(self, db, device, owner=None):
        row = db.execute("SELECT * FROM executor_identities WHERE device_id=?", (device,)).fetchone()
        require(row is not None and row["revoked_at"] is None, "executor_revoked", 401)
        require(row["owner_id"] == self.identity.owners.owner()["id"], "executor_owner_changed", 401)
        if owner is not None:
            require(row["owner_id"] == owner, "executor_not_found", 404)
        return row

    def enroll(self, payload):
        result = self.identities.enroll(payload.get("ticket"), payload.get("authorityId"), payload.get("deviceClass"))
        with self.identity.transaction() as db:
            self._connection(db, result["deviceId"])
        return result

    def list(self, owner: str) -> list[dict]:
        require(owner == self.identity.owners.owner()["id"], "executor_owner_changed", 401)
        with self.identity.database() as db:
            rows = db.execute("SELECT i.device_id,i.name,i.device_class,i.revoked_at,c.* FROM executor_identities i "
                              "LEFT JOIN executor_connections c USING(device_id) WHERE i.owner_id=?", (owner,)).fetchall()
        return [{"deviceId": r["device_id"], "name": r["name"], "deviceClass": r["device_class"],
                 "revoked": r["revoked_at"] is not None, "online": bool(r["online"] and r["lease_until"] > self.now() and r["revoked_at"] is None),
                 "grantRevision": r["grant_revision"] or 1, "grants": json.loads(r["grants"] or "[]"),
                 "capabilities": json.loads(r["hello"] or "{}").get("capabilities", [])} for r in rows]

    def grant(self, owner: str, device: str, expected: int, allowed: list) -> dict:
        allowed = grants(allowed)
        with self.identity.transaction() as db:
            identity = self._identity(db, device, owner)
            permitted = {"device.health", "android.observe", "android.capture", "android.action"} if identity["device_class"] == "android" else {"device.health", "sensor.read", "actuator.set"}
            require(all(g["capability"] in permitted for g in allowed), "device_class_capability_mismatch", 400)
            row = self._connection(db, device)
            require(type(expected) is int and expected == row["grant_revision"], "grant_revision_conflict")
            db.execute("UPDATE executor_connections SET grants=?,grant_revision=grant_revision+1,online=0,lease_until=0 WHERE device_id=?", (canonical(allowed), device))
            self._uncertain(db, device, "grant_changed")
        return {"deviceId": device, "grantRevision": expected + 1, "grants": allowed}

    def revoke(self, owner: str, device: str):
        self.identities.revoke(owner, device)
        with self.identity.transaction() as db:
            db.execute("UPDATE executor_connections SET online=0,lease_until=0 WHERE device_id=?", (device,))
            self._uncertain(db, device, "revoked")

    def _uncertain(self, db, device, reason):
        # Persisted before send: queued work is provably unsent. A sent command
        # may have acted even when neither received nor started reached Engine.
        db.execute("UPDATE executor_commands SET state='cancelled',cancel_requested=1,updated_at=? "
                   "WHERE device_id=? AND state IN ('authorized','queued')", (self.now(), device))
        db.execute("UPDATE executor_commands SET state='unknown_outcome',updated_at=?,reconciliation_note=? "
                   "WHERE device_id=? AND state IN ('sent','received','started')", (self.now(), reason, device))

    def hello(self, principal: dict, payload: dict) -> dict:
        device = principal["device_id"]
        require(payload.get("type") == "hello" and type(payload.get("protocolVersion")) is int and payload["protocolVersion"] == 1, "protocol_version_invalid", 400)
        require(payload.get("deviceId") == device and payload.get("authorityId") == principal["authority_id"], "channel_identity_mismatch", 403)
        require(payload.get("localEnabled") is True, "executor_not_armed", 403)
        identifier(payload.get("bootId")); identifier(payload.get("controlSessionId"))
        integer(payload.get("capabilityRevision"), 1, 2**53 - 1, "capability_revision_invalid")
        capabilities = grants(payload.get("capabilities"))
        allowed_class = {"device.health", "android.observe", "android.capture", "android.action"} if principal["device_class"] == "android" else {"device.health", "sensor.read", "actuator.set"}
        require(all(g["capability"] in allowed_class for g in capabilities), "device_class_capability_mismatch", 400)
        # Store a bounded typed hello; unrecognized device text cannot add authority.
        hello = {k: payload[k] for k in ("deviceId", "authorityId", "bootId", "controlSessionId", "capabilityRevision", "capabilities")}
        with self.identity.transaction() as db:
            self._identity(db, device)
            row = self._connection(db, device)
            self._uncertain(db, device, "reconnected_query_required")
            epoch = row["epoch"] + 1
            db.execute("UPDATE executor_connections SET epoch=?,hello=?,lease_until=?,online=1 WHERE device_id=?",
                       (epoch, canonical(hello), self.now() + LEASE_MS, device))
            return self._session(db, device, epoch, "session")

    def _session(self, db, device, epoch, kind):
        row = self._connection(db, device)
        return {"type": kind, "protocolVersion": 1, **{k: v for k, v in json.loads(row["hello"]).items() if k != "capabilities"},
                "leaseEpoch": epoch, "leaseExpiresUnixMs": row["lease_until"], "serverUnixMs": self.now(),
                "grantRevision": row["grant_revision"], "grants": json.loads(row["grants"])}

    def _current(self, db, device, epoch):
        self._identity(db, device)
        row = self._connection(db, device)
        require(row["epoch"] == epoch and row["online"] and row["lease_until"] > self.now(), "connection_fenced", 409)
        return row

    def renew(self, device: str, epoch: int) -> dict:
        with self.identity.transaction() as db:
            self._current(db, device, epoch)
            db.execute("UPDATE executor_connections SET lease_until=? WHERE device_id=?", (self.now() + LEASE_MS, device))
            return self._session(db, device, epoch, "lease")

    def disconnect(self, device: str, epoch: int):
        with self.identity.transaction() as db:
            row = self._connection(db, device)
            if row["epoch"] == epoch:
                db.execute("UPDATE executor_connections SET online=0,lease_until=0 WHERE device_id=?", (device,))
                self._uncertain(db, device, "disconnected")

    def create(self, *, owner: str, command_id: str, device: str, capability: str, resource: str,
               arguments: dict, precondition: dict, ttl_ms: int, trace: dict) -> dict:
        identifier(command_id); action(capability, resource, arguments, precondition)
        integer(ttl_ms, 1, MAX_TTL_MS, "ttl_invalid")
        require(bool(trace.get("runId")) and bool(trace.get("episodeId")), "runtime_trace_required", 400)
        request_digest = digest({"device": device, "capability": capability, "resource": resource,
                                 "arguments": arguments, "precondition": precondition, "ttlMs": ttl_ms, "traceRef": trace})
        with self.identity.transaction() as db:
            self._identity(db, device, owner)
            old = db.execute("SELECT * FROM executor_commands WHERE command_id=?", (command_id,)).fetchone()
            if old:
                require(old["owner_id"] == owner and old["request_digest"] == request_digest, "command_id_conflict")
                return self._view(old)
            # Orphaned pre-approval commands cannot reserve the device forever.
            pending = db.execute("SELECT command_id,body FROM executor_commands WHERE device_id=? AND state IN ('authorized','queued')", (device,)).fetchall()
            for item in pending:
                if json.loads(item["body"])["deadlineUnixMs"] <= self.now():
                    db.execute("UPDATE executor_commands SET state='expired',updated_at=? WHERE command_id=?", (self.now(), item["command_id"]))
            row = self._connection(db, device)
            self._current(db, device, row["epoch"])
            hello = json.loads(row["hello"])
            grant = {"capability": capability, "resourceId": resource}
            require(grant in json.loads(row["grants"]) and grant in hello["capabilities"], "capability_not_granted", 403)
            if capability == "android.capture" and arguments.get("scope", "window") == "display":
                display = {"capability": "android.capture", "resourceId": "display"}
                require(display in json.loads(row["grants"]) and display in hello["capabilities"], "display_capture_not_granted", 403)
            active = db.execute("SELECT 1 FROM executor_commands WHERE device_id=? AND state NOT IN "
                                "('succeeded','failed','rejected','cancelled','expired','unknown_outcome')", (device,)).fetchone()
            require(not active, "device_busy")
            if capability in MUTATING:
                unknown = db.execute("SELECT 1 FROM executor_commands WHERE device_id=? AND state='unknown_outcome' AND reconciled=0", (device,)).fetchone()
                require(not unknown, "outcome_reconciliation_required")
            if capability == "android.action":
                require(all(precondition.get(k) == hello[k] for k in ("deviceId", "bootId", "controlSessionId"))
                        and precondition.get("resourceId") == resource, "observation_session_stale")
                self._observation_current(db, device, hello, precondition, gesture=arguments.get("action") in GESTURES)
            now = self.now()
            command = {"type": "command", "protocolVersion": 1, "commandId": command_id,
                       **{k: hello[k] for k in ("deviceId", "authorityId", "bootId", "controlSessionId", "capabilityRevision")},
                       "leaseEpoch": row["epoch"], "grantRevision": row["grant_revision"], "issuedUnixMs": now,
                       "deadlineUnixMs": min(now + ttl_ms, row["lease_until"]), "ttlMs": ttl_ms,
                       "capability": capability, "resourceId": resource, "arguments": arguments,
                       "precondition": precondition, "traceRef": trace}
            command["commandDigest"] = digest(command)
            db.execute("INSERT INTO executor_commands(command_id,device_id,owner_id,request_digest,body,state,created_at,updated_at) VALUES (?,?,?,?,?,'authorized',?,?)",
                       (command_id, device, owner, request_digest, canonical(command), now, now))
            return self._view(db.execute("SELECT * FROM executor_commands WHERE command_id=?", (command_id,)).fetchone())

    def _observation_current(self, db, device, hello, precondition, *, gesture=False):
        rows = db.execute("SELECT command_id,body,receipt,updated_at FROM executor_commands WHERE device_id=? AND state='succeeded' "
                          "AND receipt IS NOT NULL ORDER BY updated_at DESC,rowid DESC LIMIT 16", (device,)).fetchall()
        for row in rows:
            observation = json.loads(row["receipt"]).get("observation") or {}
            if observation.get("resourceId") == precondition.get("resourceId") and observation.get("observationId") != precondition.get("observationId"):
                raise ExecutorError("observation_superseded")
            if observation.get("observationId") == precondition.get("observationId"):
                require(self.now() - row["updated_at"] <= 10_000, "observation_expired")
                observed = observation.get("observedUnixMs")
                require(type(observed) is int and -1000 <= self.now() - observed <= 10_000, "observation_expired")
                require(all(observation.get(k) == hello[k] for k in ("deviceId", "bootId", "controlSessionId")), "observation_session_stale")
                keys = ("appId", "windowId", "geometryRevision", "rotation", "viewport", "width", "height") if gesture else ("appId", "windowId", "nodeMapRevision")
                require(all(observation.get(k) == precondition.get(k) for k in keys), "observation_target_stale")
                if gesture:
                    require(observation.get("captureScope") == "window", "gesture_requires_window_capture", 403)
                    frame = observation.get("frame") or {}
                    require(frame.get("frameId") == precondition.get("frameId"), "frame_mismatch")
                    self.media.validate_frame(db, row["command_id"], observation, accepted=True)
                    old_command = json.loads(row["body"])
                    connection = self._connection(db, device)
                    require(old_command["leaseEpoch"] == connection["epoch"] and old_command["grantRevision"] == connection["grant_revision"], "observation_authority_stale")
                return
        raise ExecutorError("observation_not_found")

    def activate(self, owner: str, command_id: str):
        with self.identity.transaction() as db:
            row = self._command(db, owner, command_id)
            if row["state"] == "authorized":
                db.execute("UPDATE executor_commands SET state='queued',updated_at=? WHERE command_id=?", (self.now(), command_id))

    def outbound(self, device: str, epoch: int) -> list[dict]:
        with self.identity.transaction() as db:
            connection = self._current(db, device, epoch)
            rows = db.execute("SELECT * FROM executor_commands WHERE device_id=? AND state='queued' ORDER BY created_at LIMIT 1", (device,)).fetchall()
            output = []
            for row in rows:
                command = json.loads(row["body"])
                valid = (command["leaseEpoch"] == epoch and command["grantRevision"] == connection["grant_revision"]
                         and command["deadlineUnixMs"] > self.now())
                if not valid:
                    db.execute("UPDATE executor_commands SET state='expired',updated_at=? WHERE command_id=?", (self.now(), row["command_id"]))
                    continue
                # The transaction commits before the transport can send bytes.
                db.execute("UPDATE executor_commands SET state='sent',updated_at=? WHERE command_id=?", (self.now(), row["command_id"]))
                output.append(command)
            return output

    def queries(self, device: str) -> list[dict]:
        with self.identity.database() as db:
            rows = db.execute("SELECT command_id FROM executor_commands WHERE device_id=? AND state='unknown_outcome' AND reconciled=0 ORDER BY created_at DESC LIMIT 64", (device,)).fetchall()
        return [{"type": "query", "commandId": row["command_id"]} for row in rows]

    def controls(self, device: str, epoch: int) -> list[dict]:
        with self.identity.transaction() as db:
            self._current(db, device, epoch)
            rows = db.execute("SELECT body FROM executor_commands WHERE device_id=? AND cancel_requested=1 AND state IN ('sent','received','started')", (device,)).fetchall()
        return [{"type": "cancel", "commandId": (c := json.loads(row["body"]))["commandId"], "commandDigest": c["commandDigest"]} for row in rows]

    def receipt(self, device: str, epoch: int, payload: dict) -> dict:
        require(type(payload.get("protocolVersion")) is int and payload["protocolVersion"] == 1 and payload.get("type") == "receipt", "receipt_invalid", 400)
        status = payload.get("status")
        require(isinstance(status, str) and status in TERMINAL | {"received", "started"}, "receipt_status_invalid", 400)
        seq = integer(payload.get("receiptSeq"), 1, 2**53 - 1, "receipt_sequence_invalid")
        integer(payload.get("deviceMonotonicMs"), 0, 2**53 - 1, "receipt_time_invalid")
        with self.identity.transaction() as db:
            self._current(db, device, epoch)
            row = db.execute("SELECT * FROM executor_commands WHERE command_id=? AND device_id=?", (payload.get("commandId"), device)).fetchone()
            require(row is not None, "command_not_found", 404)
            command = json.loads(row["body"])
            # Query responses may refer to an older boot/epoch; compare with that
            # immutable command, never require a historical receipt to act again.
            for key in ("commandDigest", "deviceId", "authorityId", "bootId", "controlSessionId", "leaseEpoch", "grantRevision"):
                require(payload.get(key) == command[key], "receipt_identity_mismatch", 403)
            old = db.execute("SELECT body FROM executor_receipts WHERE command_id=? AND seq=?", (row["command_id"], seq)).fetchone()
            if old:
                require(old["body"] == canonical(payload), "receipt_sequence_conflict")
                return self._view(row)
            require(seq > row["receipt_seq"], "receipt_sequence_stale")
            require(row["state"] not in {"authorized", "queued", "expired"}, "receipt_for_unsent_command")
            if row["state"] in TERMINAL - {"unknown_outcome"}:
                require(status == row["state"], "terminal_receipt_conflict")
            if row["state"] == "started":
                require(status != "received", "receipt_state_regression")
            observation = payload.get("observation")
            if command["capability"] == "android.capture" and status == "succeeded":
                require(isinstance(observation, dict) and isinstance(observation.get("frame"), dict), "capture_frame_required")
            if observation is not None:
                require(isinstance(observation, dict), "observation_invalid", 400)
                require(all(observation.get(k) == command[k] for k in ("deviceId", "bootId", "controlSessionId", "resourceId")), "observation_identity_mismatch", 403)
                if observation.get("frame") is not None:
                    self.media.validate_frame(db, row["command_id"], observation)
                    # Publishing a frame is not a recoverable historical action:
                    # canceled, expired or disconnected callbacks never become media.
                    self.media.current_command(db, device, row["command_id"], command["commandDigest"])
                    require(status == "succeeded", "frame_requires_completed_capture")
                    from .media import RETENTION_MS
                    db.execute("UPDATE executor_media SET state='accepted',expires_at=? WHERE media_id=?", (self.now() + RETENTION_MS, observation["frame"]["mediaId"]))
            db.execute("INSERT INTO executor_receipts VALUES (?,?,?,?)", (row["command_id"], seq, canonical(payload), self.now()))
            # Late progress cannot revive a disconnected/cancelled runtime action.
            effective = "unknown_outcome" if row["state"] == "unknown_outcome" and status not in TERMINAL else status
            db.execute("UPDATE executor_commands SET state=?,receipt_seq=?,receipt=?,updated_at=? WHERE command_id=?",
                       (effective, seq, canonical(payload), self.now(), row["command_id"]))
            return self._view(db.execute("SELECT * FROM executor_commands WHERE command_id=?", (row["command_id"],)).fetchone())

    def _command(self, db, owner, command_id):
        row = db.execute("SELECT * FROM executor_commands WHERE command_id=? AND owner_id=?", (command_id, owner)).fetchone()
        require(row is not None and owner == self.identity.owners.owner()["id"], "command_not_found", 404)
        return row

    def _view(self, row):
        return {"commandId": row["command_id"], "deviceId": row["device_id"], "status": row["state"],
                "command": json.loads(row["body"]), "receipt": json.loads(row["receipt"]) if row["receipt"] else None,
                "cancelRequested": bool(row["cancel_requested"]), "reconciled": bool(row["reconciled"]),
                "businessVerification": "unverified", "updatedUnixMs": row["updated_at"]}

    def status(self, owner: str, command_id: str):
        with self.identity.transaction() as db:
            row = self._command(db, owner, command_id)
            command = json.loads(row["body"])
            if row["state"] not in TERMINAL and command["deadlineUnixMs"] <= self.now():
                state = "expired" if row["state"] in {"authorized", "queued"} else "unknown_outcome"
                db.execute("UPDATE executor_commands SET state=?,cancel_requested=1,updated_at=? WHERE command_id=?", (state, self.now(), command_id))
                row = self._command(db, owner, command_id)
            result = self._view(row)
        return self.media.project(result)

    def cancel(self, owner: str, command_id: str):
        with self.identity.transaction() as db:
            row = self._command(db, owner, command_id)
            state = "cancelled" if row["state"] in {"authorized", "queued"} else row["state"]
            db.execute("UPDATE executor_commands SET state=?,cancel_requested=1,updated_at=? WHERE command_id=?", (state, self.now(), command_id))
        return self.status(owner, command_id)

    def reconcile(self, owner: str, command_id: str, note: str):
        require(isinstance(note, str) and 1 <= len(note) <= 1000, "reconciliation_note_required", 400)
        with self.identity.transaction() as db:
            row = self._command(db, owner, command_id)
            require(row["state"] == "unknown_outcome" and json.loads(row["body"])["deadlineUnixMs"] <= self.now(), "reconciliation_not_ready")
            db.execute("UPDATE executor_commands SET reconciled=1,reconciliation_note=? WHERE command_id=?", (note, command_id))
        # Acknowledgement permits a new decision, it never manufactures success.
        return self.status(owner, command_id)


_service = None
_lock = threading.Lock()


def get_executor_service() -> ExecutorService:
    global _service
    with _lock:
        if _service is None:
            from core.client_identity import get_identity_service
            _service = ExecutorService(get_identity_service())
        return _service
