import os
import json
import asyncio
import hashlib
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from langchain_mcp_adapters.tools import load_mcp_tools
import httpx
from pydantic import AnyUrl

from core.json_safe import to_jsonable
from core.security.credentials import resolve_config_credential_refs
from core.storage import storage
from runtimes.extensions.mcp.stdio import stdio_client
from runtimes.extensions.mcp.oauth import mcp_oauth_coordinator

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
        self._app_registry_by_tool: dict[tuple[str, str], dict[str, Any]] = {}
        self._app_resources_by_uri: dict[tuple[str, str], dict[str, Any]] = {}
        self._app_instances: dict[str, dict[str, Any]] = {}

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

    def _extract_ui_resource_uri(self, meta: Any) -> str:
        if not isinstance(meta, dict):
            return ""
        ui_meta = meta.get("ui") if isinstance(meta.get("ui"), dict) else {}
        for value in (
            ui_meta.get("resourceUri"),
            ui_meta.get("resource_uri"),
            meta.get("ui/resourceUri"),
            meta.get("ui.resourceUri"),
            meta.get("ui_resource_uri"),
            meta.get("io.modelcontextprotocol/ui.resourceUri"),
            meta.get("openai/outputTemplate"),
            meta.get("resourceUri"),
            meta.get("resource_uri"),
        ):
            candidate = str(value or "").strip()
            if candidate.startswith("ui://"):
                return candidate
        return ""

    def _extract_ui_meta(self, meta: Any) -> dict[str, Any]:
        if not isinstance(meta, dict):
            return {}
        ui_meta = meta.get("ui") if isinstance(meta.get("ui"), dict) else {}
        return dict(ui_meta or {})

    async def _discover_apps_for_server(self, name: str, session: ClientSession) -> dict[str, Any]:
        app_tools: list[dict[str, Any]] = []
        resources: list[dict[str, Any]] = []
        errors: list[str] = []

        try:
            listed_tools = await session.list_tools()
            for tool in list(getattr(listed_tools, "tools", []) or []):
                tool_payload = to_jsonable(tool)
                if not isinstance(tool_payload, dict):
                    continue
                tool_name = str(tool_payload.get("name") or "").strip()
                if not tool_name:
                    continue
                meta = tool_payload.get("_meta") or tool_payload.get("meta") or {}
                resource_uri = self._extract_ui_resource_uri(meta)
                if not resource_uri:
                    continue
                ui_meta = self._extract_ui_meta(meta)
                entry = {
                    "serverName": name,
                    "toolName": tool_name,
                    "resourceUri": resource_uri,
                    "uiMeta": ui_meta,
                    "schemaHash": hashlib.sha256(
                        json.dumps(
                            tool_payload.get("inputSchema") or {},
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ).encode("utf-8")
                    ).hexdigest()[:16],
                }
                self._app_registry_by_tool[(name, tool_name)] = entry
                app_tools.append(entry)
        except Exception as exc:
            errors.append(f"tools/list apps metadata failed: {type(exc).__name__}: {exc}")

        try:
            listed_resources = await session.list_resources()
            for resource in list(getattr(listed_resources, "resources", []) or []):
                resource_payload = to_jsonable(resource)
                if not isinstance(resource_payload, dict):
                    continue
                uri = str(resource_payload.get("uri") or "").strip()
                if not uri.startswith("ui://"):
                    continue
                meta = resource_payload.get("_meta") or resource_payload.get("meta") or {}
                entry = {
                    "serverName": name,
                    "uri": uri,
                    "name": resource_payload.get("name"),
                    "title": resource_payload.get("title"),
                    "mimeType": resource_payload.get("mimeType") or resource_payload.get("mime_type"),
                    "description": resource_payload.get("description"),
                    "uiMeta": self._extract_ui_meta(meta),
                }
                self._app_resources_by_uri[(name, uri)] = entry
                resources.append(entry)
        except Exception as exc:
            errors.append(f"resources/list failed: {type(exc).__name__}: {exc}")

        return {
            "appsSupported": bool(app_tools or resources),
            "appToolCount": len(app_tools),
            "uiResourceCount": len(resources),
            "appTools": app_tools,
            "uiResources": resources,
            "lastAppsError": "; ".join(errors) if errors else None,
        }

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

    def _remove_server_apps(self, name: str) -> None:
        for key in [key for key in self._app_registry_by_tool if key[0] == name]:
            self._app_registry_by_tool.pop(key, None)
        for key in [key for key in self._app_resources_by_uri if key[0] == name]:
            self._app_resources_by_uri.pop(key, None)
        for instance_id, instance in list(self._app_instances.items()):
            if instance.get("serverName") == name:
                self._app_instances.pop(instance_id, None)

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
        # Credential refs are resolved only in this ephemeral connection copy.
        # The canonical config, fingerprints, logs and API responses keep refs.
        srv_config = resolve_config_credential_refs(srv_config)
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
                auth = mcp_oauth_coordinator.provider_for(server_name=name, server_url=url, config=srv_config) if bool(srv_config.get("oauth")) else None
                custom_client = httpx.AsyncClient(headers=headers, timeout=30.0, auth=auth)
                custom_client = await stack.enter_async_context(custom_client)
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(url=url, http_client=custom_client)
                )

            elif transport_type == "sse":
                if not url:
                    raise ValueError(f"Server '{name}' is configured for sse but missing 'url'.")
                headers = srv_config.get("headers", {})
                print(f"[MCP] Connecting to server '{name}' via SSE ({url})...")
                auth = mcp_oauth_coordinator.provider_for(server_name=name, server_url=url, config=srv_config) if bool(srv_config.get("oauth")) else None
                read, write = await stack.enter_async_context(sse_client(url=url, headers=headers, auth=auth))

            else:
                raise ValueError(f"Unknown transport type: {transport_type}")

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            server_tools = await load_mcp_tools(session)
            apps_discovery = await self._discover_apps_for_server(name, session)
            for t in server_tools:
                t.metadata = getattr(t, "metadata", {}) or {}
                t.metadata["server_name"] = name
                app_entry = self._app_registry_by_tool.get((name, str(getattr(t, "name", "") or "")))
                if app_entry:
                    t.metadata["mcp_app"] = {
                        "resourceUri": app_entry.get("resourceUri"),
                        "uiMeta": app_entry.get("uiMeta") or {},
                    }

            self.tools.extend(server_tools)
            self.sessions[name] = session
            self._server_tools[name] = list(server_tools)
            self._set_server_state(
                name,
                transport=transport_type,
                status="connected",
                impact="healthy",
                toolCount=len(server_tools),
                appsSupported=bool(apps_discovery.get("appsSupported")),
                appToolCount=int(apps_discovery.get("appToolCount") or 0),
                uiResourceCount=int(apps_discovery.get("uiResourceCount") or 0),
                lastAppsError=apps_discovery.get("lastAppsError"),
                lastError=None,
                lastErrorKind=None,
                executionImpacted=False,
                readyAt=self._now_iso(),
            )
            if bool(srv_config.get("oauth")):
                mcp_oauth_coordinator.mark_connected(name)
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
            if bool(srv_config.get("oauth")):
                mcp_oauth_coordinator.mark_failed(name, str(exc).strip() or exc.__class__.__name__)
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
            self._remove_server_apps(name)
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
        self._app_registry_by_tool = {}
        self._app_resources_by_uri = {}
        self._app_instances = {}
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
            self._remove_server_apps(normalized_name)
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
                self._remove_server_apps(normalized_name)
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
                self._remove_server_apps(normalized_name)
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
                self._remove_server_apps(removed_name)
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
                    self._remove_server_apps(server_name)
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
                    self._remove_server_apps(server_name)
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
                "appsSupported": bool(server_state.get("appsSupported", False)),
                "appToolCount": int(server_state.get("appToolCount") or 0),
                "uiResourceCount": int(server_state.get("uiResourceCount") or 0),
                "lastAppsError": server_state.get("lastAppsError"),
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

    def get_app_registry(self) -> dict[str, Any]:
        servers: dict[str, dict[str, Any]] = {}
        for (server_name, tool_name), entry in sorted(self._app_registry_by_tool.items()):
            server = servers.setdefault(
                server_name,
                {
                    "serverName": server_name,
                    "appsSupported": True,
                    "appTools": [],
                    "uiResources": [],
                },
            )
            server["appTools"].append(dict(entry))
        for (server_name, uri), entry in sorted(self._app_resources_by_uri.items()):
            server = servers.setdefault(
                server_name,
                {
                    "serverName": server_name,
                    "appsSupported": True,
                    "appTools": [],
                    "uiResources": [],
                },
            )
            server["uiResources"].append(dict(entry))
        for server_name, state in self._server_state.items():
            server = servers.setdefault(
                server_name,
                {
                    "serverName": server_name,
                    "appsSupported": bool(state.get("appsSupported")),
                    "appTools": [],
                    "uiResources": [],
                },
            )
            server["appsSupported"] = bool(server.get("appsSupported") or state.get("appsSupported"))
            server["appToolCount"] = int(state.get("appToolCount") or len(server.get("appTools") or []))
            server["uiResourceCount"] = int(state.get("uiResourceCount") or len(server.get("uiResources") or []))
            server["lastAppsError"] = state.get("lastAppsError")
            server["status"] = state.get("status")
        return {
            "enabled": True,
            "extension": "io.modelcontextprotocol/ui",
            "serverCount": len(servers),
            "servers": list(servers.values()),
        }

    def find_app_for_tool(self, *, tool_name: str, server_name: str | None = None) -> dict[str, Any] | None:
        normalized_tool = str(tool_name or "").strip()
        normalized_server = str(server_name or "").strip()
        if normalized_server:
            entry = self._app_registry_by_tool.get((normalized_server, normalized_tool))
            return dict(entry) if entry else None
        matches = [dict(entry) for (srv, tool), entry in self._app_registry_by_tool.items() if tool == normalized_tool]
        if len(matches) == 1:
            return matches[0]
        return None

    def create_app_instance(
        self,
        *,
        server_name: str,
        tool_name: str,
        resource_uri: str,
        tool_invocation_id: str,
        initial_tool_result: Any = None,
        session_id: str | None = None,
        run_id: str | None = None,
        plugin_id: str | None = None,
        plugin_digest: str | None = None,
        grant_id: str | None = None,
        component_id: str | None = None,
    ) -> dict[str, Any]:
        seed = f"{server_name}:{tool_name}:{resource_uri}:{tool_invocation_id or uuid.uuid4().hex}"
        app_instance_id = f"mcpapp_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}"
        resource_meta = dict(self._app_resources_by_uri.get((server_name, resource_uri)) or {})
        tool_meta = dict(self._app_registry_by_tool.get((server_name, tool_name)) or {})
        instance = {
            "appInstanceId": app_instance_id,
            "serverName": server_name,
            "toolName": tool_name,
            "resourceUri": resource_uri,
            "toolInvocationId": tool_invocation_id,
            "sessionId": str(session_id or "").strip(),
            "runId": str(run_id or "").strip(),
            "pluginId": str(plugin_id or "").strip(),
            "pluginDigest": str(plugin_digest or "").strip(),
            "grantId": str(grant_id or "").strip(),
            "componentId": str(component_id or "").strip(),
            "initialToolResult": to_jsonable(initial_tool_result),
            "initialToolResultRef": None,
            "uiMeta": tool_meta.get("uiMeta") or resource_meta.get("uiMeta") or {},
            "mimeType": resource_meta.get("mimeType"),
            "csp": (tool_meta.get("uiMeta") or resource_meta.get("uiMeta") or {}).get("csp") or {},
            "permissions": (tool_meta.get("uiMeta") or resource_meta.get("uiMeta") or {}).get("permissions") or {},
            "status": "open",
            "createdAt": self._now_iso(),
            "updatedAt": self._now_iso(),
        }
        self._app_instances[app_instance_id] = instance
        return dict(instance)

    def get_app_instance(self, app_instance_id: str) -> dict[str, Any] | None:
        instance = self._app_instances.get(str(app_instance_id or "").strip())
        return dict(instance) if instance else None

    def update_app_instance(self, app_instance_id: str, **updates: Any) -> dict[str, Any] | None:
        normalized_id = str(app_instance_id or "").strip()
        instance = self._app_instances.get(normalized_id)
        if not instance:
            return None
        next_instance = {**instance, **to_jsonable(updates), "updatedAt": self._now_iso()}
        self._app_instances[normalized_id] = next_instance
        return dict(next_instance)

    async def read_app_resource(self, *, server_name: str, uri: str) -> dict[str, Any]:
        normalized_server = str(server_name or "").strip()
        normalized_uri = str(uri or "").strip()
        if not normalized_server or normalized_server not in self.sessions:
            raise ValueError("MCP server is not connected")
        if not normalized_uri.startswith("ui://"):
            raise ValueError("Only ui:// MCP app resources can be read through this endpoint")
        session = self.sessions[normalized_server]
        result = await session.read_resource(AnyUrl(normalized_uri))
        contents = list(getattr(result, "contents", []) or [])
        if not contents:
            raise ValueError("MCP app resource returned no contents")
        content = contents[0]
        payload = to_jsonable(content)
        if not isinstance(payload, dict):
            payload = {}
        text = str(payload.get("text") or payload.get("blob") or "")
        mime_type = str(payload.get("mimeType") or payload.get("mime_type") or "").strip()
        resource_meta = dict(self._app_resources_by_uri.get((normalized_server, normalized_uri)) or {})
        meta = payload.get("_meta") or payload.get("meta") or resource_meta.get("uiMeta") or {}
        effective_mime_type = mime_type or resource_meta.get("mimeType") or "text/html;profile=mcp-app"
        if effective_mime_type and not (
            str(effective_mime_type).startswith("text/html") or "mcp-app" in str(effective_mime_type)
        ):
            raise ValueError(f"Unsupported MCP app resource mime type: {effective_mime_type}")
        sha256 = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        return {
            "serverName": normalized_server,
            "uri": normalized_uri,
            "mimeType": effective_mime_type,
            "html": text,
            "sha256": sha256,
            "uiMeta": self._extract_ui_meta(meta) or resource_meta.get("uiMeta") or {},
            "csp": (self._extract_ui_meta(meta) or resource_meta.get("uiMeta") or {}).get("csp") or {},
            "permissions": (self._extract_ui_meta(meta) or resource_meta.get("uiMeta") or {}).get("permissions") or {},
        }

    async def call_app_tool(
        self,
        *,
        app_instance_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        instance = self._app_instances.get(str(app_instance_id or "").strip())
        if not instance:
            raise ValueError("Unknown MCP app instance")
        server_name = str(instance.get("serverName") or "").strip()
        if not server_name or server_name not in self.sessions:
            raise ValueError("MCP server is not connected")
        normalized_tool = str(tool_name or "").strip()
        server_tool_names = {
            str(getattr(tool, "name", "") or "").strip()
            for tool in list(self._server_tools.get(server_name) or [])
        }
        if normalized_tool not in server_tool_names:
            raise ValueError("MCP app can only call tools registered by the same MCP server")
        result = await self.sessions[server_name].call_tool(normalized_tool, dict(arguments or {}))
        return {
            "serverName": server_name,
            "toolName": normalized_tool,
            "result": to_jsonable(result),
        }

    async def call_tool(
        self,
        *,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_server = str(server_name or "").strip()
        normalized_tool = str(tool_name or "").strip()
        if not normalized_server or normalized_server not in self.sessions:
            raise ValueError("MCP server is not connected")
        server_tool_names = {
            str(getattr(tool, "name", "") or "").strip()
            for tool in list(self._server_tools.get(normalized_server) or [])
        }
        if normalized_tool not in server_tool_names:
            raise ValueError(f"MCP tool not registered by server: {normalized_tool}")
        result = await self.sessions[normalized_server].call_tool(normalized_tool, dict(arguments or {}))
        return {
            "serverName": normalized_server,
            "toolName": normalized_tool,
            "result": to_jsonable(result),
        }

    def close_app_instance(self, app_instance_id: str) -> dict[str, Any]:
        normalized_id = str(app_instance_id or "").strip()
        instance = self._app_instances.get(normalized_id)
        if not instance:
            return {"ok": False, "error": "unknown_app_instance"}
        instance = dict(instance)
        instance["status"] = "closed"
        instance["updatedAt"] = self._now_iso()
        self._app_instances[normalized_id] = instance
        return {"ok": True, "appInstanceId": normalized_id, "status": "closed"}

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
