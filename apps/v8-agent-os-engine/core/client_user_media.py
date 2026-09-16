"""User appearance files owned by Engine, outside workspace artifacts."""
from __future__ import annotations

import re
import time
from pathlib import Path

from core.client_identity.owner import atomic_json

_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.(?:webp|mp4)")


def media_path(home: Path, kind: str, filename: str) -> Path:
    if kind not in {"avatar", "background"} or not _FILE.fullmatch(filename) or (kind == "avatar" and not filename.endswith(".webp")):
        raise ValueError("invalid_user_media_path")
    directory = home / "assets" / "user-media" / kind
    target = directory / filename
    if target.is_symlink() or not target.resolve().is_relative_to(directory.resolve()):
        raise ValueError("invalid_user_media_path")
    return target


def background_references(owners) -> set[str]:
    references = set()
    for user in owners.payload()["users"]:
        appearance = user.get("appearance") or {}
        for key in ("lightBackgroundMedia", "lightBackgroundImage"):
            if appearance.get(key):
                references.add(appearance[key])
        for item in (appearance.get("webBackground") or {}).get("items", []):
            if isinstance(item, dict) and item.get("media"):
                references.add(item["media"])
    return references


def remove_unreferenced(owners, url: str, *, kind: str = "background") -> None:
    prefix = f"/user-assets/{kind}/"
    if not url.startswith(prefix):
        return
    with owners.lock:
        if kind == "background" and url in background_references(owners):
            return
        if kind == "avatar" and any(user.get("image") == url for user in owners.payload()["users"]):
            return
        try:
            target = media_path(owners.home, kind, url[len(prefix):])
            target.unlink(missing_ok=True)
            if kind == "background" and target.suffix == ".webp":
                target.with_suffix(".thumb.webp").unlink(missing_ok=True)
        except (OSError, ValueError):
            # Cleanup failure does not invalidate a durably committed profile.
            return


def background_receipt(owners, subject: str, url: str, kind: str, *, now: float | None = None) -> dict:
    now_ms = int((time.time() if now is None else now) * 1000)
    target = media_path(owners.home, "background", url.rsplit("/", 1)[-1])
    receipt = {"userId": subject, "media": url, "kind": kind, "createdAt": now_ms, "expiresAt": now_ms + 86400000}
    atomic_json(target.parent / f".receipt-{target.name}.json", receipt)
    return {key: receipt[key] for key in ("media", "kind", "expiresAt")}
