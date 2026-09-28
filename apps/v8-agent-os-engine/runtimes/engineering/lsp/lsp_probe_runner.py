"""LSP local environment probe, toolchain discovery, and status registry."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .lsp_protocol import LSPStatus

logger = logging.getLogger("v8_agent_os.engineering.lsp")


def _get_v8os_cache_root() -> Path:
    """Return ~/.v8-agent-os/cache/lsp."""
    home = Path(os.path.expanduser("~"))
    return home / ".v8-agent-os" / "cache" / "lsp"


class LSPStatusRegistry:
    """In-memory registry maintaining the live status of all language servers."""

    def __init__(self) -> None:
        self._statuses: Dict[str, LSPStatus] = {
            "ts": LSPStatus.UNINSTALLED,
            "py": LSPStatus.UNINSTALLED,
            "rust": LSPStatus.UNINSTALLED,
            "go": LSPStatus.UNINSTALLED,
        }
        self._subscribers: List[Callable[[str, LSPStatus], None]] = []

    def get_status(self, lang: str) -> LSPStatus:
        canonical = self.canonical_language(lang)
        return self._statuses.get(canonical, LSPStatus.UNINSTALLED)

    def set_status(self, lang: str, status: LSPStatus) -> None:
        canonical = self.canonical_language(lang)
        old_status = self._statuses.get(canonical)
        if old_status != status:
            self._statuses[canonical] = status
            logger.info("LSP status for '%s' transitioned: %s -> %s", canonical, old_status, status)
            for sub in list(self._subscribers):
                try:
                    sub(canonical, status)
                except Exception as exc:
                    logger.warning("Error in LSP status subscriber: %s", exc)

    def subscribe(self, callback: Callable[[str, LSPStatus], None]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[str, LSPStatus], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def get_summary(self) -> Dict[str, str]:
        """Return dict of canonical lang -> status string for UI/prompt projection."""
        return {lang: status.value for lang, status in sorted(self._statuses.items())}

    def get_indicator(self) -> str:
        """Return single-line compact traffic light indicator (e.g. '🟢 ts  ⚪ py  ⚪ rust  ⚪ go')."""
        symbols = {
            LSPStatus.ACTIVE: "🟢",
            LSPStatus.INITIALIZING: "🟡",
            LSPStatus.ERROR: "🔴",
            LSPStatus.MISSING: "⚪",
            LSPStatus.UNINSTALLED: "⚪",
            LSPStatus.IDLE: "⚪",
        }
        parts = []
        for lang in ("ts", "py", "rust", "go"):
            status = self._statuses.get(lang, LSPStatus.UNINSTALLED)
            sym = symbols.get(status, "⚪")
            parts.append(f"{sym} {lang}")
        return "  ".join(parts)

    @staticmethod
    def canonical_language(lang: str) -> str:
        normalized = str(lang or "").strip().lower()
        if normalized in {"ts", "typescript", "js", "javascript", "tsx", "jsx"}:
            return "ts"
        if normalized in {"py", "python"}:
            return "py"
        if normalized in {"rs", "rust"}:
            return "rust"
        if normalized in {"go", "golang"}:
            return "go"
        return normalized


class LSPProbeRunner:
    """Discovers available language server binaries across workspace, cache, and host PATH."""

    def __init__(self) -> None:
        self.registry = LSPStatusRegistry()

    def canonical_language(self, lang: str) -> str:
        return self.registry.canonical_language(lang)

    @classmethod
    def get_install_guidance(cls, lang: str) -> str:
        """Return actionable single-command installation guidance for missing language server."""
        canonical = LSPStatusRegistry.canonical_language(lang)
        if canonical == "ts":
            return (
                "[LSP Guidance] TypeScript Language Server missing. Run:\n"
                "npm install --prefix ~/.v8-agent-os/cache/lsp typescript typescript-language-server\n"
                "(Retry with --registry=https://registry.npmmirror.com if network fails)"
            )
        elif canonical == "py":
            return (
                "[LSP Guidance] Python Language Server (pyright) missing. Run:\n"
                "pip install --target ~/.v8-agent-os/cache/lsp/py pyright\n"
                "(Retry with -i https://pypi.tuna.tsinghua.edu.cn/simple if network fails)"
            )
        elif canonical == "rust":
            return (
                "[LSP Guidance] Rust Language Server (rust-analyzer) missing. Run:\n"
                "rustup component add rust-analyzer"
            )
        elif canonical == "go":
            return (
                "[LSP Guidance] Go Language Server (gopls) missing. Run:\n"
                "go install golang.org/x/tools/gopls@latest\n"
                "(Set GOPROXY=https://goproxy.cn,direct if network fails)"
            )
        return ""

    def probe_workspace(self, workspace_root: str) -> Dict[str, Dict[str, Any]]:
        """Probe toolchain readiness for supported languages in a given workspace root."""
        results: Dict[str, Dict[str, Any]] = {}
        for lang in ("ts", "py", "rust", "go"):
            resolution = self.resolve_launch_command(lang, workspace_root=workspace_root)
            if resolution:
                cmd, kind, bin_path = resolution
                # If currently uninstalled, transition to idle (ready to launch)
                if self.registry.get_status(lang) == LSPStatus.UNINSTALLED:
                    self.registry.set_status(lang, LSPStatus.IDLE)
                results[lang] = {
                    "available": True,
                    "kind": kind,
                    "binaryPath": bin_path,
                    "status": self.registry.get_status(lang).value,
                }
            else:
                if self.registry.get_status(lang) in (LSPStatus.IDLE, LSPStatus.ACTIVE):
                    pass
                else:
                    self.registry.set_status(lang, LSPStatus.UNINSTALLED)
                results[lang] = {
                    "available": False,
                    "kind": "missing",
                    "binaryPath": None,
                    "status": self.registry.get_status(lang).value,
                }
        return results

    def resolve_launch_command(
        self,
        lang: str,
        *,
        workspace_root: Optional[str] = None,
    ) -> Optional[Tuple[List[str], str, str]]:
        """Resolve executable command for a language.

        Returns:
            Tuple of (command_args, discovery_kind, binary_path) or None.
            discovery_kind can be: "local_workspace", "cache", "host_path", "ephemeral".
        """
        canonical = self.canonical_language(lang)

        if canonical == "ts":
            return self._resolve_typescript(workspace_root)
        elif canonical == "py":
            return self._resolve_python(workspace_root)
        elif canonical == "rust":
            return self._resolve_rust()
        elif canonical == "go":
            return self._resolve_go()

        return None

    def _resolve_typescript(
        self,
        workspace_root: Optional[str],
    ) -> Optional[Tuple[List[str], str, str]]:
        is_win = sys.platform == "win32"
        bin_name = "typescript-language-server.cmd" if is_win else "typescript-language-server"

        # 1. Local workspace node_modules/.bin
        if workspace_root:
            local_bin = Path(workspace_root) / "node_modules" / ".bin" / bin_name
            if local_bin.is_file():
                return [str(local_bin), "--stdio"], "local_workspace", str(local_bin)

        # 2. V8OS cache node_modules/.bin
        cache_bin = _get_v8os_cache_root() / "ts" / "node_modules" / ".bin" / bin_name
        if cache_bin.is_file():
            return [str(cache_bin), "--stdio"], "cache", str(cache_bin)

        # 3. Host global PATH
        which_bin = shutil.which("typescript-language-server")
        if which_bin:
            return [which_bin, "--stdio"], "host_path", which_bin

        # 4. Ephemeral runner via npx if node/npx exists
        npx_bin = shutil.which("npx")
        if npx_bin:
            return [npx_bin, "-y", "typescript-language-server", "--stdio"], "ephemeral", npx_bin

        return None

    def _resolve_python(
        self,
        workspace_root: Optional[str],
    ) -> Optional[Tuple[List[str], str, str]]:
        is_win = sys.platform == "win32"
        bin_name = "pyright.exe" if is_win else "pyright"

        # 1. Local workspace .venv
        if workspace_root:
            sub = "Scripts" if is_win else "bin"
            local_venv = Path(workspace_root) / ".venv" / sub / bin_name
            if local_venv.is_file():
                return [str(local_venv), "--stdio"], "local_workspace", str(local_venv)

        # 2. V8OS cache target
        sub = "Scripts" if is_win else "bin"
        cache_bin = _get_v8os_cache_root() / "py" / sub / bin_name
        if cache_bin.is_file():
            return [str(cache_bin), "--stdio"], "cache", str(cache_bin)

        # 3. Host PATH
        which_bin = shutil.which("pyright")
        if which_bin:
            return [which_bin, "--stdio"], "host_path", which_bin

        return None

    def _resolve_rust(self) -> Optional[Tuple[List[str], str, str]]:
        which_bin = shutil.which("rust-analyzer")
        if which_bin:
            return [which_bin], "host_path", which_bin

        # Also check ~/.cargo/bin
        cargo_bin = Path(os.path.expanduser("~")) / ".cargo" / "bin" / (
            "rust-analyzer.exe" if sys.platform == "win32" else "rust-analyzer"
        )
        if cargo_bin.is_file():
            return [str(cargo_bin)], "host_path", str(cargo_bin)

        return None

    def _resolve_go(self) -> Optional[Tuple[List[str], str, str]]:
        which_bin = shutil.which("gopls")
        if which_bin:
            return [which_bin], "host_path", which_bin

        # Check ~/go/bin
        gopath_bin = Path(os.path.expanduser("~")) / "go" / "bin" / (
            "gopls.exe" if sys.platform == "win32" else "gopls"
        )
        if gopath_bin.is_file():
            return [str(gopath_bin)], "host_path", str(gopath_bin)

        return None


lsp_probe_runner = LSPProbeRunner()
