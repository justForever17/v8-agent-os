from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


MANIFEST_NAMES = (
    "AGENTS.md",
    ".agents/rules/AGENTS.md",
    "package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "poetry.lock",
    "Cargo.toml",
    "Cargo.lock",
    "tsconfig.json",
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _hash_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""


def _find_git_root(start: Path) -> Path | None:
    current = start.resolve(strict=False)
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _read_git_head(git_root: Path | None) -> str:
    if git_root is None:
        return ""
    head_path = git_root / ".git" / "HEAD"
    try:
        head = head_path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        try:
            ref_value = (git_root / ".git" / ref).read_text(encoding="utf-8", errors="ignore").strip()
            return f"{ref}:{ref_value}"
        except Exception:
            return head
    return head


def build_soft_repo_signature(workspace_path: str | None) -> dict[str, Any]:
    raw_path = str(workspace_path or "").strip()
    if not raw_path:
        return {
            "signaturePolicy": "soft_v1",
            "workspaceRoot": "",
            "agentsHash": "",
            "repoSignature": "",
            "manifestHashes": {},
            "gitHead": "",
        }
    root = Path(raw_path).expanduser().resolve(strict=False)
    git_root = _find_git_root(root)
    manifests: dict[str, str] = {}
    for name in MANIFEST_NAMES:
        target = root / name
        if target.is_file():
            manifests[name] = _hash_file(target)
    agents_hash = manifests.get(".agents/rules/AGENTS.md") or manifests.get("AGENTS.md") or ""
    payload = {
        "policy": "soft_v1",
        "workspaceRoot": str(root),
        "gitRoot": str(git_root or ""),
        "gitHead": _read_git_head(git_root),
        "manifestHashes": manifests,
    }
    return {
        "signaturePolicy": "soft_v1",
        "workspaceRoot": str(root),
        "agentsHash": agents_hash,
        "repoSignature": _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        "manifestHashes": manifests,
        "gitHead": payload["gitHead"],
    }

