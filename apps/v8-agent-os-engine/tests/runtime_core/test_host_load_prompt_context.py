from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import host_load
from graph.agent_factories import _build_agent_system_bundle
from graph.supervisor_context import build_supervisor_system_content


class _MemoryRuntimeStub:
    def build_session_context(self, **_kwargs):  # noqa: ANN003
        return ""


class HostLoadPromptContextTests(unittest.TestCase):
    def setUp(self) -> None:
        host_load.clear_host_load_cache()

    def tearDown(self) -> None:
        host_load.clear_host_load_cache()

    def test_host_load_uses_psutil_and_gpu_probe_when_available(self):
        fake_psutil = SimpleNamespace(
            cpu_percent=Mock(return_value=12.2),
            virtual_memory=Mock(return_value=SimpleNamespace(percent=60.7)),
            pids=Mock(return_value=[1, 2, 3]),
        )
        completed = subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout="18\n42\n",
            stderr="",
        )

        with patch.object(host_load, "psutil", fake_psutil), patch("core.host_load.shutil.which", return_value="nvidia-smi"), patch(
            "core.host_load.subprocess.run",
            return_value=completed,
        ):
            line = host_load.render_host_load_line(use_cache=False)

        self.assertEqual(line, "Host Load: CPU 12%, Mem 61%, GPU 42%, Procs 3")

    def test_host_load_degrades_to_na_without_psutil_or_gpu(self):
        with patch.object(host_load, "psutil", None), patch("core.host_load.shutil.which", return_value=None):
            line = host_load.render_host_load_line(use_cache=False)

        self.assertEqual(line, "Host Load: CPU n/a, Mem n/a, GPU n/a, Procs n/a")

    def test_host_load_ttl_cache_avoids_repeated_gpu_probe(self):
        fake_psutil = SimpleNamespace(
            cpu_percent=Mock(return_value=1),
            virtual_memory=Mock(return_value=SimpleNamespace(percent=2)),
            pids=Mock(return_value=[1]),
        )
        completed = subprocess.CompletedProcess(args=["nvidia-smi"], returncode=0, stdout="3\n", stderr="")

        with patch.object(host_load, "psutil", fake_psutil), patch("core.host_load.shutil.which", return_value="nvidia-smi"), patch(
            "core.host_load.subprocess.run",
            return_value=completed,
        ) as run:
            first = host_load.render_host_load_line()
            second = host_load.render_host_load_line()

        self.assertEqual(first, second)
        run.assert_called_once()

    def test_supervisor_environment_includes_dynamic_host_load_segment(self):
        with patch("graph.supervisor_context.capability_registry.build_supervisor_summary", return_value=""), patch(
            "graph.supervisor_context._build_workspace_rules_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._build_artifact_awareness_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._render_engineering_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context.render_host_load_line",
            return_value="Host Load: CPU 12%, Mem 61%, GPU n/a, Procs 286",
        ), patch(
            "graph.supervisor_context.utc_now_iso",
            return_value="2026-04-30T00:00:00Z",
        ):
            result = build_supervisor_system_content(
                state={},
                config=SimpleNamespace(system_prompt="Base prompt."),
                user_query="hello",
                current_scope="global",
                scope_chain=["global"],
                session_id="sess_host_load",
                messages=[],
                loaded_agents=[],
                supervisor_tools=[],
                memory_runtime=_MemoryRuntimeStub(),
            )

        self.assertIn("Host Load: CPU 12%, Mem 61%, GPU n/a, Procs 286", result["env_context"])
        host_segments = [
            item
            for item in result["v8_prompt_segments"]
            if item.get("source") == "environment.host_load"
        ]
        self.assertEqual(len(host_segments), 1)
        self.assertEqual(host_segments[0]["type"], "dynamic")

    def test_supervisor_environment_includes_dynamic_host_alerts_segment_when_present(self):
        with patch("graph.supervisor_context.capability_registry.build_supervisor_summary", return_value=""), patch(
            "graph.supervisor_context._build_workspace_rules_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._build_artifact_awareness_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._render_engineering_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context.render_host_load_line",
            return_value="Host Load: CPU 12%, Mem 61%, GPU n/a, Procs 286",
        ), patch(
            "graph.supervisor_context.render_host_alerts_line",
            return_value="Host Alerts: High load: python(1234) CPU 91%, Mem 2048MB",
        ), patch(
            "graph.supervisor_context.utc_now_iso",
            return_value="2026-04-30T00:00:00Z",
        ):
            result = build_supervisor_system_content(
                state={},
                config=SimpleNamespace(system_prompt="Base prompt."),
                user_query="hello",
                current_scope="global",
                scope_chain=["global"],
                session_id="sess_host_alerts",
                messages=[],
                loaded_agents=[],
                supervisor_tools=[],
                memory_runtime=_MemoryRuntimeStub(),
            )

        self.assertIn("Host Alerts: High load: python(1234) CPU 91%, Mem 2048MB", result["env_context"])
        alert_segments = [
            item
            for item in result["v8_prompt_segments"]
            if item.get("source") == "environment.host_alerts"
        ]
        self.assertEqual(len(alert_segments), 1)
        self.assertEqual(alert_segments[0]["type"], "dynamic")

    def test_host_load_changes_do_not_change_static_environment_segment_hashes(self):
        common_patches = [
            patch("graph.supervisor_context.capability_registry.build_supervisor_summary", return_value=""),
            patch("graph.supervisor_context._build_workspace_rules_context", return_value=("", [])),
            patch("graph.supervisor_context._build_artifact_awareness_context", return_value=("", [])),
            patch("graph.supervisor_context._render_engineering_context", return_value=("", [])),
            patch("graph.supervisor_context.utc_now_iso", return_value="2026-04-30T00:00:00Z"),
        ]
        for item in common_patches:
            item.start()
            self.addCleanup(item.stop)

        with patch("graph.supervisor_context.render_host_load_line", return_value="Host Load: CPU 1%, Mem 2%, GPU n/a, Procs 3"):
            first = build_supervisor_system_content(
                state={},
                config=SimpleNamespace(system_prompt="Base prompt."),
                user_query="hello",
                current_scope="global",
                scope_chain=["global"],
                session_id="sess_host_load",
                messages=[],
                loaded_agents=[],
                supervisor_tools=[],
                memory_runtime=_MemoryRuntimeStub(),
            )
        with patch("graph.supervisor_context.render_host_load_line", return_value="Host Load: CPU 99%, Mem 88%, GPU 77%, Procs 666"):
            second = build_supervisor_system_content(
                state={},
                config=SimpleNamespace(system_prompt="Base prompt."),
                user_query="hello",
                current_scope="global",
                scope_chain=["global"],
                session_id="sess_host_load",
                messages=[],
                loaded_agents=[],
                supervisor_tools=[],
                memory_runtime=_MemoryRuntimeStub(),
            )

        def static_environment_hashes(payload: dict) -> list[str]:
            return [
                str(item.get("hash"))
                for item in payload["v8_prompt_segments"]
                if item.get("scope") == "environment" and item.get("type") == "scoped_static"
            ]

        self.assertEqual(static_environment_hashes(first), static_environment_hashes(second))

    def test_subagent_environment_marks_host_load_dynamic(self):
        bundle = _build_agent_system_bundle(
            agent_name="agent",
            agent_system_prompt="You are a test agent.",
            env_context=(
                "<environment>\n"
                "OS: Windows\n"
                "Current Time: 2026-04-30T00:00:00Z\n"
                "Host Load: CPU 12%, Mem 61%, GPU n/a, Procs 286\n"
                "Local Workspace Absolute Path: E:/Projects/v8chat\n"
                "</environment>\n"
            ),
        )

        self.assertIn("Host Load: CPU 12%, Mem 61%, GPU n/a, Procs 286", str(bundle["content"]))
        host_segments = [
            item
            for item in list(bundle.get("segments") or [])
            if item.get("source") == "subagent.environment.host_load"
        ]
        self.assertEqual(len(host_segments), 1)
        self.assertEqual(host_segments[0]["type"], "dynamic")

    def test_subagent_environment_marks_host_alerts_dynamic(self):
        bundle = _build_agent_system_bundle(
            agent_name="agent",
            agent_system_prompt="You are a test agent.",
            env_context=(
                "<environment>\n"
                "OS: Windows\n"
                "Current Time: 2026-04-30T00:00:00Z\n"
                "Host Load: CPU 12%, Mem 61%, GPU n/a, Procs 286\n"
                "Host Alerts: High load: python(1234) CPU 91%, Mem 2048MB\n"
                "Local Workspace Absolute Path: E:/Projects/v8chat\n"
                "</environment>\n"
            ),
        )

        self.assertIn("Host Alerts: High load: python(1234) CPU 91%, Mem 2048MB", str(bundle["content"]))
        alert_segments = [
            item
            for item in list(bundle.get("segments") or [])
            if item.get("source") == "subagent.environment.host_alerts"
        ]
        self.assertEqual(len(alert_segments), 1)
        self.assertEqual(alert_segments[0]["type"], "dynamic")


if __name__ == "__main__":
    unittest.main()
