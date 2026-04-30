from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover - covered through monkeypatch/fallback tests
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

from core.audit_logger import audit_logger
from core.storage import storage


DEFAULT_ACTIVE_DEFENSE_CONFIG: dict[str, Any] = {
    "enabled": False,
    "sampleIntervalSeconds": 20,
    "injectHostAlerts": True,
    "maxInjectedProcesses": 3,
    "highCpuPercent": 85,
    "highMemoryPercent": 25,
    "highMemoryRssMb": 2048,
    "networkTunnelPolicy": "confirm_first",
    "knownNetworkTools": [],
    "knownListeningPorts": ["tcp:9527", "tcp:9528", "tcp:9530"],
}

_TUNNEL_KEYWORDS = (
    "wireguard",
    "wg",
    "openvpn",
    "clash",
    "tailscale",
    "zerotier",
    "sing-box",
    "singbox",
    "v2ray",
    "xray",
    "tun2socks",
)

_SUSPICIOUS_PROCESS_NAMES = (
    "mshta",
    "rundll32",
    "regsvr32",
    "certutil",
    "bitsadmin",
    "wscript",
    "cscript",
    "powershell",
    "pwsh",
)


def normalize_active_defense_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(raw or {})
    merged = dict(DEFAULT_ACTIVE_DEFENSE_CONFIG)
    merged["enabled"] = bool(data.get("enabled", merged["enabled"]))
    merged["injectHostAlerts"] = bool(data.get("injectHostAlerts", merged["injectHostAlerts"]))
    merged["networkTunnelPolicy"] = (
        str(data.get("networkTunnelPolicy") or merged["networkTunnelPolicy"]).strip()
        or merged["networkTunnelPolicy"]
    )
    for key in (
        "sampleIntervalSeconds",
        "maxInjectedProcesses",
        "highCpuPercent",
        "highMemoryPercent",
        "highMemoryRssMb",
    ):
        try:
            value = int(data.get(key, merged[key]))
        except Exception:
            value = int(merged[key])
        if key == "sampleIntervalSeconds":
            value = max(5, min(value, 300))
        elif key == "maxInjectedProcesses":
            value = max(1, min(value, 5))
        elif key in {"highCpuPercent", "highMemoryPercent"}:
            value = max(1, min(value, 100))
        elif key == "highMemoryRssMb":
            value = max(128, min(value, 262144))
        merged[key] = value
    merged["knownNetworkTools"] = sorted(
        {
            str(item or "").strip().lower()
            for item in list(data.get("knownNetworkTools") or [])
            if str(item or "").strip()
        }
    )
    merged["knownListeningPorts"] = sorted(
        {
            str(item or "").strip().lower()
            for item in list(data.get("knownListeningPorts") or merged["knownListeningPorts"])
            if str(item or "").strip()
        }
    )
    return merged


@dataclass
class _SampleState:
    last_sample_at: float = 0.0
    last_error: str = ""


