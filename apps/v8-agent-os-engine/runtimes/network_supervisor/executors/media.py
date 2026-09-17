"""Bounded device image ingress and publication through the existing artifact owner.

No image library is required by the server profile. Android supplies metadata-free
baseline JPEG; stdlib validates its markers, dimensions, size and transport hash.
This is format/transport validation, not a claim to decode or understand pixels.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path

from .protocol import ExecutorError, canonical, frame_geometry, identifier, integer, require

MAX_BYTES = 2 * 1024 * 1024
DEVICE_BUDGET = 64 * 1024 * 1024
RETENTION_MS = 24 * 60 * 60 * 1000
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Accept a single baseline JPEG, no EXIF/ICC/comments/trailing payload."""
    require(4 <= len(data) <= MAX_BYTES and data[:2] == b"\xff\xd8", "jpeg_invalid", 400)
    offset, dimensions, scanned, tables = 2, None, False, set()
    while offset < len(data):
        require(data[offset] == 255, "jpeg_marker_invalid", 400)
        while offset < len(data) and data[offset] == 255:
            offset += 1
        require(offset < len(data), "jpeg_truncated", 400)
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            require(scanned and dimensions is not None and offset == len(data), "jpeg_incomplete", 400)
            return dimensions
        require(marker in {0xC0, 0xC4, 0xDB, 0xDD, 0xDA, 0xE0}, "jpeg_marker_unsupported", 400)
        require(offset + 2 <= len(data), "jpeg_truncated", 400)
        length = int.from_bytes(data[offset:offset + 2], "big")
        require(length >= 2 and offset + length <= len(data), "jpeg_truncated", 400)
        payload = data[offset + 2:offset + length]
        offset += length
        if marker == 0xE0:
            require(payload.startswith(b"JFIF\x00") and len(payload) >= 14, "jpeg_metadata_not_allowed", 400)
            require(len(payload) == 14 + 3 * payload[12] * payload[13], "jpeg_app0_invalid", 400)
        if marker in {0xC4, 0xDB}:
            require(bool(payload), "jpeg_table_invalid", 400)
            tables.add(marker)
        if marker == 0xC0:
            require(dimensions is None and len(payload) >= 6 and payload[0] == 8, "jpeg_frame_invalid", 400)
            height, width = int.from_bytes(payload[1:3], "big"), int.from_bytes(payload[3:5], "big")
            components = payload[5]
            require(components in (1, 3) and len(payload) == 6 + components * 3, "jpeg_frame_invalid", 400)
            require(0 < width <= 4096 and 0 < height <= 4096 and width * height <= 8 * 1024 * 1024, "frame_dimensions_invalid", 400)
            dimensions = width, height
        if marker == 0xDA:
            require(not scanned and dimensions is not None and tables == {0xC4, 0xDB}, "jpeg_scan_invalid", 400)
            require(len(payload) >= 6 and payload[0] in (1, 3) and len(payload) == 1 + payload[0] * 2 + 3
                    and payload[-3:] == b"\x00\x3f\x00", "jpeg_scan_invalid", 400)
            scanned = True
            while offset < len(data):
                found = data.find(b"\xff", offset)
                require(found >= 0 and found + 1 < len(data), "jpeg_truncated", 400)
                next_marker = data[found + 1]
                if next_marker == 0 or 0xD0 <= next_marker <= 0xD7:
                    offset = found + 2
                    continue
                offset = found
                break
    raise ExecutorError("jpeg_truncated", 400)


