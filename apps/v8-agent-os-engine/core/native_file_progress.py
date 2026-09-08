"""Read a successful native write's version without treating prose as progress."""
from __future__ import annotations

import json
import ntpath
import posixpath
import re
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


def completed_write_version(message: Any) -> str:
    if (not isinstance(message, ToolMessage) or message.status == "error"
            or message.name != "write_native_file"):
        return ""
    text = str(message.content or "").strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        version = str(payload.get("contentVersion") or "") if payload.get("ok") is True else ""
        return version if re.fullmatch(r"sha256:[a-f0-9]{64}", version) else ""
    if not text.startswith(("Successfully ", "write native file result\n")):
        return ""
    if re.search(r"(?im)^Status:\s*(failed|blocked|error|cancelled)\b", text):
        return ""
    version = re.search(r"(?m)^Content version: (sha256:[a-f0-9]{64})\b", text)
    return version[1] if version else ""


class NativeFileProgress:
    """Scope successful write progress to an actor and the read's file/directory.

    This is loop accounting, not a permission or read receipt. Paths are only
    normalized lexically; no filesystem scan or symlink resolution is needed.
    """

    def __init__(self, *, agent_id: str, workspace_path: str = "") -> None:
        self.agent_id = agent_id
        self.workspace_path = str(workspace_path or "")
        self.calls: dict[str, tuple[str, str]] = {}
        self.seen_results: set[str] = set()
        self.versions: dict[str, str] = {}
        self.revisions: dict[str, int] = {}
        self.revision = 0

    def _path(self, value: Any) -> str:
        path = str(value or "").strip().replace("\\", "/")
        if not path:
            return ""
        windows = bool(re.match(r"^(?:[a-zA-Z]:|//)", path) or re.match(r"^(?:[a-zA-Z]:|//)", self.workspace_path))
        paths = ntpath if windows else posixpath
        if self.workspace_path and not paths.isabs(path):
            path = paths.join(self.workspace_path, path)
        path = paths.normpath(path)
        return (paths.normcase(path) if windows else path).replace("\\", "/")

    @staticmethod
    def _args(call: dict[str, Any]) -> dict[str, Any]:
        args = call.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                return {}
        return args if isinstance(args, dict) else {}

    def observe(self, message: Any, calls: list[dict[str, Any]]) -> None:
        metadata = getattr(message, "additional_kwargs", {}) or {}
        owner = metadata.get("v8_owner_agent_id") or metadata.get("v8_owner_subagent_id")
        if owner and owner != self.agent_id:
            return
        if isinstance(message, AIMessage):
            for call in calls:
                call_id = str(call.get("id") or "").strip()
                args = self._args(call)
                path = self._path(args.get("path"))
                if call.get("name") == "write_native_file" and call_id and path:
                    self.calls.setdefault(call_id, (path, str(args.get("expected_version") or "")))
        elif isinstance(message, ToolMessage):
            call_id = message.tool_call_id
            if call_id not in self.calls or call_id in self.seen_results:
                return
            self.seen_results.add(call_id)
            version = completed_write_version(message)
            path, expected = self.calls[call_id]
            if version:
                previous = self.versions.get(path, expected)
                self.versions[path] = version
                if previous != version:
                    self.revision += 1
                    self.revisions[path] = self.revision

    def read_epoch(self, call: dict[str, Any]) -> int:
        name = call.get("name")
        target = self._path(self._args(call).get("path"))
        if name not in {"read_native_file", "grep_search"} or not target:
            return 0
        def matches(path: str) -> bool:
            if path == target:
                return True
            if name != "grep_search":
                return False
            if target == ".":
                return not path.startswith(("../", "/")) and not ntpath.isabs(path)
            return path.startswith(target.rstrip("/") + "/")
        return max((version for path, version in self.revisions.items() if matches(path)), default=0)
