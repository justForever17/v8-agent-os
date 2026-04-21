import os
import json
import asyncio
import hashlib
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from langchain_mcp_adapters.tools import load_mcp_tools
import httpx

from core.storage import storage
from runtimes.extensions.mcp.stdio import stdio_client

MCP_SERVER_INIT_TIMEOUT_SECONDS = float(
    os.environ.get("V8_AGENT_OS_MCP_SERVER_INIT_TIMEOUT_SECONDS", "15.0").strip() or "15.0"
)


@dataclass(slots=True)
class _ManagedServerTask:
    name: str
    stop_event: asyncio.Event
    task: asyncio.Task
    ready_future: asyncio.Future

class MCPManager:
    def __init__(self):
        # Now uses the globally managed `config.json#mcp` domain via storage aliases
        self.config_path = storage.mcp_config_path
        self.tools: List[Any] = []
        self.sessions = {}
        self.subprocesses = {}  # Added to track raw processes
        self._server_tools = {}
        self._server_tasks: dict[str, _ManagedServerTask] = {}
        self._server_state: dict[str, dict[str, Any]] = {}
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._closing = False
        self._startup_state = "cold"
        self._last_refresh_at: str | None = None
        self._last_refresh_error: str | None = None
        self._server_config_fingerprints: dict[str, str] = {}
        self._inventory_revision = "cold"
        self._last_reload_result: dict[str, Any] = {}

    def _log_server_task_result(self, name: str, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            print(f"[MCP] Server task '{name}' exited: {type(exc).__name__}: {exc}")

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _server_config_fingerprint(self, name: str, srv_config: dict[str, Any]) -> str:
        payload = {
            "name": name,
            "config": srv_config or {},
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _revision_from_state(self) -> str:
        server_payload: list[dict[str, Any]] = []
        for name in sorted(set(self._server_config_fingerprints) | set(self._server_tools) | set(self._server_state)):
            server_payload.append(
                {
                    "name": name,
                    "configFingerprint": self._server_config_fingerprints.get(name),
                    "toolNames": sorted(str(getattr(tool, "name", "") or "") for tool in list(self._server_tools.get(name) or [])),
                    "status": str((self._server_state.get(name) or {}).get("status") or ""),
                }
            )
        raw = json.dumps(server_payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _commit_inventory_revision(self) -> str:
        self._inventory_revision = self._revision_from_state()
        return self._inventory_revision

    def _set_server_state(self, name: str, **patch: Any) -> dict[str, Any]:
        current = dict(self._server_state.get(name) or {})
        current.update(patch)
        current["updatedAt"] = self._now_iso()
        self._server_state[name] = current
        return current

    async def initialize(self):
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return
            if self._closing:
                print("[MCP] Cleanup in progress. Skipping initialize.")
                return

            print(f"[MCP] Initializing MCP Client Manager from {self.config_path}")
            self._startup_state = "refreshing"
            self._last_refresh_error = None

            try:
                config = storage.get_mcp_config()
            except Exception as e:
                print(f"[MCP] Error parsing {self.config_path}: {e}")
                self._startup_state = "error"
                self._last_refresh_error = str(e).strip() or e.__class__.__name__
                return

            servers = config.get("mcpServers", {})
            if not servers:
                print("[MCP] No MCP servers configured.")
                self._initialized = True
                self._startup_state = "ready"
                self._last_refresh_at = self._now_iso()
                self._commit_inventory_revision()
                return

            for name, srv_config in servers.items():
                self._server_config_fingerprints[name] = self._server_config_fingerprint(name, srv_config)
                if self._closing:
                    print("[MCP] Cleanup requested during initialize. Stopping further MCP bootstrap.")
                    break

                transport_type = srv_config.get("type") or ("stdio" if srv_config.get("command") else ("http" if str(srv_config.get("url") or "").startswith("http") else "sse"))
                if srv_config.get("disabled", False):
                    self._set_server_state(
                        name,
                        transport=transport_type,
                        status="disabled",
                        impact="disabled",
                        toolCount=0,
                        lastError=None,
                        lastErrorKind=None,
                        executionImpacted=False,
                    )
                    print(f"[MCP] Server '{name}' is disabled. Skipping.")
                    continue

                command = srv_config.get("command")
                url = srv_config.get("url")
                if not command and not url:
                    self._set_server_state(
                        name,
                        transport=transport_type,
                        status="error",
                        impact="startup_failed",
                        toolCount=0,
                        lastError="missing command/url",
                        lastErrorKind="ConfigurationError",
                        executionImpacted=False,
                    )
                    print(f"[MCP] Warning: Server '{name}' missing 'command' or 'url'. Skipping.")
                    continue

                try:
                    await self._start_server(name, srv_config)
                except asyncio.CancelledError:
                    if self._closing:
                        print(f"[MCP] MCP startup for '{name}' cancelled during shutdown.")
                        break
                    raise
                except asyncio.TimeoutError:
                    print(f"[MCP] Timeout while loading MCP server '{name}' after {MCP_SERVER_INIT_TIMEOUT_SECONDS:.0f}s. Skipping for this boot.")
                except Exception as e:
                    print(f"[MCP] Failed to load MCP server '{name}': {e}")
                except BaseException as e:
                    if hasattr(e, 'exceptions'):
                        causes = [f"{type(exc).__name__}: {exc}" for exc in e.exceptions]
                        print(f"[MCP] Fatal error loading MCP server '{name}': {', '.join(causes)}")
                    else:
                        print(f"[MCP] Fatal error loading MCP server '{name}': {type(e).__name__}: {e}")

            self._initialized = True
            self._startup_state = "ready"
            self._last_refresh_at = self._now_iso()
            self._commit_inventory_revision()

    def _remove_server_tools(self, name: str) -> None:
        server_tools = list(self._server_tools.pop(name, []) or [])
        if not server_tools:
            return
        remove_ids = {id(tool) for tool in server_tools}
        self.tools = [tool for tool in self.tools if id(tool) not in remove_ids]

    async def _stop_server_task(self, name: str, *, cancel: bool = False) -> None:
        managed = self._server_tasks.pop(name, None)
        if not managed:
            return
        managed.stop_event.set()
        if cancel and not managed.task.done():
            managed.task.cancel()
        try:
            await managed.task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[MCP] Error stopping server task '{name}': {type(exc).__name__}: {exc}")

    async def _start_server(self, name: str, srv_config: dict[str, Any]) -> None:
        await self._stop_server_task(name, cancel=True)
        transport_type = srv_config.get("type") or ("stdio" if srv_config.get("command") else ("http" if str(srv_config.get("url") or "").startswith("http") else "sse"))
        started_at = self._now_iso()
        self._set_server_state(
            name,
            transport=transport_type,
            status="connecting",
            impact="background_connect",
            toolCount=0,
            lastError=None,
            lastErrorKind=None,
            executionImpacted=False,
            startedAt=started_at,
            readyAt=None,
            timedOutDuringStartup=False,
        )
        stop_event = asyncio.Event()
        ready_future = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(
            self._run_server_task(name, srv_config, stop_event, ready_future),
            name=f"mcp:{name}",
        )
        task.add_done_callback(lambda completed_task, server_name=name: self._log_server_task_result(server_name, completed_task))
        self._server_tasks[name] = _ManagedServerTask(
            name=name,
            stop_event=stop_event,
            task=task,
            ready_future=ready_future,
        )
        try:
            await asyncio.wait_for(asyncio.shield(ready_future), timeout=MCP_SERVER_INIT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self._set_server_state(
                name,
                transport=transport_type,
                status="connecting",
                impact="background_connect",
                toolCount=len(self._server_tools.get(name) or []),
                lastError=None,
                lastErrorKind=None,
                executionImpacted=False,
                startedAt=started_at,
                readyAt=None,
                timedOutDuringStartup=True,
            )
            print(
                f"[MCP] Server '{name}' is still warming after "
                f"{MCP_SERVER_INIT_TIMEOUT_SECONDS:.0f}s; keeping it running in background."
            )
            return
        except BaseException:
            stop_event.set()
            if not ready_future.done():
                ready_future.cancel()
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            self._server_tasks.pop(name, None)
            raise

    async def _run_server_task(
        self,
        name: str,
        srv_config: dict[str, Any],
        stop_event: asyncio.Event,
        ready_future: asyncio.Future,
    ) -> None:
        if self._closing:
            raise asyncio.CancelledError("MCP manager is shutting down")
        stack = AsyncExitStack()
        command = srv_config.get("command")
        url = srv_config.get("url")
        transport_type = srv_config.get("type") or ("stdio" if command else ("http" if str(url or "").startswith("http") else "sse"))
        try:
            if transport_type == "stdio":
                if not command:
                    raise ValueError(f"Server '{name}' is configured for stdio but missing 'command'.")
                args = srv_config.get("args", [])
                env = srv_config.get("env", None)
                server_env = os.environ.copy()
                if env:
                    for k, v in env.items():
                        server_env[k] = str(v)

                import platform
                import shutil
                if platform.system() == "Windows":
                    if command == "npx" and not shutil.which("npx"):
                        if shutil.which("npx.cmd"):
                            command = "npx.cmd"
                    elif command == "npm" and not shutil.which("npm"):
                        if shutil.which("npm.cmd"):
                            command = "npm.cmd"
                    elif command == "uvx" and not shutil.which("uvx"):
                        if shutil.which("uvx.exe"):
                            command = "uvx.exe"

                server_params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=server_env
                )

                print(f"[MCP] Connecting to server '{name}' via stdio ({command})...")
                stdio_ctx = stdio_client(server_params)
                read, write = await stack.enter_async_context(stdio_ctx)

                if hasattr(stdio_ctx, 'process'):
                    self.subprocesses[name] = stdio_ctx.process
                elif hasattr(read, 'process'):
                    self.subprocesses[name] = read.process
                elif hasattr(read, '_process'):
                    self.subprocesses[name] = read._process
                elif hasattr(write, 'process'):
                    self.subprocesses[name] = write.process
                elif hasattr(write, '_process'):
                    self.subprocesses[name] = write._process

            elif transport_type == "http":
                if not url:
                    raise ValueError(f"Server '{name}' is configured for HTTP (Streamable) but missing 'url'.")
                headers = srv_config.get("headers", {})
                print(f"[MCP] Connecting to server '{name}' via HTTP ({url})...")
                custom_client = httpx.AsyncClient(headers=headers, timeout=30.0)
                custom_client = await stack.enter_async_context(custom_client)
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(url=url, http_client=custom_client)
                )

            elif transport_type == "sse":
                if not url:
                    raise ValueError(f"Server '{name}' is configured for sse but missing 'url'.")
                headers = srv_config.get("headers", {})
                print(f"[MCP] Connecting to server '{name}' via SSE ({url})...")
                read, write = await stack.enter_async_context(sse_client(url=url, headers=headers))

            else:
                raise ValueError(f"Unknown transport type: {transport_type}")

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            server_tools = await load_mcp_tools(session)
            for t in server_tools:
                t.metadata = getattr(t, "metadata", {}) or {}
                t.metadata["server_name"] = name

            self.tools.extend(server_tools)
            self.sessions[name] = session
            self._server_tools[name] = list(server_tools)
            self._set_server_state(
                name,
                transport=transport_type,
                status="connected",
                impact="healthy",
                toolCount=len(server_tools),
                lastError=None,
                lastErrorKind=None,
                executionImpacted=False,
                readyAt=self._now_iso(),
            )
            self._commit_inventory_revision()
            if not ready_future.done():
                ready_future.set_result({"tool_count": len(server_tools)})
            print(f"[MCP] Successfully loaded {len(server_tools)} tools from '{name}'.")
            await stop_event.wait()
        except asyncio.CancelledError:
            if not ready_future.done():
                ready_future.cancel()
            raise
        except Exception as exc:
            if ready_future.done():
                self._set_server_state(
                    name,
                    transport=transport_type,
                    status="reconnecting",
                    impact="background_reconnect",
                    toolCount=len(self._server_tools.get(name) or []),
                    lastError=str(exc).strip() or exc.__class__.__name__,
                    lastErrorKind=exc.__class__.__name__,
                    executionImpacted=False,
                )
            else:
                self._set_server_state(
                    name,
                    transport=transport_type,
                    status="error",
                    impact="startup_failed",
                    toolCount=0,
                    lastError=str(exc).strip() or exc.__class__.__name__,
                    lastErrorKind=exc.__class__.__name__,
                    executionImpacted=False,
                )
            if not ready_future.done():
                ready_future.set_exception(exc)
            raise
        finally:
            self.sessions.pop(name, None)
            self.subprocesses.pop(name, None)
            self._remove_server_tools(name)
            if self._closing or stop_event.is_set():
                self._set_server_state(
                    name,
                    transport=transport_type,
                    status="stopped",
                    impact="stopped",
                    toolCount=0,
                    executionImpacted=False,
                )
            self._commit_inventory_revision()
            try:
                await stack.aclose()
            except asyncio.CancelledError:
                raise
            except Exception as close_exc:
                if not (self._closing or stop_event.is_set()):
                    print(f"[MCP] Error closing '{name}': {type(close_exc).__name__}: {close_exc}")

    async def cleanup(self):
        print("[MCP] Cleaning up MCP Client connections...")
        self._closing = True

        # Phase 1: Forcefully terminate tracked MCP subprocesses FIRST.
        # Only kill subprocesses created by MCP adapters; do not touch unrelated engine children.
        try:
            for name, process in list(self.subprocesses.items()):
                try:
                    pid = getattr(process, "pid", None)
                    if pid is None and hasattr(process, "process"):
                        pid = getattr(process.process, "pid", None)

                    if hasattr(process, "kill"):
                        print(f"[MCP] Force killing tracked MCP process '{name}' PID {pid}...")
                        process.kill()
                except Exception as process_err:
                    print(f"[MCP] Failed to kill tracked MCP process '{name}': {process_err}")
        except Exception as e:
            print(f"[MCP] Failed to terminate tracked MCP processes cleanly: {e}")

        tasks = []
        for name, managed in list(self._server_tasks.items()):
            managed.stop_event.set()
            tasks.append(managed.task)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, asyncio.CancelledError):
                    continue
                if isinstance(result, Exception):
                    print(f"[MCP] Server task shutdown returned: {type(result).__name__}: {result}")

        self.tools = []
        self.sessions = {}
        self.subprocesses = {}
        self._server_tools = {}
        self._server_tasks = {}
        self._server_config_fingerprints = {}
        self._initialized = False
        self._closing = False
        self._startup_state = "cold"
        self._commit_inventory_revision()
        print("[MCP] Cleanup complete.")

    async def remove_server(self, name: str) -> dict[str, Any]:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return {"changed": False, "server": "", "reason": "empty_name"}
        async with self._init_lock:
            await self._stop_server_task(normalized_name, cancel=True)
            self._remove_server_tools(normalized_name)
            self.sessions.pop(normalized_name, None)
            self.subprocesses.pop(normalized_name, None)
            self._server_tools.pop(normalized_name, None)
            self._server_config_fingerprints.pop(normalized_name, None)
            self._set_server_state(
                normalized_name,
                status="removed",
                impact="removed",
                toolCount=0,
                executionImpacted=False,
                lastError=None,
                lastErrorKind=None,
            )
            self._last_refresh_at = self._now_iso()
            self._commit_inventory_revision()
            return {
                "changed": True,
                "server": normalized_name,
                "revision": self._inventory_revision,
            }

    async def refresh_server(self, name: str) -> dict[str, Any]:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return {"changed": False, "server": "", "reason": "empty_name"}
        try:
            config = storage.get_mcp_config() or {}
        except Exception as exc:
            self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
            return {"changed": False, "server": normalized_name, "error": self._last_refresh_error}
        servers = config.get("mcpServers", {}) if isinstance(config, dict) else {}
        if normalized_name not in servers:
            return await self.remove_server(normalized_name)
        async with self._init_lock:
            srv_config = servers.get(normalized_name) or {}
            fingerprint = self._server_config_fingerprint(normalized_name, srv_config)
            self._server_config_fingerprints[normalized_name] = fingerprint
            transport_type = srv_config.get("type") or ("stdio" if srv_config.get("command") else ("http" if str(srv_config.get("url") or "").startswith("http") else "sse"))
            if srv_config.get("disabled", False):
                await self._stop_server_task(normalized_name, cancel=True)
                self._remove_server_tools(normalized_name)
                self._set_server_state(
                    normalized_name,
                    transport=transport_type,
                    status="disabled",
                    impact="disabled",
                    toolCount=0,
                    lastError=None,
                    lastErrorKind=None,
                    executionImpacted=False,
                )
            elif not srv_config.get("command") and not srv_config.get("url"):
                await self._stop_server_task(normalized_name, cancel=True)
                self._remove_server_tools(normalized_name)
                self._set_server_state(
                    normalized_name,
                    transport=transport_type,
                    status="error",
                    impact="startup_failed",
                    toolCount=0,
                    lastError="missing command/url",
                    lastErrorKind="ConfigurationError",
                    executionImpacted=False,
                )
            else:
                await self._start_server(normalized_name, srv_config)
            self._initialized = True
            self._startup_state = "ready"
            self._last_refresh_at = self._now_iso()
            self._last_refresh_error = None
            self._commit_inventory_revision()
            return {
                "changed": True,
                "server": normalized_name,
                "revision": self._inventory_revision,
            }

    async def reload_if_changed(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        changed_servers: dict[str, list[str]] = {"added": [], "updated": [], "removed": []}
        try:
            config = storage.get_mcp_config() or {}
        except Exception as exc:
            self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
            result = {
                "changed": False,
                "refreshMode": "delta",
                "revision": self._inventory_revision,
                "mcpChangedServers": changed_servers,
                "error": self._last_refresh_error,
                "durationMs": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            }
            self._last_reload_result = result
            return result

        servers = config.get("mcpServers", {}) if isinstance(config, dict) else {}
        if not isinstance(servers, dict):
            servers = {}

        async with self._init_lock:
            self._startup_state = "refreshing"
            self._last_refresh_error = None
            current_names = set(self._server_config_fingerprints)
            configured_names = {str(name) for name in servers.keys()}

            for removed_name in sorted(current_names - configured_names):
                await self._stop_server_task(removed_name, cancel=True)
                self._remove_server_tools(removed_name)
                self.sessions.pop(removed_name, None)
                self.subprocesses.pop(removed_name, None)
                self._server_tools.pop(removed_name, None)
                self._server_config_fingerprints.pop(removed_name, None)
                self._set_server_state(
                    removed_name,
                    status="removed",
                    impact="removed",
                    toolCount=0,
                    executionImpacted=False,
                    lastError=None,
                    lastErrorKind=None,
                )
                changed_servers["removed"].append(removed_name)

            for name, srv_config in sorted(servers.items(), key=lambda item: str(item[0]).lower()):
                server_name = str(name)
                srv_config = srv_config or {}
                new_fingerprint = self._server_config_fingerprint(server_name, srv_config)
                old_fingerprint = self._server_config_fingerprints.get(server_name)
                server_known = server_name in self._server_config_fingerprints
                server_state = dict(self._server_state.get(server_name) or {})
                state_status = str(server_state.get("status") or "").strip()
                should_refresh = (
                    not server_known
                    or old_fingerprint != new_fingerprint
                    or state_status in {"error", "removed"}
                )
                if not should_refresh:
                    continue
                self._server_config_fingerprints[server_name] = new_fingerprint
                transport_type = srv_config.get("type") or ("stdio" if srv_config.get("command") else ("http" if str(srv_config.get("url") or "").startswith("http") else "sse"))
                if srv_config.get("disabled", False):
                    await self._stop_server_task(server_name, cancel=True)
                    self._remove_server_tools(server_name)
                    self._set_server_state(
                        server_name,
                        transport=transport_type,
                        status="disabled",
                        impact="disabled",
                        toolCount=0,
                        lastError=None,
                        lastErrorKind=None,
                        executionImpacted=False,
                    )
                elif not srv_config.get("command") and not srv_config.get("url"):
                    await self._stop_server_task(server_name, cancel=True)
                    self._remove_server_tools(server_name)
                    self._set_server_state(
                        server_name,
                        transport=transport_type,
                        status="error",
                        impact="startup_failed",
                        toolCount=0,
                        lastError="missing command/url",
                        lastErrorKind="ConfigurationError",
                        executionImpacted=False,
                    )
                else:
                    try:
                        await self._start_server(server_name, srv_config)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        print(f"[MCP] Delta refresh failed for server '{server_name}': {type(exc).__name__}: {exc}")
                if server_known:
                    changed_servers["updated"].append(server_name)
                else:
                    changed_servers["added"].append(server_name)

            self._initialized = True
            self._startup_state = "ready"
            self._last_refresh_at = self._now_iso()
            self._commit_inventory_revision()

        changed = any(changed_servers.values())
        result = {
            "changed": changed,
            "refreshMode": "delta",
            "revision": self._inventory_revision,
            "mcpChangedServers": changed_servers,
            "durationMs": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        }
        self._last_reload_result = result
        return result

    def get_tools(self) -> List[Any]:
        return self.tools

    def get_status(self) -> dict:
        try:
            config = storage.get_mcp_config() or {}
        except Exception:
            config = {}
        configured_servers = config.get("mcpServers", {})
        
        status = {}
        for name, cfg in configured_servers.items():
            server_state = dict(self._server_state.get(name) or {})
            status[name] = {
                "config": cfg,
                "status": "connected" if name in self.sessions else ("disabled" if cfg.get("disabled") else "error"),
                "tools": [],
                "transport": server_state.get("transport"),
                "impact": server_state.get("impact"),
                "toolCount": server_state.get("toolCount", 0),
                "lastError": server_state.get("lastError"),
                "lastErrorKind": server_state.get("lastErrorKind"),
                "executionImpacted": bool(server_state.get("executionImpacted", False)),
                "updatedAt": server_state.get("updatedAt"),
                "startedAt": server_state.get("startedAt"),
                "readyAt": server_state.get("readyAt"),
                "timedOutDuringStartup": bool(server_state.get("timedOutDuringStartup", False)),
            }
            if server_state.get("status"):
                status[name]["status"] = server_state.get("status")
            
        for t in self.tools:
            srv_name = t.metadata.get("server_name")
            if srv_name and srv_name in status:
                status[srv_name]["tools"].append({
                    "name": t.name,
                    "description": t.description
                })
                status[srv_name]["toolCount"] = len(status[srv_name]["tools"])
        
        return status

    def get_health_summary(self) -> dict[str, Any]:
        status = self.get_status()
        degraded_servers: list[dict[str, Any]] = []
        streamable_http_issues: list[dict[str, Any]] = []
        execution_impacted = False
        connected = 0

        for name, server in status.items():
            current_status = str(server.get("status") or "unknown").strip()
            if current_status == "connected":
                connected += 1
            if current_status in {"error", "reconnecting"}:
                degraded_servers.append(
                    {
                        "name": name,
                        "transport": server.get("transport"),
                        "status": current_status,
                        "impact": server.get("impact") or "background_reconnect",
                        "lastError": server.get("lastError"),
                        "lastErrorKind": server.get("lastErrorKind"),
                    }
                )
            if str(server.get("transport") or "") == "http" and str(server.get("lastErrorKind") or "") == "BrokenResourceError":
                streamable_http_issues.append(
                    {
                        "name": name,
                        "status": current_status,
                        "impact": server.get("impact") or "background_reconnect",
                        "lastError": server.get("lastError"),
                        "executionImpacted": bool(server.get("executionImpacted", False)),
                    }
                )
            execution_impacted = execution_impacted or bool(server.get("executionImpacted", False))

        degraded_entries = [*degraded_servers, *streamable_http_issues]
        background_reconnect_only = bool(degraded_entries) and not execution_impacted and all(
            str(item.get("impact") or "background_reconnect") == "background_reconnect" for item in degraded_entries
        )

        return {
            "configured": len(status),
            "connected": connected,
            "degraded": len(degraded_servers),
            "executionImpacted": execution_impacted,
            "backgroundReconnectOnly": background_reconnect_only,
            "degradedServers": degraded_servers,
            "streamableHttpIssues": streamable_http_issues,
        }

    def get_startup_status(self) -> dict[str, Any]:
        status = self.get_status()
        startup_state = self._startup_state
        if any(str(payload.get("status") or "").strip() == "connecting" for payload in status.values()):
            startup_state = "refreshing"
        connected_servers = [
            name
            for name, payload in sorted(status.items(), key=lambda item: item[0].lower())
            if str(payload.get("status") or "").strip() == "connected"
        ]
        return {
            "startupState": startup_state,
            "lastRefreshAt": self._last_refresh_at,
            "lastRefreshError": self._last_refresh_error,
            "inventoryRevision": self._inventory_revision,
            "lastReloadResult": dict(self._last_reload_result or {}),
            "configuredServers": len(status),
            "connectedServers": connected_servers,
            "connectedCount": len(connected_servers),
        }

    def get_inventory_revision(self) -> str:
        return self._inventory_revision

# Global singleton instance
mcp_manager = MCPManager()
