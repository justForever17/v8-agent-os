from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import safety_active_defense
from core.safety_active_defense import SafetyActiveDefenseMonitor, normalize_active_defense_config


class _Proc:
    def __init__(self, pid: int, name: str, cpu: float = 0, memory_percent: float = 0, rss_mb: float = 0):
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": name,
            "cpu_percent": cpu,
            "memory_percent": memory_percent,
            "memory_info": SimpleNamespace(rss=int(rss_mb * 1024 * 1024)),
            "status": "running",
        }


class SafetyActiveDefenseTests(unittest.TestCase):
    def _monitor_with_config(self, config: dict) -> SafetyActiveDefenseMonitor:
        monitor = SafetyActiveDefenseMonitor()
        state = normalize_active_defense_config(config)
        monitor.config = lambda: state  # type: ignore[method-assign]

        def _save_config(next_config: dict) -> dict:
            copied = dict(next_config)
            state.clear()
            state.update(normalize_active_defense_config(copied))
            return state

        monitor.save_config = _save_config  # type: ignore[method-assign]
        return monitor

    def test_disabled_monitor_does_not_sample_processes(self):
        monitor = self._monitor_with_config({"enabled": False})
        fake_psutil = SimpleNamespace(process_iter=Mock(return_value=[_Proc(1, "python", cpu=99)]))
        with patch.object(safety_active_defense, "psutil", fake_psutil):
            payload = monitor.sample(force=True)

        fake_psutil.process_iter.assert_not_called()
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["summary"]["activeIncidents"], 0)

    def test_high_load_process_generates_incident_and_short_host_alert(self):
        monitor = self._monitor_with_config(
            {
                "enabled": True,
                "injectHostAlerts": True,
                "maxInjectedProcesses": 3,
                "sampleIntervalSeconds": 5,
                "highCpuPercent": 80,
                "highMemoryRssMb": 1024,
            }
        )
        fake_psutil = SimpleNamespace(
            process_iter=Mock(
                return_value=[
                    _Proc(1, "python", cpu=91, rss_mb=512),
                    _Proc(2, "chrome", cpu=3, rss_mb=4096),
                    _Proc(3, "node", cpu=88, rss_mb=256),
                    _Proc(4, "ffmpeg", cpu=96, rss_mb=256),
                ]
            )
        )

        with patch.object(safety_active_defense, "psutil", fake_psutil), patch.object(
            safety_active_defense.audit_logger,
            "log",
        ):
            line = monitor.render_host_alerts_line()

        self.assertTrue(line.startswith("Host Alerts: High load: "))
        self.assertEqual(line.count(" CPU "), 3)

    def test_network_tunnel_first_seen_can_be_confirmed_into_baseline(self):
        monitor = self._monitor_with_config(
            {
                "enabled": True,
                "knownNetworkTools": [],
            }
        )
        fake_psutil = SimpleNamespace(process_iter=Mock(return_value=[_Proc(77, "WireGuard.exe")]))

        with patch.object(safety_active_defense, "psutil", fake_psutil), patch.object(
            safety_active_defense.audit_logger,
            "log",
        ):
            payload = monitor.sample(force=True)

        incidents = payload["incidents"]
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["riskCode"], "network_tunnel_first_seen")

        confirmed = monitor.confirm_incident(incidents[0]["id"])
        self.assertIsNotNone(confirmed)
        self.assertIn("wireguard", monitor.config()["knownNetworkTools"])

        with patch.object(safety_active_defense, "psutil", fake_psutil):
            followup = monitor.sample(force=True)
        self.assertEqual(followup["summary"]["activeIncidents"], 0)

    def test_unknown_listening_port_can_be_confirmed_into_baseline(self):
        monitor = self._monitor_with_config(
            {
                "enabled": True,
                "knownListeningPorts": ["tcp:9527"],
            }
        )
        conn = SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(port=34567), pid=42)
        fake_psutil = SimpleNamespace(
            process_iter=Mock(return_value=[_Proc(42, "dev-server")]),
            net_connections=Mock(return_value=[conn]),
        )

        with patch.object(safety_active_defense, "psutil", fake_psutil), patch.object(
            safety_active_defense.audit_logger,
            "log",
        ):
            payload = monitor.sample(force=True)

        incidents = payload["incidents"]
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["riskCode"], "unknown_listening_port")
        monitor.confirm_incident(incidents[0]["id"])
        self.assertIn("tcp:34567", monitor.config()["knownListeningPorts"])

        with patch.object(safety_active_defense, "psutil", fake_psutil):
            followup = monitor.sample(force=True)
        self.assertEqual(followup["summary"]["activeIncidents"], 0)


if __name__ == "__main__":
    unittest.main()