class ExecutorMedia:
    def __init__(self, service):
        self.service, self.identity = service, service.identity
        self.root = self.identity.home / "cache" / "executor-media"
        self.root.mkdir(parents=True, exist_ok=True)
        with self.identity.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS executor_media (
                    media_id TEXT PRIMARY KEY, command_id TEXT NOT NULL UNIQUE, device_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL, manifest TEXT NOT NULL, digest TEXT NOT NULL, byte_length INTEGER NOT NULL,
                    state TEXT NOT NULL, expires_at INTEGER NOT NULL, artifact_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL, run_id TEXT NOT NULL, published_at INTEGER);
                CREATE INDEX IF NOT EXISTS executor_media_device ON executor_media(device_id,state);
            """)

    def _database(self):
        if self.service.runtime_database is not None:
            return self.service.runtime_database
        from core.database import db
        return db

    def _run(self, command, owner):
        from core.client_identity.owner import session_identifier
        run = self._database().get_run_record(command["traceRef"]["runId"])
        account = self.identity.owners.owner()
        require(run is not None and run.get("user_id") in {owner, session_identifier(account)} and account["id"] == owner,
                "media_runtime_owner_mismatch", 403)
        session = str(run.get("session_id") or "")
        require(bool(session) and self._database().get_session(session) is not None, "media_session_unavailable", 409)
        return run, session

    def path(self, media_id: str, *, temporary=False) -> Path:
        require(isinstance(media_id, str) and re.fullmatch(r"media_[0-9a-f]{32}", media_id), "media_not_found", 404)
        return self.root / (media_id + (".upload" if temporary else ".jpg"))

    def cleanup(self):
        with self.identity.transaction() as db:
            rows = db.execute("SELECT media_id FROM executor_media WHERE expires_at<=? AND state!='deleted'", (self.service.now(),)).fetchall()
            for row in rows:
                self.path(row["media_id"]).unlink(missing_ok=True)
                self.path(row["media_id"], temporary=True).unlink(missing_ok=True)
            db.execute("UPDATE executor_media SET state='deleted' WHERE expires_at<=?", (self.service.now(),))
            db.execute("DELETE FROM executor_media WHERE state='deleted' AND expires_at<?", (self.service.now() - 7 * RETENTION_MS,))

    def current_command(self, db, device, command_id, command_digest):
        row = db.execute("SELECT * FROM executor_commands WHERE command_id=? AND device_id=?", (command_id, device)).fetchone()
        require(row is not None, "command_not_found", 404)
        command = json.loads(row["body"])
        require(command["commandDigest"] == command_digest, "command_digest_mismatch", 403)
        require(row["state"] in {"received", "started"} and not row["cancel_requested"]
                and command["deadlineUnixMs"] > self.service.now(), "media_command_not_current")
        connection = self.service._current(db, device, command["leaseEpoch"])
        require(command["grantRevision"] == connection["grant_revision"], "grant_revision_stale", 403)
        hello = json.loads(connection["hello"])
        require(all(command[k] == hello[k] for k in ("bootId", "controlSessionId", "capabilityRevision")), "capture_session_stale")
        capture_grant = {"capability": "android.capture", "resourceId": command["resourceId"]}
        require(capture_grant in json.loads(connection["grants"]) and capture_grant in hello["capabilities"], "capture_not_granted", 403)
        return row, command, connection

    def reserve(self, principal: dict, payload: dict):
        self.cleanup()
        require(set(payload) == {"commandId", "commandDigest", "observation", "byteLength", "sha256", "mimeType"}, "media_manifest_invalid", 400)
        size = integer(payload["byteLength"], 1, MAX_BYTES, "media_size_invalid")
        require(payload["mimeType"] == "image/jpeg" and isinstance(payload["sha256"], str) and _SHA.fullmatch(payload["sha256"]), "media_type_or_hash_invalid", 400)
        observation = payload["observation"]
        require(isinstance(observation, dict), "observation_invalid", 400)
        frame_geometry(observation)
        for key in ("observationId", "windowId", "appId"):
            identifier(observation.get(key), "observation_anchor_required")
        require(observation.get("captureScope") in ("window", "display") and observation.get("availability") == "screenshot_only", "capture_scope_invalid", 400)
        frame = observation.get("frame")
        require(isinstance(frame, dict) and set(frame) == {"frameId", "sha256", "mimeType", "width", "height"}, "frame_invalid", 400)
        identifier(frame.get("frameId"), "frame_required")
        require(observation.get("frameId", frame["frameId"]) == frame["frameId"], "frame_manifest_mismatch", 400)
        require(frame["sha256"] == payload["sha256"] and frame["mimeType"] == payload["mimeType"]
                and frame["width"] == observation["width"] and frame["height"] == observation["height"], "frame_manifest_mismatch", 400)
        observed = integer(observation.get("observedUnixMs"), 0, 2**53 - 1, "observation_time_invalid")
        require(-1000 <= self.service.now() - observed <= 10000, "observation_expired")
        with self.identity.transaction() as db:
            row, command, connection = self.current_command(db, principal["device_id"], payload["commandId"], payload["commandDigest"])
            require(command["capability"] == "android.capture", "capture_command_required", 403)
            require(observation["captureScope"] == command["arguments"].get("scope", "window"), "capture_scope_mismatch", 403)
            require(all(observation.get(k) == command[k] for k in ("deviceId", "bootId", "controlSessionId", "resourceId"))
                    and observation["appId"] == command["resourceId"], "observation_identity_mismatch", 403)
            if observation["captureScope"] == "display":
                require({"capability": "android.capture", "resourceId": "display"} in json.loads(connection["grants"]), "display_capture_not_granted", 403)
            run, session = self._run(command, row["owner_id"])
            old = db.execute("SELECT * FROM executor_media WHERE command_id=?", (payload["commandId"],)).fetchone()
            if old:
                require(old["manifest"] == canonical(payload) and old["state"] in {"reserved", "uploaded"}, "media_reservation_conflict")
                return self._reservation(old)
            size_now = db.execute("SELECT COALESCE(SUM(byte_length),0) FROM executor_media WHERE device_id=? AND state!='deleted'", (principal["device_id"],)).fetchone()[0]
            require(size_now + size <= DEVICE_BUDGET, "device_media_budget_exceeded", 429)
            media_id = "media_" + secrets.token_hex(16)
            db.execute("INSERT INTO executor_media VALUES (?,?,?,?,?,?,?,'reserved',?,?,?,?,NULL)",
                       (media_id, payload["commandId"], principal["device_id"], row["owner_id"], canonical(payload), payload["sha256"], size,
                        command["deadlineUnixMs"], "art_" + secrets.token_hex(16), session, run["id"]))
            return self._reservation(db.execute("SELECT * FROM executor_media WHERE media_id=?", (media_id,)).fetchone())

    def _reservation(self, row):
        return {"mediaId": row["media_id"], "uploadPath": "/api/executor/media/" + row["media_id"], "maxBytes": MAX_BYTES, "expiresUnixMs": row["expires_at"]}

    def begin(self, principal, media_id):
        with self.identity.transaction() as db:
            row = db.execute("SELECT * FROM executor_media WHERE media_id=? AND device_id=?", (media_id, principal["device_id"])).fetchone()
            require(row is not None, "media_not_found", 404)
            manifest = json.loads(row["manifest"])
            self.current_command(db, principal["device_id"], row["command_id"], manifest["commandDigest"])
            require(row["state"] == "reserved" and row["expires_at"] > self.service.now(), "media_upload_not_available")
            db.execute("UPDATE executor_media SET state='uploading' WHERE media_id=?", (media_id,))
            return dict(row)

    def finish(self, principal, media_id):
        path = self.path(media_id, temporary=True)
        data = path.read_bytes()
        width, height = jpeg_dimensions(data)
        sha = hashlib.sha256(data).hexdigest()
        with self.identity.transaction() as db:
            row = db.execute("SELECT * FROM executor_media WHERE media_id=? AND device_id=?", (media_id, principal["device_id"])).fetchone()
            require(row is not None and row["state"] == "uploading", "media_upload_not_available")
            manifest = json.loads(row["manifest"])
            self.current_command(db, principal["device_id"], row["command_id"], manifest["commandDigest"])
            require(row["expires_at"] > self.service.now() and len(data) == row["byte_length"] and sha == row["digest"], "media_content_mismatch", 400)
            require(width == manifest["observation"]["width"] and height == manifest["observation"]["height"], "media_dimensions_mismatch", 400)
            path.replace(self.path(media_id))
            db.execute("UPDATE executor_media SET state='uploaded' WHERE media_id=?", (media_id,))
            return {"mediaId": media_id, "frameId": manifest["observation"]["frame"]["frameId"], "sha256": sha, "width": width, "height": height, "status": "uploaded"}

    def abort(self, media_id):
        # Exact server-generated paths only. Never remove a published observation.
        with self.identity.transaction() as db:
            row = db.execute("SELECT state FROM executor_media WHERE media_id=?", (media_id,)).fetchone()
            if row and row["state"] not in {"accepted", "published"}:
                self.path(media_id).unlink(missing_ok=True)
                self.path(media_id, temporary=True).unlink(missing_ok=True)
                db.execute("UPDATE executor_media SET state='deleted' WHERE media_id=?", (media_id,))

    def delete(self, principal, media_id):
        with self.identity.database() as db:
            row = db.execute("SELECT state FROM executor_media WHERE media_id=? AND device_id=?", (media_id, principal["device_id"])).fetchone()
        require(row is not None, "media_not_found", 404)
        require(row["state"] not in {"accepted", "published"}, "media_already_published")
        self.abort(media_id)

    def delete_owned(self, owner, media_id):
        require(owner == self.identity.owners.owner()["id"], "media_owner_mismatch", 403)
        with self.identity.transaction() as db:
            row = db.execute("SELECT * FROM executor_media WHERE media_id=? AND owner_id=?", (media_id, owner)).fetchone()
            require(row is not None, "media_not_found", 404)
            self.path(media_id).unlink(missing_ok=True)
            self.path(media_id, temporary=True).unlink(missing_ok=True)
            db.execute("UPDATE executor_media SET state='deleted' WHERE media_id=?", (media_id,))

    def validate_frame(self, db, command_id, observation, *, accepted=False):
        frame = observation.get("frame") or {}
        row = db.execute("SELECT * FROM executor_media WHERE media_id=? AND command_id=?", (frame.get("mediaId"), command_id)).fetchone()
        allowed = {"accepted", "published"} if accepted else {"uploaded"}
        require(row is not None and row["state"] in allowed and row["expires_at"] > self.service.now(), "media_frame_unavailable")
        supplied = {**observation, "frame": {k: v for k, v in frame.items() if k != "mediaId"}}
        require(canonical(supplied) == canonical(json.loads(row["manifest"])["observation"]), "frame_observation_mismatch", 403)
        require(self.path(row["media_id"]).is_file(), "media_frame_gone", 410)
        return row

    def verified_artifact_metadata(self, *, session_id, artifact_id, path):
        """Read qualification from the persisted upload ledger, never a flag."""
        with self.identity.database() as db:
            row = db.execute("SELECT * FROM executor_media WHERE artifact_id=? AND session_id=? AND state='published'", (artifact_id, session_id)).fetchone()
        require(row is not None and row["owner_id"] == self.identity.owners.owner()["id"], "executor_frame_reference_invalid", 403)
        require(row["expires_at"] > self.service.now(), "executor_frame_expired", 410)
        expected = self.path(row["media_id"]).resolve(strict=False)
        require(Path(path).resolve(strict=False) == expected and expected.is_file(), "executor_frame_reference_invalid", 403)
        artifact = self._database().get_runtime_artifact(artifact_id)
        require(artifact is not None, "executor_frame_reference_invalid", 403)
        manifest = json.loads(row["manifest"])
        frame = manifest["observation"]["frame"]
        metadata = artifact.get("metadata") or {}
        expected_metadata = {"sha256": row["digest"], "width": frame["width"], "height": frame["height"],
                             "frameId": frame["frameId"], "expiresUnixMs": row["expires_at"]}
        require(all(metadata.get(k) == v for k, v in expected_metadata.items()), "executor_frame_metadata_changed", 403)
        return expected_metadata

    def project(self, result):
        observation = (result.get("receipt") or {}).get("observation") or {}
        frame = observation.get("frame")
        if result["status"] != "succeeded" or not isinstance(frame, dict) or not frame.get("mediaId"):
            return result
        with self.identity.database() as db:
            row = db.execute("SELECT * FROM executor_media WHERE media_id=? AND command_id=?", (frame["mediaId"], result["commandId"])).fetchone()
        if not row or row["state"] not in {"accepted", "published"} or row["expires_at"] <= self.service.now() or not self.path(row["media_id"]).is_file():
            return {**result, "mediaStatus": "gone"}
        database = self._database()
        artifact = database.get_runtime_artifact(row["artifact_id"])
        if row["state"] == "accepted":
            self._run(result["command"], row["owner_id"])
            if artifact is None:
                from core.artifact_store import ArtifactStore
                from core.workspace_authority import workspace_authority_service
                scope = workspace_authority_service.resolve(runtime_kind="chat", session_id=row["session_id"])
                require(bool(scope.workspace_root), "media_workspace_unavailable")
                artifact = ArtifactStore(database=database).record_local_file(file_path=self.path(row["media_id"]), artifact_id=row["artifact_id"],
                    session_id=row["session_id"], run_id=row["run_id"], resource_role="source_derivative", source_id=frame["frameId"], auto_attach_to_message=False,
                    workspace_path=str(scope.workspace_root), metadata={"deviceId": result["deviceId"], "commandId": result["commandId"],
                        "sha256": frame["sha256"], "frameId": frame["frameId"], "width": frame["width"], "height": frame["height"],
                        "captureScope": observation["captureScope"], "executorCapture": True,
                        "workspacePath": str(scope.workspace_root), "expiresUnixMs": row["expires_at"]},
                    source_component="device_executor", node="capture")
            with self.identity.transaction() as db:
                db.execute("UPDATE executor_media SET state='published',published_at=? WHERE media_id=? AND state='accepted'", (self.service.now(), row["media_id"]))
        if artifact is None:
            return {**result, "mediaStatus": "gone"}  # Deletion does not recreate an artifact.
        return {**result, "mediaStatus": "available", "artifacts": [artifact],
                "screenshotRef": {"artifactId": artifact["artifactId"], "filePath": artifact["sourcePath"],
                                  "contentUrl": artifact["contentUrl"], "frameId": frame["frameId"]}}
