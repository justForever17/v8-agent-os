"""Async subprocess management for Language Server instances over stdio."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.process_launch import windowless_subprocess_kwargs
from .lsp_protocol import (
    Diagnostic,
    LSPNotification,
    LSPRequest,
    LSPStatus,
    LSPStreamDecoder,
    encode_lsp_message,
)

logger = logging.getLogger("v8_agent_os.engineering.lsp")


def _canonical_uri(path_or_uri: str) -> str:
    """Safely convert any file path or URI string to a normalized canonical file:// URI."""
    raw = str(path_or_uri or "").strip()
    if not raw:
        return ""
    if raw.startswith("file://"):
        path_part = urllib.parse.unquote(raw[7:])
        if len(path_part) >= 3 and path_part[0] == "/" and path_part[2] == ":":
            path_part = path_part[1:]
        clean_path = path_part.replace("\\", "/").rstrip("/")
        if len(clean_path) >= 2 and clean_path[1] == ":":
            clean_path = clean_path[0].lower() + clean_path[1:]
        return f"file:///{clean_path.lstrip('/')}"

    clean = raw.replace("\\", "/").rstrip("/")
    if len(clean) >= 2 and clean[1] == ":":
        clean = clean[0].lower() + clean[1:]
    return f"file:///{clean.lstrip('/')}"


_to_file_uri = _canonical_uri


