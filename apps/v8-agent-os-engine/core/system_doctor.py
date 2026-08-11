from __future__ import annotations

import importlib
import json
import shutil
import socket
import sqlite3
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.process_launch import run_windowless
from core.runtime_ports import governed_web_port
from core.runtime.startup_profile import build_installation_snapshot, startup_bundle_diagnostics
from core.storage import storage
from core.storage_retention import storage_retention_service
from core.v8_agent_os_paths import (
    CHECKPOINT_DB_PATH,
    CONFIG_JSON_PATH,
    OBSERVABILITY_DB_PATH,
    STATE_DB_PATH,
    V8_AGENT_OS_HOME,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _check(status: str, check_id: str, title: str, summary: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "title": title,
        "summary": summary,
        **extra,
    }


def _module_available(module_name: str) -> tuple[bool, str | None]:
    try:
        module = importlib.import_module(module_name)
        return True, str(getattr(module, "__version__", "") or "") or None
    except Exception as exc:
        return False, str(exc)


def _run_version_command(command: list[str], *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    try:
        executable = shutil.which(command[0])
        if executable is None and sys.platform == "win32":
            executable = shutil.which(f"{command[0]}.cmd") or shutil.which(f"{command[0]}.exe")
        if executable is None:
            return {"ok": False, "output": f"{command[0]} not found on PATH", "returnCode": None}
        completed = run_windowless(
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        return {"ok": completed.returncode == 0, "output": output, "returnCode": completed.returncode}
    except Exception as exc:
        return {"ok": False, "output": str(exc), "returnCode": None}


def _connect_port(port: int) -> dict[str, Any]:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.35):
            return {"port": port, "open": True, "code": 0}
    except OSError as exc:
        return {"port": port, "open": False, "code": getattr(exc, "errno", None), "error": str(exc)}


class SystemDoctorService:
    """Local health doctor for the V8 Agent OS host."""

    def run(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        checkers = (
            self._check_paths,
            self._check_ports,
            self._check_dependencies,
            self._check_databases,
            self._check_models,
            self._check_runtimes,
            self._check_extensions,
            self._check_network_supervisor_compat,
            self._check_storage_pressure,
        )
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="v8-system-doctor") as executor:
            for result in executor.map(lambda checker: checker(), checkers):
                checks.extend(result)
        summary = self._summarize(checks)
        return {
            "id": f"doctor_{uuid.uuid4().hex[:12]}",
            "generatedAt": _utc_now(),
            "summary": summary,
            "checks": checks,
            "repairPlan": self.build_repair_plan(checks),
        }

    def build_repair_plan(self, checks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        target_checks = list(checks or self.run().get("checks") or [])
        actions: list[dict[str, Any]] = []
        for item in target_checks:
            if item.get("status") not in {"warning", "error"}:
                continue
            check_id = str(item.get("id") or "")
            if check_id.startswith("db."):
                actions.append(
                    {
                        "id": f"repair_{check_id}",
                        "title": "检查并迁移数据库",
                        "description": "数据库检查异常时先备份，再运行迁移/重建索引。该动作需要显式确认。",
                        "risk": "medium",
                        "requiresConfirmation": True,
                        "action": "backup_then_run_db_migrations",
                        "checkId": check_id,
                    }
                )
            elif check_id == "storage.pressure":
                actions.append(
                    {
                        "id": "repair_storage_retention_dry_run",
                        "title": "生成空间清理 dry-run",
                        "description": "只生成将清理哪些日志/证据的计划，不删除用户消息、artifact 或向量知识库。",
                        "risk": "low",
                        "requiresConfirmation": False,
                        "action": "storage_retention_dry_run",
                        "checkId": check_id,
                    }
                )
            elif check_id.startswith("models."):
                actions.append(
                    {
                        "id": f"repair_{check_id}",
                        "title": "打开 Model Hub 体检",
                        "description": "补齐模型 key、context window、输入窗口或 provider 连接信息。",
                        "risk": "low",
                        "requiresConfirmation": False,
                        "action": "open_model_role_doctor",
                        "checkId": check_id,
                    }
                )
            elif check_id.startswith("deps."):
                actions.append(
                    {
                        "id": f"repair_{check_id}",
                        "title": "修复运行依赖",
                        "description": "依赖修复可能涉及安装包或切换解释器，需要用户确认。",
                        "risk": "medium",
                        "requiresConfirmation": True,
                        "action": "inspect_dependency_installation",
                        "checkId": check_id,
                    }
                )
            elif check_id.startswith("ports."):
                actions.append(
                    {
                        "id": f"repair_{check_id}",
                        "title": "检查端口占用",
                        "description": "定位端口占用或服务未启动原因；不自动关闭进程。",
                        "risk": "low",
                        "requiresConfirmation": False,
                        "action": "inspect_ports",
                        "checkId": check_id,
                    }
                )
        return {
            "mode": "plan_only",
            "generatedAt": _utc_now(),
            "actions": actions,
            "requiresExplicitConfirmationForWrites": True,
        }

    @staticmethod
    def _summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {"ok": 0, "warning": 0, "error": 0, "info": 0}
        for item in checks:
            status = str(item.get("status") or "info")
            counts[status] = counts.get(status, 0) + 1
        overall = "error" if counts.get("error") else ("warning" if counts.get("warning") else "ok")
        return {"status": overall, "counts": counts}

    def _check_paths(self) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        home_exists = V8_AGENT_OS_HOME.exists()
        checks.append(
            _check(
                "ok" if home_exists else "warning",
                "paths.runtime_home",
                "Runtime home",
                str(V8_AGENT_OS_HOME),
                path=str(V8_AGENT_OS_HOME),
                exists=home_exists,
            )
        )
        config_exists = CONFIG_JSON_PATH.exists()
        checks.append(
            _check(
                "ok" if config_exists else "warning",
                "paths.config",
                "config.json",
                "Config file found." if config_exists else "config.json is missing; defaults will be synthesized.",
                path=str(CONFIG_JSON_PATH),
                exists=config_exists,
            )
        )
        return checks

    def _check_ports(self) -> list[dict[str, Any]]:
        port_labels = {9530: "Engine", 9528: "Admin", governed_web_port(): "Web"}
        checks: list[dict[str, Any]] = []
        for port, label in port_labels.items():
            result = _connect_port(port)
            expected = port in {9530, 9528}
            status = "ok" if result["open"] else ("warning" if expected else "info")
            checks.append(
                _check(
                    status,
                    f"ports.{port}",
                    f"{label} port {port}",
                    "Port is reachable." if result["open"] else "Port is not reachable on localhost.",
                    **result,
                )
            )
        return checks

    def _check_dependencies(self) -> list[dict[str, Any]]:
        checks = [
            _check(
                "ok",
                "deps.python",
                "Python",
                sys.version.splitlines()[0],
                executable=sys.executable,
            )
        ]
        for module_name in ("fastapi", "uvicorn", "pydantic", "langchain_core"):
            ok, detail = _module_available(module_name)
            checks.append(
                _check(
                    "ok" if ok else "error",
                    f"deps.python_module.{module_name}",
                    module_name,
                    detail or "available",
                )
            )
        for command in (["node", "--version"], ["npm", "--version"]):
            result = _run_version_command(command)
            checks.append(
                _check(
                    "ok" if result["ok"] else "warning",
                    f"deps.command.{command[0]}",
                    command[0],
                    result["output"] or "not available",
                    **result,
                )
            )
        return checks

    def _check_databases(self) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        required_tables = {
            STATE_DB_PATH: ("run_records", "runtime_events", "runtime_artifacts"),
            OBSERVABILITY_DB_PATH: ("tool_observation_records", "run_ledger_events", "conversation_compaction_records"),
            CHECKPOINT_DB_PATH: (),
        }
        for path, tables in required_tables.items():
            if not path.exists():
                checks.append(
                    _check(
                        "warning",
                        f"db.{path.stem}",
                        path.name,
                        "Database file does not exist yet.",
                        path=str(path),
                    )
                )
                continue
            try:
                conn = sqlite3.connect(path, timeout=2)
                try:
                    quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
                    missing = []
                    for table in tables:
                        exists = conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,),
                        ).fetchone()
                        if not exists:
                            missing.append(table)
                    status = "ok" if quick.lower() == "ok" and not missing else "warning"
                    checks.append(
                        _check(
                            status,
                            f"db.{path.stem}",
                            path.name,
                            "Database quick_check ok." if status == "ok" else "Database needs migration or inspection.",
                            path=str(path),
                            quickCheck=quick,
                            missingTables=missing,
                        )
                    )
                finally:
                    conn.close()
            except Exception as exc:
                checks.append(_check("error", f"db.{path.stem}", path.name, str(exc), path=str(path)))
        return checks

    def _check_models(self) -> list[dict[str, Any]]:
        try:
            config = storage.get_models_config() or {}
        except Exception as exc:
            return [_check("error", "models.config", "Models config", str(exc))]
        providers = config.get("providers") if isinstance(config.get("providers"), list) else []
        models = config.get("models") if isinstance(config.get("models"), list) else []
        roles = config.get("roles") if isinstance(config.get("roles"), dict) else {}
        checks = [
            _check(
                "ok" if providers or models else "warning",
                "models.catalog",
                "Model catalog",
                f"{len(providers)} providers, {len(models)} models configured.",
                providerCount=len(providers),
                modelCount=len(models),
            )
        ]
        missing_roles = [role for role in ("supervisor", "summary") if not str(roles.get(role) or "").strip()]
        checks.append(
            _check(
                "ok" if not missing_roles else "warning",
                "models.roles",
                "Model roles",
                "Required text roles configured." if not missing_roles else f"Missing roles: {', '.join(missing_roles)}",
                missingRoles=missing_roles,
            )
        )
        return checks

    def _check_runtimes(self) -> list[dict[str, Any]]:
        try:
            snapshot = build_installation_snapshot()
            diagnostics = startup_bundle_diagnostics()
            disabled = list((snapshot.get("runtimeInstallation") or {}).get("disabledRuntimeFamilies") or [])
            status = "ok" if not disabled else "warning"
            return [
                _check(
                    status,
                    "runtime.installation",
                    "Runtime installation",
                    "Runtime installation snapshot loaded.",
                    snapshot=snapshot.get("runtimeInstallation") or snapshot,
                    diagnostics=diagnostics,
                    disabledRuntimeFamilies=disabled,
                )
            ]
        except Exception as exc:
            return [_check("error", "runtime.installation", "Runtime installation", str(exc))]

    def _check_extensions(self) -> list[dict[str, Any]]:
        try:
            service = importlib.import_module("runtimes.extensions.runtime").extensions_runtime_service
            mcp = service.get_mcp_startup_status()
            skills = service.get_skill_startup_status()
            mcp_state = str(mcp.get("startupState") or mcp.get("status") or "unknown")
            skill_state = str(skills.get("startupState") or skills.get("status") or "unknown")
            return [
                _check(
                    "ok" if mcp_state not in {"failed", "error"} else "warning",
                    "extensions.mcp",
                    "MCP",
                    f"MCP startup state: {mcp_state}",
                    statusPayload=mcp,
                ),
                _check(
                    "ok" if skill_state not in {"failed", "error"} else "warning",
                    "extensions.skills",
                    "Skills",
                    f"Skill startup state: {skill_state}",
                    statusPayload=skills,
                ),
            ]
        except Exception as exc:
            return [_check("warning", "extensions.status", "Extensions", str(exc))]

    def _check_network_supervisor_compat(self) -> list[dict[str, Any]]:
        try:
            service = importlib.import_module("runtimes.network_supervisor.service").network_supervisor_service
            pending = service.pending_external_tools_summary(limit=10)
            waiting = int(pending.get("waitingCount") or 0)
            recent = list(pending.get("recent") or [])
            abandoned = [
                item for item in recent
                if str(item.get("status") or "") == "external_tool_abandoned"
            ]
            status = "warning" if waiting or abandoned else "ok"
            return [
                _check(
                    status,
                    "compat.external_tools",
                    "Compat external tools",
                    "No pending external tools." if status == "ok" else f"{waiting} waiting, {len(abandoned)} recently abandoned.",
                    pending=pending,
                )
            ]
        except Exception as exc:
            return [_check("warning", "compat.external_tools", "Compat external tools", str(exc))]

    def _check_storage_pressure(self) -> list[dict[str, Any]]:
        try:
            stats = storage_retention_service.build_stats()
            findings = list(stats.get("budgetFindings") or [])
            non_ok = [item for item in findings if item.get("severity") in {"warning", "error"}]
            disk = dict(stats.get("disk") or {})
            watermark = str(disk.get("watermark") or "healthy")
            needs_attention = watermark != "healthy" or bool(non_ok)
            return [
                _check(
                    "ok" if not needs_attention else "warning",
                    "storage.pressure",
                    "Storage pressure",
                    "Disk headroom and storage classes are healthy."
                    if not needs_attention
                    else f"Disk watermark is {watermark}; {len(non_ok)} storage class budget(s) need attention.",
                    stats={
                        "totalGovernedBytes": stats.get("totalGovernedBytes"),
                        "totalProductBytes": stats.get("totalProductBytes"),
                        "storageClassTotals": stats.get("storageClassTotals"),
                        "disk": disk,
                        "budgetFindings": findings,
                    },
                )
            ]
        except Exception as exc:
            return [_check("warning", "storage.pressure", "Storage pressure", str(exc))]


system_doctor_service = SystemDoctorService()
