"""Executor credentials under the canonical Engine identity/transaction owner.

These opaque credentials deliberately cannot become client JWTs or peer keys.
Only verifiers are persisted; the enrollment result belongs to the native host.
"""
from __future__ import annotations

import hashlib
import secrets
from urllib.parse import urlsplit

from .owner import IdentityError


def verifier(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class ExecutorIdentities:
    def __init__(self, identity):
        self.identity = identity
        with identity.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS executor_enrollments (
                    verifier TEXT PRIMARY KEY, owner_id TEXT NOT NULL, authority_id TEXT NOT NULL,
                    device_class TEXT NOT NULL, name TEXT NOT NULL, base_url TEXT NOT NULL,
                    expires_at REAL NOT NULL, consumed_at REAL);
                CREATE TABLE IF NOT EXISTS executor_identities (
                    device_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, authority_id TEXT NOT NULL,
                    device_class TEXT NOT NULL, name TEXT NOT NULL, verifier TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL, revoked_at REAL);
            """)

    def ticket(self, owner: str, *, device_class: str, name: str, base_url: str) -> dict:
        if self.identity.owners.owner()["id"] != owner:
            raise IdentityError("executor_owner_mismatch", 403)
        if not isinstance(base_url, str) or not isinstance(name, str):
            raise IdentityError("executor_identity_invalid")
        try:
            parsed = urlsplit(base_url)
            parsed.port
        except ValueError:
            raise IdentityError("executor_requires_https_origin") from None
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise IdentityError("executor_requires_https_origin")
        if device_class not in ("android", "esp32") or not 1 <= len(name) <= 80:
            raise IdentityError("executor_identity_invalid")
        authority = self.identity.instance()["instanceId"]
        token, now = "ext_" + secrets.token_urlsafe(32), self.identity.clock()
        with self.identity.transaction() as db:
            db.execute("DELETE FROM executor_enrollments WHERE expires_at<?", (now,))
            db.execute("INSERT INTO executor_enrollments VALUES (?,?,?,?,?,?,?,NULL)",
                       (verifier(token), owner, authority, device_class, name, base_url.rstrip("/"), now + 120))
        return {"ticket": token, "authorityId": authority, "baseUrl": base_url.rstrip("/"), "expiresUnixMs": int((now + 120) * 1000)}

    def enroll(self, ticket: str, authority: str, device_class: str) -> dict:
        if not isinstance(ticket, str) or not ticket.startswith("ext_") or len(ticket) > 128:
            raise IdentityError("executor_ticket_invalid", 401)
        now = self.identity.clock()
        with self.identity.transaction() as db:
            row = db.execute("SELECT * FROM executor_enrollments WHERE verifier=?", (verifier(ticket),)).fetchone()
            if (not row or row["consumed_at"] is not None or row["expires_at"] <= now
                    or row["authority_id"] != authority or row["device_class"] != device_class
                    or row["owner_id"] != self.identity.owners.owner()["id"]):
                raise IdentityError("executor_ticket_invalid", 401)
            credential, device = "exe_" + secrets.token_urlsafe(48), "executor_" + secrets.token_hex(12)
            db.execute("UPDATE executor_enrollments SET consumed_at=? WHERE verifier=?", (now, verifier(ticket)))
            db.execute("INSERT INTO executor_identities VALUES (?,?,?,?,?,?,?,NULL)",
                       (device, row["owner_id"], authority, device_class, row["name"], verifier(credential), now))
        return {"deviceId": device, "authorityId": authority, "credential": credential, "grantRevision": 1}

    def verify(self, credential: str) -> dict:
        if not isinstance(credential, str) or not credential.startswith("exe_") or len(credential) > 128:
            raise IdentityError("executor_credential_required", 401)
        with self.identity.database() as db:
            row = db.execute("SELECT * FROM executor_identities WHERE verifier=?", (verifier(credential),)).fetchone()
        if not row or row["revoked_at"] is not None or row["owner_id"] != self.identity.owners.owner()["id"]:
            raise IdentityError("executor_credential_revoked", 401)
        return {k: row[k] for k in row.keys() if k != "verifier"}

    def owned(self, owner: str, device: str) -> dict:
        with self.identity.database() as db:
            row = db.execute("SELECT * FROM executor_identities WHERE device_id=? AND owner_id=?", (device, owner)).fetchone()
        if not row or owner != self.identity.owners.owner()["id"]:
            raise IdentityError("executor_not_found", 404)
        return {k: row[k] for k in row.keys() if k != "verifier"}

    def revoke(self, owner: str, device: str) -> None:
        self.owned(owner, device)
        with self.identity.transaction() as db:
            db.execute("UPDATE executor_identities SET revoked_at=? WHERE device_id=?", (self.identity.clock(), device))
