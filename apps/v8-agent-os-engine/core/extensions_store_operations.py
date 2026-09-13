"""Durable, secret-free operation observations for ordinary Extensions installs.

This owns no plugin grants or runtime queue. A process restart never replays a
side effect: unfinished work is reconciled as interrupted and can be retried.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from core.interprocess_lock import interprocess_file_lock
from core.v8_agent_os_paths import V8_AGENT_OS_HOME

_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="extensions-install")
_ADMISSION = threading.BoundedSemaphore(9)  # Three workers plus at most six waiting inputs.
_INSTANCE = uuid4().hex
_CURRENT = contextvars.ContextVar("extension_operation", default="")
_LOCK = threading.RLock()
_TERMINAL = {"completed", "failed", "cancelled", "interrupted", "awaiting_input"}


class InstallCancelled(ValueError):
    pass


class InstallBusy(ValueError):
    pass


def _root() -> Path:
    root = V8_AGENT_OS_HOME / "extensions" / "operations"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(operation_id: str) -> Path:
    if len(operation_id) != 32 or any(c not in "0123456789abcdef" for c in operation_id):
        raise ValueError("无效安装操作。")
    return _root() / f"{operation_id}.json"


def _save(record: dict[str, Any]) -> None:
    path = _path(record["operationId"])
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    record["updatedAt"] = time.time()
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def get_operation(operation_id: str) -> dict[str, Any]:
    with _LOCK:
        record = json.loads(_path(operation_id).read_text(encoding="utf-8"))
        if record.get("owner") != _INSTANCE and record["status"] not in _TERMINAL:
            # The Engine runs one install owner; do not silently repeat committed work.
            record.update(status="interrupted", phase="interrupted", canCancel=False,
                          message="安装进程已重启。请先检查已安装项，再继续此操作。")
            _save(record)
        return {k: v for k, v in record.items() if k != "owner"}


def list_operations() -> dict[str, Any]:
    paths = sorted(_root().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {"operations": [get_operation(p.stem) for p in paths[:100]]}


def checkpoint(phase: str, *, can_cancel: bool = True) -> None:
    operation_id = _CURRENT.get()
    if not operation_id:
        return
    with _LOCK:
        record = json.loads(_path(operation_id).read_text(encoding="utf-8"))
        if record.get("cancelRequested"):
            raise InstallCancelled("安装已取消，尚未提交。")
        record.update(phase=phase, canCancel=can_cancel)
        _save(record)


def cancel_operation(operation_id: str) -> dict[str, Any]:
    with _LOCK:
        record = get_operation(operation_id)
        if record["status"] not in _TERMINAL and record.get("canCancel"):
            record.update(cancelRequested=True, owner=_INSTANCE, message="正在取消，等待下载退出。")
            _save(record)
        return get_operation(operation_id)


def start_operation(kind: str, payload: dict[str, Any], installer: Callable) -> dict[str, Any]:
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
        raise ValueError("安装参数超过 64 KiB 限制。")
    provider = str(payload.get("provider") or "international")
    item_id = str(payload.get("id") or payload.get("skillId") or "")
    source = str(payload.get("source") or "")
    if kind == "skills":
        skill_id = str(payload.get("skillId") or "")
        if provider == "modelscope":
            source = skill_id
            item_id = skill_id
        else:
            item_id = f"{source}@{skill_id}"
    else:
        source = ""
    if not item_id or len(item_id) > 240 or not re.fullmatch(r"[A-Za-z0-9_./@-]+", item_id) or ".." in item_id:
        raise ValueError("安装来源身份无效。")
    target = f"{provider}:{kind}:{source}:{item_id}"
    operation_id = hashlib.sha256(target.encode()).hexdigest()[:32]
    # One stable operation per target, including double tabs and lost HTTP replies.
    with _LOCK, interprocess_file_lock(_root() / "operations.lock", timeout_seconds=30):
        path = _path(operation_id)
        if path.exists():
            existing = get_operation(operation_id)
            if existing["status"] not in _TERMINAL or not payload.get("retry"):
                return existing
        record = {"operationId": operation_id, "target": target, "kind": kind,
                  "provider": provider, "itemId": item_id, "source": source,
                  "skillId": str(payload.get("skillId") or ""),
                  "candidateId": str(payload.get("candidateId") or ""),
                  "status": "running", "phase": "queued", "canCancel": kind == "skills",
                  "owner": _INSTANCE, "createdAt": time.time()}
        if kind == "mcp" and payload.get("waitForInput"):
            record.update(status="awaiting_input", phase="awaiting_input", canCancel=False,
                          message="请在来源页面完成服务配置，再回到此操作继续连接。")
            _save(record)
            return get_operation(operation_id)
        if not _ADMISSION.acquire(blocking=False):
            raise InstallBusy("已有 9 个安装正在处理或排队，请等待现有操作完成后重试。")
        try:
            _save(record)
        except Exception:
            _ADMISSION.release()
            raise

        def run() -> None:
            token = _CURRENT.set(operation_id)
            try:
                checkpoint("resolving", can_cancel=kind == "skills")
                result = installer(payload)
                final = dict(status="completed", phase="completed", result=result)
            except InstallCancelled:
                final = dict(status="cancelled", phase="cancelled", message="安装已取消。")
            except Exception as exc:
                # Exception chains can contain credentials, endpoint paths and argv.
                code = getattr(exc, "code", type(exc).__name__)
                final = dict(status="failed", phase="failed", errorCode=code,
                             message=getattr(exc, "message", "安装未完成，请检查来源、依赖和配置后重试。"))
                if code == "select_skill":
                    final["choices"] = getattr(exc, "details", {}).get("skills", [])
            finally:
                _CURRENT.reset(token)
            with _LOCK:
                current = json.loads(path.read_text(encoding="utf-8"))
                current.update(**final, canCancel=False)
                _save(current)

        try:
            future = _POOL.submit(run)
            future.add_done_callback(lambda _: _ADMISSION.release())
        except Exception:
            _ADMISSION.release()
            record.update(status="failed", phase="failed", canCancel=False, message="安装执行器未启动，请重试。")
            _save(record)
            raise
        return get_operation(operation_id)