class SafetyActiveDefenseMonitor:
    """Lightweight opt-in host sentinel.

    The monitor is intentionally passive: it records incidents and prompt hints,
    but never terminates processes, closes sockets, or wakes Supervisor by itself.
    """

    def __init__(self) -> None:
        self._state = _SampleState()
        self._incidents: dict[str, dict[str, Any]] = {}
        self._reported_ids: set[str] = set()

    def config(self) -> dict[str, Any]:
        raw = storage.get_safety_guardian_config().get("activeDefense")
        return normalize_active_defense_config(raw if isinstance(raw, dict) else {})

    def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        current = storage.get_safety_guardian_config()
        current["activeDefense"] = normalize_active_defense_config(config)
        storage.save_safety_guardian_config(current)
        return self.config()

    def _incident_id(self, risk_code: str, key: str) -> str:
        digest = hashlib.sha256(f"{risk_code}:{key}".encode("utf-8")).hexdigest()[:16]
        return f"ad_{digest}"

    def _upsert_incident(self, incident: dict[str, Any]) -> None:
        incident_id = str(incident.get("id") or "")
        if not incident_id:
            return
        now = time.time()
        previous = self._incidents.get(incident_id)
        if previous:
            incident = {
                **previous,
                **incident,
                "firstSeenAt": previous.get("firstSeenAt") or incident.get("firstSeenAt") or now,
                "lastSeenAt": now,
                "seenCount": int(previous.get("seenCount") or 1) + 1,
            }
        else:
            incident.setdefault("firstSeenAt", now)
            incident.setdefault("lastSeenAt", now)
            incident.setdefault("seenCount", 1)
            incident.setdefault("status", "active")
        self._incidents[incident_id] = incident
        if incident_id not in self._reported_ids:
            self._reported_ids.add(incident_id)
            self._log_incident(incident)

    def _log_incident(self, incident: dict[str, Any]) -> None:
        try:
            payload = {
                "subject": incident.get("summary"),
                "verdict": "audit",
                "reason": incident.get("summary"),
                "riskCode": incident.get("riskCode"),
                "governanceTarget": "host_active_defense",
                "posture": "observe_only",
                "details": incident,
                "metadata": {"source": "active_defense"},
            }
            audit_logger.log(
                source_type="SAFETY",
                action="active_defense_incident",
                status="WARNING",
                details=json.dumps(payload, ensure_ascii=False),
            )
        except Exception:
            return

    def _iter_processes(self) -> list[Any]:
        if psutil is None:
            return []
        try:
            return list(psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "memory_info", "status"]))
        except Exception:
            return []

    def _iter_connections(self) -> list[Any]:
        if psutil is None or not hasattr(psutil, "net_connections"):
            return []
        try:
            return list(psutil.net_connections(kind="inet"))
        except Exception:
            return []

    def _process_snapshot(self, proc: Any) -> dict[str, Any] | None:
        try:
            info = dict(getattr(proc, "info", {}) or {})
            pid = int(info.get("pid") or getattr(proc, "pid", 0) or 0)
            name = str(info.get("name") or getattr(proc, "name", lambda: "")() or "unknown")[:80]
            cpu = float(info.get("cpu_percent") or 0.0)
            mem_percent = float(info.get("memory_percent") or 0.0)
            rss_mb = 0.0
            memory_info = info.get("memory_info")
            if memory_info is not None:
                rss_mb = float(getattr(memory_info, "rss", 0) or 0) / (1024 * 1024)
            return {
                "pid": pid,
                "name": name,
                "nameLower": name.lower(),
                "cpuPercent": round(cpu, 1),
                "memoryPercent": round(mem_percent, 1),
                "rssMb": round(rss_mb, 1),
                "status": str(info.get("status") or ""),
            }
        except Exception:
            return None

    def sample(self, *, force: bool = False) -> dict[str, Any]:
        config = self.config()
        if not config.get("enabled"):
            return self.dashboard(sample=False)

        now = time.time()
        interval = int(config.get("sampleIntervalSeconds") or 20)
        if not force and self._state.last_sample_at and now - self._state.last_sample_at < interval:
            return self.dashboard(sample=False)

        self._state.last_sample_at = now
        self._state.last_error = ""
        try:
            known_tunnels = set(config.get("knownNetworkTools") or [])
            process_names_by_pid: dict[int, str] = {}
            for proc in self._iter_processes():
                snapshot = self._process_snapshot(proc)
                if not snapshot:
                    continue
                process_names_by_pid[int(snapshot["pid"])] = str(snapshot["name"])
                key = f"{snapshot['nameLower']}:{snapshot['pid']}"
                if (
                    snapshot["cpuPercent"] >= float(config.get("highCpuPercent") or 85)
                    or snapshot["memoryPercent"] >= float(config.get("highMemoryPercent") or 25)
                    or snapshot["rssMb"] >= float(config.get("highMemoryRssMb") or 2048)
                ):
                    incident_id = self._incident_id("high_resource_process", key)
                    self._upsert_incident(
                        {
                            "id": incident_id,
                            "riskCode": "high_resource_process",
                            "severity": "warning",
                            "summary": (
                                f"High load process: {snapshot['name']}({snapshot['pid']}) "
                                f"CPU {snapshot['cpuPercent']}%, Mem {snapshot['rssMb']}MB"
                            ),
                            "process": {
                                "pid": snapshot["pid"],
                                "name": snapshot["name"],
                                "cpuPercent": snapshot["cpuPercent"],
                                "memoryPercent": snapshot["memoryPercent"],
                                "rssMb": snapshot["rssMb"],
                            },
                        }
                    )
                if any(token in snapshot["nameLower"] for token in _SUSPICIOUS_PROCESS_NAMES):
                    incident_id = self._incident_id("suspicious_process_name", key)
                    self._upsert_incident(
                        {
                            "id": incident_id,
                            "riskCode": "suspicious_process_name",
                            "severity": "info",
                            "summary": f"Suspicious process name observed: {snapshot['name']}({snapshot['pid']})",
                            "process": {"pid": snapshot["pid"], "name": snapshot["name"]},
                        }
                    )
                tunnel_token = next((token for token in _TUNNEL_KEYWORDS if token in snapshot["nameLower"]), "")
                if tunnel_token and tunnel_token not in known_tunnels:
                    incident_id = self._incident_id("network_tunnel_first_seen", tunnel_token)
                    self._upsert_incident(
                        {
                            "id": incident_id,
                            "riskCode": "network_tunnel_first_seen",
                            "severity": "notice",
                            "summary": f"Network tunnel/proxy first seen: {snapshot['name']}",
                            "networkToolKey": tunnel_token,
                            "process": {"pid": snapshot["pid"], "name": snapshot["name"]},
                        }
                    )
            known_ports = set(config.get("knownListeningPorts") or [])
            for conn in self._iter_connections():
                status = str(getattr(conn, "status", "") or "").upper()
                if status != "LISTEN":
                    continue
                laddr = getattr(conn, "laddr", None)
                port = int(getattr(laddr, "port", 0) or (laddr[1] if isinstance(laddr, tuple) and len(laddr) > 1 else 0) or 0)
                if not port:
                    continue
                proto = "tcp"
                key = f"{proto}:{port}".lower()
                if key in known_ports:
                    continue
                pid = int(getattr(conn, "pid", 0) or 0)
                process_name = process_names_by_pid.get(pid, "unknown")
                incident_id = self._incident_id("unknown_listening_port", key)
                self._upsert_incident(
                    {
                        "id": incident_id,
                        "riskCode": "unknown_listening_port",
                        "severity": "notice",
                        "summary": f"Unknown listening port observed: {proto.upper()} {port} ({process_name})",
                        "listeningPortKey": key,
                        "connection": {
                            "direction": "listen",
                            "proto": proto,
                            "port": port,
                            "pid": pid,
                            "processName": process_name,
                        },
                    }
                )
        except Exception as exc:  # pragma: no cover - defensive
            self._state.last_error = str(exc)[:200]
        return self.dashboard(sample=False)

    def dashboard(self, *, sample: bool = True) -> dict[str, Any]:
        if sample:
            config = self.config()
            if config.get("enabled"):
                return self.sample(force=False)
        config = self.config()
        incidents = [
            incident
            for incident in self._incidents.values()
            if str(incident.get("status") or "active") not in {"ignored", "confirmed"}
        ]
        incidents.sort(key=lambda item: float(item.get("lastSeenAt") or 0), reverse=True)
        return {
            "enabled": bool(config.get("enabled")),
            "config": config,
            "status": "enabled" if config.get("enabled") else "disabled",
            "lastSampleAt": self._state.last_sample_at or None,
            "lastError": self._state.last_error or None,
            "incidents": incidents[:50],
            "knownNetworkTools": list(config.get("knownNetworkTools") or []),
            "knownListeningPorts": list(config.get("knownListeningPorts") or []),
            "summary": {
                "activeIncidents": len(incidents),
                "highLoad": sum(1 for item in incidents if item.get("riskCode") == "high_resource_process"),
                "networkTunnels": sum(1 for item in incidents if item.get("riskCode") == "network_tunnel_first_seen"),
                "unknownListeningPorts": sum(1 for item in incidents if item.get("riskCode") == "unknown_listening_port"),
            },
        }

    def ignore_incident(self, incident_id: str) -> dict[str, Any] | None:
        incident = self._incidents.get(incident_id)
        if not incident:
            return None
        incident["status"] = "ignored"
        incident["ignoredAt"] = time.time()
        return incident

    def confirm_incident(self, incident_id: str) -> dict[str, Any] | None:
        incident = self._incidents.get(incident_id)
        if not incident:
            return None
        incident["status"] = "confirmed"
        incident["confirmedAt"] = time.time()
        if incident.get("riskCode") == "network_tunnel_first_seen":
            tool_key = str(incident.get("networkToolKey") or "").strip().lower()
            if tool_key:
                config = self.config()
                known = {str(item).strip().lower() for item in config.get("knownNetworkTools", []) if str(item).strip()}
                known.add(tool_key)
                config["knownNetworkTools"] = sorted(known)
                self.save_config(config)
        if incident.get("riskCode") == "unknown_listening_port":
            port_key = str(incident.get("listeningPortKey") or "").strip().lower()
            if port_key:
                config = self.config()
                known_ports = {str(item).strip().lower() for item in config.get("knownListeningPorts", []) if str(item).strip()}
                known_ports.add(port_key)
                config["knownListeningPorts"] = sorted(known_ports)
                self.save_config(config)
        return incident

    def render_host_alerts_line(self) -> str:
        config = self.config()
        if not config.get("enabled") or not config.get("injectHostAlerts"):
            return ""
        snapshot = self.sample(force=False)
        incidents = list(snapshot.get("incidents") or [])
        if not incidents:
            return ""
        max_processes = int(config.get("maxInjectedProcesses") or 3)
        high_load = [
            item.get("process") or {}
            for item in incidents
            if item.get("riskCode") == "high_resource_process" and isinstance(item.get("process"), dict)
        ][:max_processes]
        chunks: list[str] = []
        if high_load:
            formatted = []
            for process in high_load:
                name = str(process.get("name") or "unknown")
                pid = process.get("pid")
                cpu = process.get("cpuPercent")
                mem = process.get("rssMb")
                formatted.append(f"{name}({pid}) CPU {cpu}%, Mem {mem}MB")
            chunks.append("High load: " + "; ".join(formatted))
        tunnel_count = sum(1 for item in incidents if item.get("riskCode") == "network_tunnel_first_seen")
        if tunnel_count:
            chunks.append(f"Unconfirmed tunnel/proxy: {tunnel_count}")
        listening_count = sum(1 for item in incidents if item.get("riskCode") == "unknown_listening_port")
        if listening_count:
            chunks.append(f"Unknown listening ports: {listening_count}")
        suspicious_count = sum(1 for item in incidents if item.get("riskCode") == "suspicious_process_name")
        if suspicious_count:
            chunks.append(f"Suspicious process names: {suspicious_count}")
        if not chunks:
            return ""
        return "Host Alerts: " + " | ".join(chunks[:3])


safety_active_defense_monitor = SafetyActiveDefenseMonitor()


def render_host_alerts_line() -> str:
    return safety_active_defense_monitor.render_host_alerts_line()
