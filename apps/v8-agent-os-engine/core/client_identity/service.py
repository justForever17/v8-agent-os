"""Transactional device credentials in the existing state.db, with OS-kept keys."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .owner import IdentityError, OwnerStore, atomic_json, public_user, timestamp, session_identifier

ACCESS_TTL = 15 * 60
REFRESH_TTL = 30 * 24 * 60 * 60
REPLAY_TTL = 60


def encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def decode(data: str) -> bytes:
    return base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)


def epoch(value: str) -> float:
    if not isinstance(value, str):
        raise ValueError("invalid identity timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("identity timestamp requires timezone")
    return parsed.timestamp()


class ClientIdentityService:
    def __init__(self, home: Path, credential_store, *, clock=time.time, config_reader=None):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "state.db"
        self.owners = OwnerStore(self.home)
        self.credentials, self.clock = credential_store, clock
        self.config_reader = config_reader
        self.lock = threading.RLock()
        self._keys: dict[str, str] = {}
        self._initialized = False
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS client_identity_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS client_devices (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, family_id TEXT NOT NULL,
                    session_identifier TEXT NOT NULL, name TEXT NOT NULL, surface TEXT NOT NULL,
                    hidden INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                    expires_at REAL NOT NULL, last_used_at REAL, revoked_at REAL,
                    legacy_until REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS client_refresh_tokens (
                    token_hash TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                    generation INTEGER NOT NULL, expires_at REAL NOT NULL, consumed_at REAL);
                CREATE INDEX IF NOT EXISTS client_refresh_device ON client_refresh_tokens(device_id);
                CREATE TABLE IF NOT EXISTS client_pairing_tickets (
                    id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, instance_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL, surface TEXT NOT NULL, base_url TEXT NOT NULL,
                    device_name TEXT NOT NULL, expires_at REAL NOT NULL, consumed_at REAL, revoked_at REAL);
                CREATE TABLE IF NOT EXISTS client_refresh_replays (
                    token_hash TEXT PRIMARY KEY, rotation_id TEXT NOT NULL, request_digest TEXT NOT NULL,
                    expires_at REAL NOT NULL, encrypted_result TEXT NOT NULL);
            """)

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def instance(self) -> dict:
        with self.lock:
            path = self.home / "runtime" / "instance.json"
            if path.exists():
                try:
                    value = json.loads(path.read_text(encoding="utf-8-sig"))
                    if value["version"] != 1 or value["product"] != "v8-agent-os" or not isinstance(value["instanceId"], str) or len(value["instanceId"]) < 16:
                        raise ValueError()
                    return value
                except (OSError, ValueError, TypeError, KeyError, AssertionError):
                    raise IdentityError("instance_identity_unavailable", 503) from None
            value = {"version": 1, "product": "v8-agent-os", "instanceId": "v8i_" + secrets.token_urlsafe(18), "createdAt": timestamp(self.clock())}
            atomic_json(path, value)
            return value

    def _key(self, purpose: str, *, imported: str = "") -> str:
        from core.security.credentials import CredentialStoreError
        with self.lock:
            if purpose in self._keys:
                return self._keys[purpose]
            reference = "cred:v8-system:identity-" + hashlib.sha256(self.instance()["instanceId"].encode()).hexdigest()[:24] + "-" + purpose
            with self.transaction() as db:
                stored = db.execute("SELECT value FROM client_identity_meta WHERE key=?", (purpose + "_ref",)).fetchone()
                try:
                    if stored:
                        result = self.credentials.resolve(stored["value"])
                    else:
                        # A recorded reference is never replaced when its key is missing.
                        try:
                            existing = self.credentials.resolve(reference)
                        except CredentialStoreError as exc:
                            # CredentialRefStore uses this error only for an
                            # explicit backend None. Permission failures are not
                            # evidence that it is safe to create/replace a key.
                            if str(exc) != "credential reference is missing":
                                raise
                            existing = None
                        if existing is not None:
                            result = existing
                            if imported and not hmac.compare_digest(result, imported):
                                raise IdentityError("identity_credential_import_conflict", 503)
                        else:
                            result = imported or secrets.token_urlsafe(48 if purpose == "signing" else 32)
                            self.credentials.put(result, reference=reference, namespace="system")
                        db.execute("INSERT INTO client_identity_meta VALUES (?,?)", (purpose + "_ref", reference))
                except CredentialStoreError:
                    raise IdentityError("identity_credentials_unavailable", 503) from None
            self._keys[purpose] = result
            return result

    def _hash_refresh(self, token: str) -> str:
        # Preserve the legacy salted hash so existing refresh credentials can migrate.
        return hashlib.sha256((self._key("signing") + ":" + token).encode()).hexdigest()

    def initialize(self) -> dict:
        """One Engine-controlled import. Never rewrites the legacy input files."""
        with self.lock:
            if self._initialized:
                return {"ok": True, "imported": False}
            with self.database() as db:
                completed = db.execute("SELECT value FROM client_identity_meta WHERE key='legacy_import_v1'").fetchone()
            if completed:
                self._initialized = True
                return {"ok": True, "imported": False}
            owner = self.owners.owner(required=False)  # Corrupt state must not be treated as a new install.
            tokens_path, secret_path = self.home / "mobile_app_tokens.json", self.home / "mobile_app_auth.json"
            secret, rows = "", []
            try:
                if secret_path.exists():
                    secret_payload = json.loads(secret_path.read_text(encoding="utf-8-sig"))
                    if not isinstance(secret_payload, dict):
                        raise ValueError()
                    secret = secret_payload.get("secret", "")
                    if not isinstance(secret, str) or not secret:
                        raise ValueError()
                if tokens_path.exists():
                    payload = json.loads(tokens_path.read_text(encoding="utf-8-sig"))
                    rows = payload["refreshTokens"]
                    if not isinstance(rows, list) or (rows and not secret):
                        raise ValueError()
                prepared = []
                now = self.clock()
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError()
                    if not owner or row.get("userId") != owner["id"] or row.get("revokedAt") or epoch(row["expiresAt"]) <= now:
                        continue
                    if row.get("sessionIdentifier") != session_identifier(owner):
                        continue
                    if not isinstance(row.get("id"), str) or not row["id"] or not isinstance(row.get("tokenHash"), str) or len(row["tokenHash"]) != 64 or any(char not in "0123456789abcdef" for char in row["tokenHash"]):
                        raise ValueError()
                    prepared.append({**row, "createdEpoch": epoch(row["createdAt"]), "expiresEpoch": epoch(row["expiresAt"]), "lastUsedEpoch": epoch(row["lastUsedAt"]) if row.get("lastUsedAt") else None})
            except (ValueError, KeyError, TypeError, OSError):
                raise IdentityError("legacy_identity_import_required", 503) from None
            self._key("signing", imported=secret)
            self._key("refresh-recovery")
            with self.transaction() as db:
                # Re-check inside the transaction for another Engine process.
                if db.execute("SELECT 1 FROM client_identity_meta WHERE key='legacy_import_v1'").fetchone():
                    self._initialized = True
                    return {"ok": True, "imported": False}
                for row in prepared:
                    expiry = row["expiresEpoch"]
                    db.execute("INSERT INTO client_devices VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                        row["id"], owner["id"], row["id"], row["sessionIdentifier"], row.get("deviceName") or "V8 client",
                        row.get("surface") or "phone", int(bool(row.get("hiddenFromDeviceList"))), row["createdEpoch"], expiry,
                        row["lastUsedEpoch"], None, min(expiry, now + 86400)))
                    db.execute("INSERT INTO client_refresh_tokens VALUES (?,?,?,?,NULL)", (row["tokenHash"], row["id"], 0, expiry))
                db.execute("INSERT INTO client_identity_meta VALUES ('legacy_import_v1',?)", (timestamp(now),))
            self._initialized = True
            return {"ok": True, "imported": True, "deviceCount": len(prepared), "legacyAccessDeadline": timestamp(now + 86400)}

    def _ready(self):
        self.initialize()
        # Resolve OS keys before opening the business transaction.
        self._key("signing")
        self._key("refresh-recovery")

    def _access(self, owner: dict, device_id: str, now: float, *, audience: str = "v8-client") -> tuple[str, str]:
        claims = {"type": "client_access", "sub": owner["id"], "sid": session_identifier(owner),
                  "did": device_id, "iat": int(now), "exp": int(now) + ACCESS_TTL,
                  "iss": self.instance()["instanceId"], "aud": audience}
        header = encode(b'{"alg":"HS256","typ":"JWT"}')
        payload = encode(json.dumps(claims, separators=(",", ":")).encode())
        signature = encode(hmac.new(self._key("signing").encode(), (header + "." + payload).encode(), hashlib.sha256).digest())
        return header + "." + payload + "." + signature, timestamp(claims["exp"])

    def _pair(self, db, owner, device_id, generation, now):
        from core.auth_context import EngineAuthContext
        token = secrets.token_urlsafe(48)
        token_hash = self._hash_refresh(token)
        db.execute("INSERT INTO client_refresh_tokens VALUES (?,?,?,?,NULL)", (token_hash, device_id, generation, now + REFRESH_TTL))
        db.execute("UPDATE client_devices SET expires_at=?,last_used_at=? WHERE id=?", (now + REFRESH_TTL, now, device_id))
        device = db.execute("SELECT hidden,surface FROM client_devices WHERE id=?", (device_id,)).fetchone()
        audience = "v8-local" if device["hidden"] else "v8-client"
        access, expiry = self._access(owner, device_id, now, audience=audience)
        context = EngineAuthContext(owner["id"], session_identifier(owner), owner["login"], owner["role"], device_id,
            int(now), int(now) + ACCESS_TTL, self.instance()["instanceId"], audience, "mobile_bearer", device["surface"], "local_client" if device["hidden"] else "human_phone")
        return {"accessToken": access, "accessTokenExpiresAt": expiry, "refreshToken": token,
                "refreshTokenExpiresAt": timestamp(now + REFRESH_TTL), "deviceId": device_id, "user": self.client_user(context)}

    def create_session(self, *, name: str, surface: str = "phone", hidden: bool = False):
        if (hidden and surface not in ("web", "cyber", "desktop_pet", "cli", "admin", "shell")) or (not hidden and surface != "phone"):
            raise IdentityError("unsupported_identity_surface")
        self._ready()
        if hidden:
            with self.owners.lock:
                if self.owners.owner(required=False) is None:
                    self.owners.bootstrap(login="owner", name="", now=self.clock(), local_pending=True)
        owner, now, device = self.owners.owner(), self.clock(), str(uuid4())
        with self.transaction() as db:
            if hidden:
                existing = db.execute("SELECT id FROM client_devices WHERE user_id=? AND surface=? AND hidden=1 AND revoked_at IS NULL AND expires_at>?", (owner["id"], surface, now)).fetchone()
                if existing:
                    generation = db.execute("SELECT COALESCE(MAX(generation),0)+1 FROM client_refresh_tokens WHERE device_id=?", (existing["id"],)).fetchone()[0]
                    db.execute("DELETE FROM client_refresh_tokens WHERE device_id=? AND expires_at<=?", (existing["id"], now))
                    return self._pair(db, owner, existing["id"], generation, now)
            db.execute("INSERT INTO client_devices VALUES (?,?,?,?,?,?,?,?,?,?,NULL,0)", (device, owner["id"], device,
                session_identifier(owner), name[:200], surface, int(hidden), now, now + REFRESH_TTL, now))
            return self._pair(db, owner, device, 0, now)

    def owner(self):
        return self.owners.owner()

    def update_profile(self, subject: str, patch: dict):
        return self.owners.update(subject, patch, now=self.clock())

    def client_user(self, context) -> dict:
        from copy import deepcopy
        from .resources import sign_resource_url
        user = deepcopy(public_user(self.owner()))
        if context.auth_method == "internal_service":
            return user
        def sign(value):
            if isinstance(value, str) and value.startswith("/user-assets/"):
                return sign_resource_url(self, value, context)
            return value
        user["image"] = sign(user.get("image"))
        appearance = user.get("appearance") or {}
        for field in ("lightBackgroundMedia", "lightBackgroundImage"):
            if field in appearance:
                appearance[field] = sign(appearance[field])
        for item in (appearance.get("webBackground") or {}).get("items", []):
            if isinstance(item, dict):
                item["media"] = sign(item.get("media"))
        return user

    def clean_profile_patch(self, context, patch: dict) -> dict:
        from copy import deepcopy
        from urllib.parse import urlsplit
        from .resources import verify_resource_request
        result = deepcopy(patch)
        existing = public_user(self.owner())
        existing_appearance = existing.get("appearance") or {}
        existing_refs = {value for value in (existing.get("image"), existing_appearance.get("lightBackgroundMedia"), existing_appearance.get("lightBackgroundImage")) if isinstance(value, str)}
        existing_refs.update(item.get("media") for item in (existing_appearance.get("webBackground") or {}).get("items", []) if isinstance(item, dict) and isinstance(item.get("media"), str))
        def clean(value):
            if isinstance(value, str) and ("v8sig=" in value or "v8exp=" in value):
                parsed = urlsplit(value)
                if not parsed.scheme and not parsed.netloc and parsed.path in existing_refs:
                    # An open profile form may outlive its preview capability.
                    return parsed.path
                verified = verify_resource_request(self, value)
                if not verified or verified.subject != context.subject:
                    raise IdentityError("profile_resource_invalid")
                return urlsplit(value).path
            return value
        if "image" in result:
            result["image"] = clean(result["image"])
        appearance = result.get("appearance")
        if isinstance(appearance, dict):
            for key in ("lightBackgroundMedia", "lightBackgroundImage"):
                if key in appearance:
                    appearance[key] = clean(appearance[key])
            playlist = appearance.get("webBackground")
            if isinstance(playlist, dict) and isinstance(playlist.get("items"), list):
                for item in playlist["items"]:
                    if isinstance(item, dict):
                        item["media"] = clean(item.get("media"))
        return result

    def migration_status(self) -> dict:
        with self.database() as db:
            marker = db.execute("SELECT value FROM client_identity_meta WHERE key='legacy_import_v1'").fetchone()
            legacy = db.execute("SELECT COUNT(*) FROM client_devices WHERE legacy_until>? AND revoked_at IS NULL", (self.clock(),)).fetchone()[0]
            references = db.execute("SELECT key,value FROM client_identity_meta WHERE key IN ('signing_ref','refresh-recovery_ref')").fetchall()
        return {"ok": True, "version": 1, "importedAt": marker["value"] if marker else None, "legacyDevicesInWindow": legacy,
            "credentialStatus": {row["key"].removesuffix("_ref"): self.credentials.status(row["value"]).configured for row in references},
            "legacyFilesReadOnly": True, "recoveryMode": "forward_restore_credentials", "rollbackToLegacyWriterAllowed": False}

    def sign_resource_for_owner(self, path: str, session_id: str = ""):
        from core.auth_context import _local_context
        from .resources import sign_resource_url
        owner = self.owner()
        if session_id:
            try:
                from core.database import db
                row = db.get_session(session_id)
            except Exception:
                row = None
            if not row or str(row.get("user_id") or row.get("userId") or "") not in {owner["id"], session_identifier(owner)}:
                raise IdentityError("session_not_found", 404)
        return sign_resource_url(self, path, _local_context(), session_id=session_id)

    def create_ticket(self, *, base_url: str, device_name: str = "", ttl_ms: int = 300000, surface: str = "phone") -> dict:
        self._ready()
        if surface != "phone":
            raise IdentityError("phone_pairing_only")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.hostname in ("localhost", "127.0.0.1", "::1"):
            raise IdentityError("pairing_reachable_https_required")
        base_url = base_url.rstrip("/").removesuffix("/api")
        owner, instance, now = self.owners.owner(), self.instance(), self.clock()
        ticket_id, code = str(uuid4()), secrets.token_urlsafe(24)
        expiry = now + max(60, min(600, ttl_ms / 1000))
        with self.transaction() as db:
            db.execute("DELETE FROM client_pairing_tickets WHERE expires_at<?", (now - 86400,))
            db.execute("INSERT INTO client_pairing_tickets VALUES (?,?,?,?,?,?,?,?,NULL,NULL)", (ticket_id,
                hashlib.sha256(code.encode()).hexdigest(), instance["instanceId"], owner["id"], "phone", base_url, device_name[:200], expiry))
        return {"pairingId": ticket_id, "instanceId": instance["instanceId"], "surface": "phone", "adminBaseUrl": base_url,
                "pairingCode": code, "pairingUri": "v8agentosphone://pair?" + urlencode({"admin": base_url, "code": code, "instance": instance["instanceId"], "surface": "phone"}), "expiresAt": timestamp(expiry)}

    def consume_ticket(self, *, code: str, instance_id: str, device_name: str = "") -> dict:
        self._ready()
        now, owner = self.clock(), self.owners.owner()
        with self.transaction() as db:
            row = db.execute("SELECT * FROM client_pairing_tickets WHERE code_hash=?", (hashlib.sha256(code.encode()).hexdigest(),)).fetchone()
            if not row or row["revoked_at"]:
                raise IdentityError("pairing_ticket_invalid")
            if row["consumed_at"] is not None:
                raise IdentityError("pairing_ticket_consumed", 410)
            if row["expires_at"] <= now:
                raise IdentityError("pairing_ticket_expired", 410)
            if instance_id != row["instance_id"] or instance_id != self.instance()["instanceId"]:
                raise IdentityError("pairing_instance_mismatch")
            if row["surface"] != "phone" or row["owner_id"] != owner["id"]:
                raise IdentityError("pairing_owner_unavailable", 401)
            device = str(uuid4())
            db.execute("UPDATE client_pairing_tickets SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (now, row["id"]))
            db.execute("INSERT INTO client_devices VALUES (?,?,?,?,?,?,?,?,?,?,NULL,0)", (device, owner["id"], device,
                session_identifier(owner), (device_name or row["device_name"] or "V8 Phone")[:200], "phone", 0, now, now + REFRESH_TTL, now))
            pair = self._pair(db, owner, device, 0, now)
            return {**pair, "instanceId": instance_id, "surface": "phone", "adminBaseUrl": row["base_url"], "linkManifest": self.manifest(row["base_url"])}

    def verify_access(self, token: str, *, now: float | None = None):
        from core.auth_context import EngineAuthContext
        if not isinstance(token, str) or len(token) > 8192:
            return None
        self._ready()
        current = self.clock() if now is None else now
        try:
            header, payload, signature = token.split(".")
            metadata, claims = json.loads(decode(header)), json.loads(decode(payload))
            if metadata != {"alg": "HS256", "typ": "JWT"} or not isinstance(claims, dict):
                return None
            expected = hmac.new(self._key("signing").encode(), (header + "." + payload).encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, decode(signature)):
                return None
            issued, expiry = claims["iat"], claims["exp"]
            if type(issued) is not int or type(expiry) is not int or issued > current + 30 or expiry <= current or expiry <= issued:
                return None
            if claims.get("type") not in ("client_access", "mobile_access") or not claims.get("did"):
                return None
            with self.database() as db:
                device = db.execute("SELECT * FROM client_devices WHERE id=?", (claims["did"],)).fetchone()
            if not device or device["revoked_at"] is not None or device["expires_at"] <= current or device["user_id"] != claims["sub"]:
                return None
            if claims["type"] == "client_access":
                audience = "v8-local" if device["hidden"] else "v8-client"
                if claims.get("iss") != self.instance()["instanceId"] or claims.get("aud") != audience or expiry - issued > ACCESS_TTL:
                    return None
            elif device["legacy_until"] <= current or expiry - issued > 86400:
                return None
            owner = self.owners.owner()
            if owner["id"] != device["user_id"] or claims["sid"] != device["session_identifier"]:
                return None
            # Current owner metadata, never role/login asserted by the JWT caller.
            return EngineAuthContext(owner["id"], session_identifier(owner), owner["login"], owner["role"], device["id"], issued, expiry,
                claims.get("iss"), claims.get("aud"), "mobile_bearer", device["surface"], "local_client" if device["hidden"] else "human_phone")
        except IdentityError as exc:
            if exc.status == 503:
                raise
            return None
        except (ValueError, TypeError, KeyError, UnicodeError):
            return None

    def refresh(self, token: str, *, rotation_id: str = "", device_name: str = "", phone_only: bool = False) -> dict:
        self._ready()
        if not token or len(token) > 512 or len(rotation_id) > 128:
            raise IdentityError("refresh_token_invalid", 401)
        token_hash, now = self._hash_refresh(token), self.clock()
        digest = hashlib.sha256(json.dumps({"deviceName": device_name}, sort_keys=True).encode()).hexdigest()
        key = decode(self._key("refresh-recovery"))
        failure = False
        with self.transaction() as db:
            db.execute("DELETE FROM client_refresh_replays WHERE expires_at<=?", (now,))
            record = db.execute("SELECT r.*,d.user_id,d.revoked_at,d.session_identifier,d.hidden,d.surface FROM client_refresh_tokens r JOIN client_devices d ON d.id=r.device_id WHERE r.token_hash=?", (token_hash,)).fetchone()
            if not record or record["revoked_at"] is not None or record["expires_at"] <= now:
                raise IdentityError("refresh_token_invalid", 401)
            if phone_only and (record["hidden"] or record["surface"] != "phone"):
                # Reject before consuming, replaying, or revoking any token.
                raise IdentityError("phone_device_credential_required", 403)
            owner = self.owners.owner()
            if owner["id"] != record["user_id"]:
                raise IdentityError("refresh_token_invalid", 401)
            if record["consumed_at"] is not None:
                replay = db.execute("SELECT * FROM client_refresh_replays WHERE token_hash=?", (token_hash,)).fetchone()
                if rotation_id and replay and replay["rotation_id"] == rotation_id and replay["request_digest"] == digest:
                    encrypted = decode(replay["encrypted_result"])
                    try:
                        return json.loads(AESGCM(key).decrypt(encrypted[:12], encrypted[12:], (token_hash + ":" + rotation_id).encode()))
                    except Exception:
                        raise IdentityError("refresh_recovery_unavailable", 503) from None
                db.execute("UPDATE client_devices SET revoked_at=? WHERE id=?", (now, record["device_id"]))
                failure = True  # Commit family revocation before returning an error.
            else:
                db.execute("UPDATE client_refresh_tokens SET consumed_at=? WHERE token_hash=?", (now, token_hash))
                if device_name:
                    db.execute("UPDATE client_devices SET name=? WHERE id=?", (device_name[:200], record["device_id"]))
                result = self._pair(db, owner, record["device_id"], record["generation"] + 1, now)
                if rotation_id:
                    nonce = secrets.token_bytes(12)
                    ciphertext = nonce + AESGCM(key).encrypt(nonce, json.dumps(result, separators=(",", ":")).encode(), (token_hash + ":" + rotation_id).encode())
                    db.execute("INSERT INTO client_refresh_replays VALUES (?,?,?,?,?)", (token_hash, rotation_id, digest, now + REPLAY_TTL, encode(ciphertext)))
        if failure:
            raise IdentityError("refresh_token_replayed", 401)
        return result

    def revoke(self, subject: str, device_id: str) -> bool:
        with self.transaction() as db:
            changed = db.execute("UPDATE client_devices SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL", (self.clock(), device_id, subject)).rowcount
            if changed:
                db.execute("DELETE FROM client_refresh_replays WHERE token_hash IN (SELECT token_hash FROM client_refresh_tokens WHERE device_id=?)", (device_id,))
            return bool(changed)

    def logout(self, token: str, *, phone_only: bool = False) -> bool:
        self._ready()
        with self.database() as db:
            row = db.execute("SELECT d.user_id,d.id,d.hidden,d.surface FROM client_devices d JOIN client_refresh_tokens r ON r.device_id=d.id WHERE r.token_hash=?", (self._hash_refresh(token),)).fetchone()
        if row and phone_only and (row["hidden"] or row["surface"] != "phone"):
            raise IdentityError("phone_device_credential_required", 403)
        return self.revoke(row["user_id"], row["id"]) if row else False

    def devices(self, subject: str) -> list[dict]:
        self._ready()
        with self.database() as db:
            rows = db.execute("SELECT * FROM client_devices WHERE user_id=? AND hidden=0 AND revoked_at IS NULL AND expires_at>? ORDER BY created_at DESC", (subject, self.clock())).fetchall()
        return [{"id": row["id"], "deviceName": row["name"], "surface": row["surface"], "createdAt": timestamp(row["created_at"]),
                 "expiresAt": timestamp(row["expires_at"]), "lastUsedAt": timestamp(row["last_used_at"]) if row["last_used_at"] else None, "active": True} for row in rows]

    def revoke_ticket(self, ticket_id: str) -> bool:
        with self.transaction() as db:
            return bool(db.execute("UPDATE client_pairing_tickets SET revoked_at=? WHERE id=? AND consumed_at IS NULL AND revoked_at IS NULL", (self.clock(), ticket_id)).rowcount)

    def manifest(self, base_url: str) -> dict:
        from core.v8_link import normalize_remote_link_config, normalize_transport_kind, strip_api_suffix, is_stable_cloudflare_origin
        if self.config_reader is None:
            from core.system_base import get_system_base_config
            system_base = get_system_base_config()
        else:
            system_base = self.config_reader()
        configured_remote = system_base.get("remoteLink") or {}
        remote = normalize_remote_link_config(configured_remote)
        base = base_url.rstrip("/").removesuffix("/api")
        instance_id = self.instance()["instanceId"]
        gateway = configured_remote.get("phoneGateway") or {}
        active_id = remote.get("activeProfileId") or ""
        profiles, endpoints, warnings, seen = [], [], [], set()
        if not remote.get("enabled", True):
            warnings.append("remote_link_disabled")
        if gateway.get("enabled") is False:
            warnings.append("phone_gateway_disabled")
        def approved_url(value):
            value = strip_api_suffix(value)
            try:
                parsed = urlsplit(value)
                return value if parsed.scheme == "https" and parsed.hostname and parsed.hostname not in ("localhost", "127.0.0.1", "::1") and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment else ""
            except ValueError:
                return ""
        def append(identifier, kind, address, priority):
            if address and address not in seen:
                endpoints.append({"id": identifier, "kind": kind, "baseUrl": address, "priority": priority, "enabled": True,
                    "scope": "local" if kind == "lan" else "remote"})
                seen.add(address)
        # The request/ticket origin is already selected by this client. Never
        # derive a Phone endpoint by replacing an old Admin or internal port.
        append("current-engine", "manual_url", base, 0)
        public = approved_url(gateway.get("publicBaseUrl") or "")
        if remote.get("enabled", True) and gateway.get("enabled") is not False:
            append("phone-gateway", "manual_url", public, 10)
        if not public:
            warnings.append("phone_gateway_public_url_not_configured")
        with self.database() as db:
            paired_origins = {row[0] for row in db.execute("SELECT DISTINCT base_url FROM client_pairing_tickets WHERE consumed_at IS NOT NULL AND revoked_at IS NULL")}
        active_kind = "manual_url"
        for index, raw in enumerate(remote.get("transportProfiles") or []):
            kind, identifier = normalize_transport_kind(raw.get("kind")), str(raw.get("id") or "")
            if identifier == active_id:
                active_kind = kind
            phone = approved_url(raw.get("phoneBaseUrl") or "")
            legacy = approved_url(raw.get("adminBaseUrl") or "")
            if not phone and legacy in paired_origins:
                phone = legacy
            if phone and kind == "cloudflare_tunnel" and not is_stable_cloudflare_origin(phone):
                phone = ""
                warnings.append("cloudflare_stable_https_origin_required:" + identifier)
            item = {"id": identifier, "kind": kind, "label": str(raw.get("label") or identifier), "enabled": raw.get("enabled") is not False,
                "phoneBaseUrl": phone, "adminBaseUrl": phone, "migrationRequired": bool(raw.get("adminBaseUrl") and not phone)}
            if item["migrationRequired"]:
                warnings.append("phone_profile_endpoint_migration_required:" + identifier)
            profiles.append(item)
            if remote.get("enabled", True) and item["enabled"]:
                append(identifier, kind, phone, 20 + index)
        return {"ok": True, "kind": "v8_client_link_manifest", "version": "2", "serverId": instance_id,
                "instanceId": instance_id, "ownerMode": "single_owner", "clientGateway": "engine", "transportKind": active_kind, "activeProfileId": active_id,
                "admin": {"baseUrl": base, "apiBaseUrl": base + "/api"},
                "phoneGateway": {"enabled": gateway.get("enabled", True), "port": int(gateway.get("port") or 9532), "publicBaseUrl": public},
                "endpoints": endpoints, "profiles": profiles, "capabilities": {"adminProxy": False, "pairing": True,
                    "publicRegistration": False, "phoneUpload": True, "artifactPreview": True, "runtimeEvents": True, "networkSupervisorPeers": True},
                "meshProviders": [{"id": row.get("id"), "kind": row.get("kind"), "enabled": row.get("enabled"), "mode": row.get("mode"), "allowRouteMutation": False} for row in remote.get("meshProviders", [])],
                "diagnostics": {"readOnly": True, "warnings": warnings}, "warnings": warnings}
