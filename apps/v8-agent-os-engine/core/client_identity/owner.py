"""The Engine's single writer for the existing users.json owner record."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4


class IdentityError(RuntimeError):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def public_user(user: dict) -> dict:
    return {"id": user["id"], "login": user["login"], "email": user.get("email") or user["login"], "sessionIdentifier": session_identifier(user),
            "name": user.get("name") or "", "image": user.get("image") or "", "role": user["role"],
            "appearance": user.get("appearance") or {}, "mustChangePassword": bool(user.get("mustChangePassword")),
            "createdAt": user.get("createdAt"), "updatedAt": user.get("updatedAt")}


def session_identifier(user: dict) -> str:
    return user.get("sessionIdentifier") or user.get("email") or user["login"]


class OwnerStore:
    def __init__(self, home: Path):
        self.home, self.path = home, home / "users.json"
        self.lock = threading.RLock()

    def payload(self) -> dict:
        if not self.path.exists():
            return {"users": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict) or not isinstance(value.get("users"), list):
                raise ValueError()
            ids, logins, owners = set(), set(), 0
            for user in value["users"]:
                if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not user["id"]:
                    raise ValueError()
                login = user.get("login") or user.get("email")
                if not isinstance(login, str) or not login.strip() or user.get("role") not in ("ADMIN", "USER"):
                    raise ValueError()
                if user["id"] in ids or login.lower() in logins:
                    raise ValueError()
                for key in ("password", "image", "email", "createdAt", "updatedAt", "sessionIdentifier"):
                    if key in user and not isinstance(user[key], str):
                        raise ValueError()
                if "sessionIdentifier" in user and not user["sessionIdentifier"].strip():
                    raise ValueError()
                if "name" in user and user["name"] is not None and not isinstance(user["name"], str):
                    raise ValueError()
                if "appearance" in user and not isinstance(user["appearance"], dict):
                    raise ValueError()
                ids.add(user["id"]); logins.add(login.lower())
                user["login"] = login
                owners += user["role"] == "ADMIN"
            if owners > 1:
                raise ValueError()
            return value
        except (OSError, ValueError, AssertionError, TypeError):
            raise IdentityError("owner_storage_unavailable", 503) from None

    def owner(self, *, required: bool = True) -> dict | None:
        user = next((row for row in self.payload()["users"] if row["role"] == "ADMIN"), None)
        if user is None and required:
            raise IdentityError("owner_not_initialized", 409)
        return user

    def bootstrap(self, *, login: str = "owner", name: str = "", now: float, password_hash: str = "", local_pending: bool = False) -> dict:
        with self.lock:
            payload = self.payload()
            existing = next((row for row in payload["users"] if row["role"] == "ADMIN"), None)
            if existing:
                if not existing.get("localBootstrapPending") or existing.get("password") or not password_hash:
                    raise IdentityError("owner_already_initialized", 409)
                existing["sessionIdentifier"] = session_identifier(existing)
                existing.update({"login": str(login).strip() or "owner", "name": str(name).strip()[:200], "password": password_hash,
                    "localBootstrapPending": False, "updatedAt": timestamp(now)})
                atomic_json(self.path, payload)
                return existing
            login = str(login).strip()
            if not login or len(login) > 128 or any(row["login"].lower() == login.lower() for row in payload["users"]):
                raise IdentityError("owner_login_invalid")
            owner = {"id": str(uuid4()), "login": login, "sessionIdentifier": login, "name": str(name).strip()[:200], "role": "ADMIN",
                     "createdAt": timestamp(now), "updatedAt": timestamp(now), "appearance": {}}
            if password_hash:
                owner["password"] = password_hash
            if local_pending:
                owner["localBootstrapPending"] = True
            payload["users"].append(owner)
            atomic_json(self.path, payload)
            return owner

    def update(self, subject: str, patch: dict, *, now: float) -> dict:
        # Neither a client-provided subject nor role can change owner identity.
        if set(patch) - {"name", "image", "email", "appearance"}:
            raise IdentityError("profile_field_not_configurable")
        with self.lock:
            payload = self.payload()
            target = next((row for row in payload["users"] if row["id"] == subject and row["role"] == "ADMIN"), None)
            if target is None:
                raise IdentityError("owner_unavailable", 401)
            target["sessionIdentifier"] = session_identifier(target)
            for key in ("name", "image", "email"):
                if key in patch:
                    if not isinstance(patch[key], str) or len(patch[key]) > (2048 if key == "image" else 200):
                        raise IdentityError("profile_field_invalid")
                    target[key] = patch[key].strip()
            if "appearance" in patch:
                self._appearance(target, patch["appearance"])
            target["updatedAt"] = timestamp(now)
            atomic_json(self.path, payload)
            return target

    def change_password(self, value: str, *, old_password: str = "", force: bool = False, now: float) -> dict:
        import bcrypt
        encoded = value.encode()
        if len(value) < 6 or len(encoded) > 72:
            raise IdentityError("password_length_invalid")
        with self.lock:
            payload = self.payload()
            user = next((row for row in payload["users"] if row["role"] == "ADMIN"), None)
            if user is None:
                raise IdentityError("owner_not_initialized", 409)
            if user.get("password") and not force:
                try:
                    verified = bool(old_password) and bcrypt.checkpw(old_password.encode()[:72], user["password"].encode())
                except (TypeError, ValueError):
                    verified = False
                if not verified:
                    raise IdentityError("current_password_invalid")
            user["password"] = bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=10)).decode()
            user["mustChangePassword"] = False
            user["updatedAt"] = timestamp(now)
            atomic_json(self.path, payload)
            return user

    def _appearance(self, user: dict, patch: dict) -> None:
        if not isinstance(patch, dict):
            raise IdentityError("profile_appearance_invalid")
        import re
        appearance = {**(user.get("appearance") or {}), **patch}
        for field in ("lightBackgroundMedia", "lightBackgroundImage"):
            if field in patch:
                value = patch[field]
                if not isinstance(value, str) or (value and not re.fullmatch(r"/user-assets/background/[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.(webp|mp4)", value, re.IGNORECASE)):
                    raise IdentityError("BACKGROUND_MEDIA_UNAVAILABLE")
        if "lightBackgroundMediaType" in patch and patch["lightBackgroundMediaType"] not in ("image", "video"):
            raise IdentityError("BACKGROUND_MEDIA_UNAVAILABLE")
        if "lightBackgroundEnabled" in patch and not isinstance(patch["lightBackgroundEnabled"], bool):
            raise IdentityError("profile_appearance_invalid")
        if "webBackground" in patch:
            playlist = patch["webBackground"]
            current = (user.get("appearance") or {}).get("webBackground") or {}
            if not isinstance(playlist, dict) or playlist.get("revision", 0) != current.get("revision", 0):
                raise IdentityError("BACKGROUND_REVISION_CONFLICT", 409)
            items = playlist.get("items", [])
            if not isinstance(items, list) or len(items) > 50:
                raise IdentityError("BACKGROUND_PLAYLIST_INVALID")
            existing = {item.get("media") for item in current.get("items", []) if isinstance(item, dict)}
            directory = self.home / "assets" / "user-media" / "background"
            ids = set()
            normalized_items = []
            def duration(value):
                if type(value) not in (int, float) or not 1000 <= value <= 3600000:
                    raise IdentityError("BACKGROUND_DURATION_INVALID")
                return int(value + .5)
            for item in items:
                if not isinstance(item, dict) or item.get("kind") not in ("image", "video"):
                    raise IdentityError("BACKGROUND_PLAYLIST_INVALID")
                identifier = item.get("id")
                if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier) or identifier in ids:
                    raise IdentityError("BACKGROUND_PLAYLIST_INVALID")
                ids.add(identifier)
                position = item.get("position") or "50% 50%"
                if not isinstance(position, str) or not re.fullmatch(r"(?:100|[0-9]{1,2})% (?:100|[0-9]{1,2})%", position):
                    raise IdentityError("BACKGROUND_POSITION_INVALID")
                media = str(item.get("media") or "")
                if not re.fullmatch(r"/user-assets/background/[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.(webp|mp4)", media):
                    raise IdentityError("BACKGROUND_MEDIA_UNAVAILABLE")
                path = directory / media.rsplit("/", 1)[-1]
                if not path.is_file() or (item["kind"] == "video") != media.endswith(".mp4"):
                    raise IdentityError("BACKGROUND_MEDIA_UNAVAILABLE")
                if media not in existing:
                    try:
                        receipt = json.loads(path.with_name(".receipt-" + path.name + ".json").read_text(encoding="utf-8"))
                    except (ValueError, OSError):
                        raise IdentityError("BACKGROUND_MEDIA_UNAVAILABLE") from None
                    if receipt.get("userId") != user["id"] or receipt.get("kind") != item["kind"]:
                        raise IdentityError("BACKGROUND_MEDIA_UNAVAILABLE")
                normalized = {"id": identifier, "media": media, "kind": item["kind"], "fit": "contain" if item.get("fit") == "contain" else "cover", "position": position}
                if "imageDurationMs" in item:
                    normalized["imageDurationMs"] = duration(item["imageDurationMs"])
                normalized_items.append(normalized)
            appearance["webBackground"] = {"revision": current.get("revision", 0) + 1, "enabled": playlist.get("enabled") is True,
                "items": normalized_items, "imageDurationMs": duration(playlist.get("imageDurationMs", 30000)), "order": "shuffle" if playlist.get("order") == "shuffle" else "ordered"}
        user["appearance"] = appearance