class LSPProcessInstance:
    """Manages an active Language Server child process communicating over Stdio."""

    def __init__(
        self,
        *,
        language: str,
        process: asyncio.subprocess.Process,
        workspace_root: str,
        on_status_change: Optional[Callable[[str, LSPStatus], None]] = None,
    ) -> None:
        self.language = language.lower()
        self.workspace_root = workspace_root
        self._process = process
        self._on_status_change = on_status_change
        self._status: LSPStatus = LSPStatus.INITIALIZING

        self._decoder = LSPStreamDecoder()
        self._next_request_id = 1
        self._pending_requests: Dict[int, asyncio.Future[Any]] = {}
        self._diagnostics_by_uri: Dict[str, List[Diagnostic]] = {}

        self._reader_task: Optional[asyncio.Task[None]] = None
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._closed = False

        self._start_background_readers()

    @property
    def status(self) -> LSPStatus:
        return self._status

    def set_status(self, new_status: LSPStatus) -> None:
        if self._status != new_status:
            self._status = new_status
            if callable(self._on_status_change):
                try:
                    self._on_status_change(self.language, new_status)
                except Exception as exc:
                    logger.warning("Error in LSP on_status_change callback: %s", exc)

    @classmethod
    async def spawn(
        cls,
        *,
        command: List[str],
        language: str,
        workspace_root: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        on_status_change: Optional[Callable[[str, LSPStatus], None]] = None,
    ) -> "LSPProcessInstance":
        """Asynchronously spawn a language server subprocess without opening console windows."""
        exec_cwd = str(cwd or workspace_root or os.getcwd())
        exec_env = dict(os.environ)
        if env:
            exec_env.update(env)

        kwargs: Dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": exec_cwd,
            "env": exec_env,
        }
        kwargs.update(windowless_subprocess_kwargs())

        logger.info(
            "Spawning LSP instance for language '%s': %s (cwd=%s)",
            language,
            command,
            exec_cwd,
        )

        try:
            process = await asyncio.create_subprocess_exec(*command, **kwargs)
        except Exception as exc:
            logger.error("Failed to spawn LSP process for %s: %s", language, exc)
            raise

        instance = cls(
            language=language,
            process=process,
            workspace_root=workspace_root,
            on_status_change=on_status_change,
        )
        return instance

    def _start_background_readers(self) -> None:
        loop = asyncio.get_event_loop()
        self._reader_task = loop.create_task(self._stdout_reader_loop())
        self._stderr_task = loop.create_task(self._stderr_reader_loop())

    async def _stdout_reader_loop(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            return

        try:
            while not self._closed and self._process.returncode is None:
                chunk = await stdout.read(65536)
                if not chunk:
                    break

                messages = self._decoder.feed(chunk)
                for msg in messages:
                    self._handle_incoming_message(msg)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("LSP stdout reader error for %s: %s", self.language, exc)
        finally:
            if not self._closed and self._process.returncode is not None:
                self.set_status(LSPStatus.ERROR)

    async def _stderr_reader_loop(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return

        try:
            while not self._closed and self._process.returncode is None:
                line = await stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.debug("[LSP %s stderr] %s", self.language, text)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _handle_incoming_message(self, message: Dict[str, Any]) -> None:
        # Check if it's a response to a pending request
        msg_id = message.get("id")
        if msg_id is not None and isinstance(msg_id, int):
            future = self._pending_requests.pop(msg_id, None)
            if future and not future.done():
                if "error" in message and message["error"] is not None:
                    future.set_exception(RuntimeError(f"LSP error: {message['error']}"))
                else:
                    future.set_result(message.get("result"))
            return

        # Check if it's a notification
        method = str(message.get("method") or "")
        params = message.get("params") or {}

        if method == "textDocument/publishDiagnostics":
            self._handle_publish_diagnostics(params)

    def _handle_publish_diagnostics(self, params: Dict[str, Any]) -> None:
        raw_uri = str(params.get("uri") or "")
        canon_uri = _canonical_uri(raw_uri)
        raw_diagnostics = list(params.get("diagnostics") or [])
        diagnostics = [
            Diagnostic.from_dict(d) for d in raw_diagnostics if isinstance(d, dict)
        ]
        self._diagnostics_by_uri[canon_uri] = diagnostics
        logger.debug(
            "LSP %s published %d diagnostics for %s",
            self.language,
            len(diagnostics),
            canon_uri,
        )

    def is_alive(self) -> bool:
        return self._process.returncode is None

    async def _write_and_drain(self, data: bytes) -> None:
        stdin = self._process.stdin
        if stdin is None:
            return
        res = stdin.write(data)
        if asyncio.iscoroutine(res):
            await res
        drain_res = stdin.drain()
        if asyncio.iscoroutine(drain_res):
            await drain_res

    async def send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 10.0,
    ) -> Any:
        """Send a JSON-RPC request and await response."""
        if not self.is_alive():
            raise RuntimeError(f"LSP process for {self.language} is not running")

        req_id = self._next_request_id
        self._next_request_id += 1

        request = LSPRequest(id=req_id, method=method, params=params)
        payload = request.to_dict()
        framed = encode_lsp_message(payload)

        loop = asyncio.get_event_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending_requests[req_id] = future

        await self._write_and_drain(framed)

        try:
            return await asyncio.wait_for(future, timeout=timeout_sec)
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            raise TimeoutError(
                f"LSP request '{method}' (id={req_id}) timed out after {timeout_sec}s"
            )

    async def send_notification(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send a JSON-RPC notification (fire-and-forget)."""
        if not self.is_alive():
            return

        notification = LSPNotification(method=method, params=params)
        payload = notification.to_dict()
        framed = encode_lsp_message(payload)

        await self._write_and_drain(framed)

    async def handshake(self, timeout_sec: float = 10.0) -> bool:
        """Execute LSP initialize and initialized handshake."""
        self.set_status(LSPStatus.INITIALIZING)

        root_uri = _to_file_uri(self.workspace_root) if self.workspace_root else None
        init_params: Dict[str, Any] = {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "workspace": {
                    "applyEdit": True,
                    "workspaceEdit": {"documentChanges": True},
                },
                "textDocument": {
                    "synchronization": {
                        "dynamicRegistration": True,
                        "willSave": False,
                        "willSaveWaitUntil": False,
                        "didSave": True,
                    },
                    "publishDiagnostics": {"relatedInformation": True},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "definition": {"linkSupport": True},
                    "references": {"dynamicRegistration": True},
                },
            },
            "initializationOptions": {},
        }

        try:
            result = await self.send_request("initialize", init_params, timeout_sec=timeout_sec)
            logger.info("LSP %s initialized successfully: %s", self.language, type(result))
            await self.send_notification("initialized", {})
            self.set_status(LSPStatus.ACTIVE)
            return True
        except Exception as exc:
            logger.error("LSP %s handshake failed: %s", self.language, exc)
            self.set_status(LSPStatus.ERROR)
            return False

    async def did_open(self, file_path: str, text: str) -> None:
        """Notify language server that a document has been opened."""
        uri = _to_file_uri(file_path)
        params = {
            "textDocument": {
                "uri": uri,
                "languageId": self.language,
                "version": 1,
                "text": text,
            }
        }
        await self.send_notification("textDocument/didOpen", params)

    async def did_change(self, file_path: str, text: str, version: int = 2) -> None:
        """Notify language server that a document has changed (full text sync)."""
        uri = _to_file_uri(file_path)
        params = {
            "textDocument": {
                "uri": uri,
                "version": version,
            },
            "contentChanges": [
                {"text": text}
            ],
        }
        await self.send_notification("textDocument/didChange", params)

    async def did_close(self, file_path: str) -> None:
        """Notify language server that a document has been closed."""
        uri = _to_file_uri(file_path)
        params = {
            "textDocument": {
                "uri": uri,
            }
        }
        await self.send_notification("textDocument/didClose", params)

    def get_diagnostics(self, file_path_or_uri: str) -> List[Diagnostic]:
        """Retrieve latest published diagnostics for a given file or URI."""
        uri = _to_file_uri(file_path_or_uri)
        return list(self._diagnostics_by_uri.get(uri) or [])

    async def shutdown(self, timeout_sec: float = 5.0) -> None:
        """Gracefully shut down the Language Server and terminate the process."""
        if self._closed:
            return
        self._closed = True

        for fut in list(self._pending_requests.values()):
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()

        try:
            if self.is_alive():
                try:
                    await self.send_request("shutdown", None, timeout_sec=timeout_sec)
                except Exception:
                    pass
                try:
                    await self.send_notification("exit", None)
                except Exception:
                    pass

                # Give it a moment to terminate on its own
                for _ in range(10):
                    if self._process.returncode is not None:
                        break
                    await asyncio.sleep(0.05)

                if self._process.returncode is None:
                    self._process.terminate()
                    await asyncio.sleep(0.1)
                    if self._process.returncode is None:
                        self._process.kill()
        except Exception as exc:
            logger.warning("Error during LSP %s shutdown: %s", self.language, exc)
        finally:
            if self._reader_task and not self._reader_task.done():
                self._reader_task.cancel()
            if self._stderr_task and not self._stderr_task.done():
                self._stderr_task.cancel()
            self.set_status(LSPStatus.IDLE)
