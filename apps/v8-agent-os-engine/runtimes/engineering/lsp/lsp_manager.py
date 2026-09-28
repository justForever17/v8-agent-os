"""LSP lifecycle manager, LRU process recycling, and file event hooks."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .lsp_diff import LSPDiffResult, compute_diagnostic_diff
from .lsp_probe_runner import LSPProbeRunner, LSPStatusRegistry
from .lsp_process import LSPProcessInstance
from .lsp_protocol import LSPStatus

logger = logging.getLogger("v8_agent_os.engineering.lsp")

# File extensions to canonical language identifiers
LANGUAGE_EXTENSIONS: Dict[str, str] = {
    ".ts": "ts",
    ".tsx": "ts",
    ".js": "ts",
    ".jsx": "ts",
    ".mjs": "ts",
    ".cjs": "ts",
    ".py": "py",
    ".pyi": "py",
    ".rs": "rust",
    ".go": "go",
}


class LSPManager:
    """Manages LSP process lifecycles across workspaces with LRU idle cleanup."""

    _global_instance: Optional["LSPManager"] = None

    def __init__(self, *, probe_runner: Optional[LSPProbeRunner] = None) -> None:
        self.probe_runner = probe_runner or LSPProbeRunner()
        self._instances: Dict[Tuple[str, str], LSPProcessInstance] = {}
        self._last_used: Dict[Tuple[str, str], float] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "LSPManager":
        """Get or create singleton LSPManager instance."""
        if cls._global_instance is None:
            cls._global_instance = cls()
        return cls._global_instance

    @staticmethod
    def detect_language(file_path: str) -> Optional[str]:
        """Infer canonical language identifier from file extension."""
        suffix = Path(file_path).suffix.lower()
        lang = LANGUAGE_EXTENSIONS.get(suffix)
        if lang:
            return LSPStatusRegistry.canonical_language(lang)
        return None

    def _normalize_key(self, lang: str, workspace_root: str) -> Tuple[str, str]:
        canonical_lang = LSPStatusRegistry.canonical_language(lang)
        clean_root = str(Path(workspace_root).resolve()).replace("\\", "/").rstrip("/").lower()
        return (canonical_lang, clean_root)

    async def get_or_spawn(
        self, language: str, workspace_root: str, *, auto_handshake: bool = True
    ) -> Optional[LSPProcessInstance]:
        """Obtain an active Language Server instance, spawning it on-demand if necessary."""
        key = self._normalize_key(language, workspace_root)
        canonical_lang, clean_root = key

        async with self._lock:
            existing = self._instances.get(key)
            if existing is not None:
                if existing.is_alive():
                    self._last_used[key] = time.time()
                    return existing
                else:
                    self._instances.pop(key, None)
                    self._last_used.pop(key, None)

            # Probe whether toolchain is available
            resolution = self.probe_runner.resolve_launch_command(
                canonical_lang, workspace_root=clean_root
            )
            if not resolution:
                self.probe_runner.registry.set_status(canonical_lang, LSPStatus.MISSING)
                return None

            cmd, kind, bin_path = resolution
            env = None
            cwd = clean_root

            def on_status_change(l: str, s: LSPStatus) -> None:
                self.probe_runner.registry.set_status(l, s)

            try:
                instance = await LSPProcessInstance.spawn(
                    language=canonical_lang,
                    command=cmd,
                    workspace_root=clean_root,
                    cwd=cwd,
                    env=env,
                    on_status_change=on_status_change,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to spawn language server for %s: %s", canonical_lang, exc
                )
                self.probe_runner.registry.set_status(canonical_lang, LSPStatus.ERROR)
                return None

            if auto_handshake:
                ok = await instance.handshake(timeout_sec=5.0)
                if not ok:
                    await instance.shutdown(timeout_sec=1.0)
                    self.probe_runner.registry.set_status(canonical_lang, LSPStatus.ERROR)
                    return None

            self._instances[key] = instance
            self._last_used[key] = time.time()
            return instance

    async def cleanup_idle_instances(self, idle_timeout_sec: float = 300.0) -> int:
        """Shut down and release Language Servers that have been idle past timeout."""
        now = time.time()
        to_remove: List[Tuple[str, str]] = []

        async with self._lock:
            for key, last_time in list(self._last_used.items()):
                if now - last_time > idle_timeout_sec:
                    to_remove.append(key)

            for key in to_remove:
                instance = self._instances.pop(key, None)
                self._last_used.pop(key, None)
                if instance is not None:
                    logger.info("Evicting idle LSP instance for %s (idle > %ds)", key, idle_timeout_sec)
                    try:
                        await instance.shutdown(timeout_sec=2.0)
                    except Exception as exc:
                        logger.warning("Error shutting down idle LSP %s: %s", key, exc)

        return len(to_remove)

    async def on_file_saved(
        self,
        file_path: str,
        content: str,
        *,
        workspace_root: Optional[str] = None,
        wait_ms: int = 150,
    ) -> Optional[LSPDiffResult]:
        """Notify language server of file save and calculate incremental diagnostic diff."""
        lang = self.detect_language(file_path)
        if not lang:
            return None

        resolved_root = workspace_root or str(Path(file_path).parent)
        instance = await self.get_or_spawn(lang, resolved_root)
        if instance is None:
            return None

        # Capture diagnostics before notification
        before_diags = instance.get_diagnostics(file_path)

        # Notify LSP
        await instance.did_save(file_path, content)

        # Brief pause to allow background stdio reader to ingest publishDiagnostics
        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000.0)

        # Capture diagnostics after notification
        after_diags = instance.get_diagnostics(file_path)

        diff = compute_diagnostic_diff(
            before=before_diags,
            after=after_diags,
            language=lang,
            file_path=file_path,
            file_content=content,
        )
        return diff

    def on_file_saved_sync(
        self,
        file_path: str,
        content: str,
        *,
        workspace_root: Optional[str] = None,
        wait_ms: int = 150,
        timeout: float = 3.0,
    ) -> Optional[LSPDiffResult]:
        """Synchronous wrapper for on_file_saved to integrate into synchronous tool pipelines."""
        coro = self.on_file_saved(
            file_path, content, workspace_root=workspace_root, wait_ms=wait_ms
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None:
            return asyncio.run(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result(timeout=timeout)

    async def shutdown_all(self, timeout_sec: float = 5.0) -> None:
        """Shut down all running Language Server instances cleanly."""
        async with self._lock:
            for key, instance in list(self._instances.items()):
                try:
                    await instance.shutdown(timeout_sec=timeout_sec)
                except Exception as exc:
                    logger.warning("Error shutting down LSP %s: %s", key, exc)
            self._instances.clear()
            self._last_used.clear()
