from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.native_tools import command_session_broker, delegation_broker, run_system_command
from core.tools.s3_tools import s3_broker
from core.tools.web_fetcher import web_broker


class WebAndS3BrokerTests(unittest.TestCase):
    def test_web_broker_fetch_mode_dispatches_to_unified_web_fetch(self):
        with patch(
            "core.tools.web_fetcher.web_fetch.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "url": "https://example.com",
                    "finalUrl": "https://example.com/final",
                    "requestedMode": "auto",
                    "fetchMode": "dynamic",
                    "title": "Example",
                    "text": "hello world",
                    "attemptedModes": ["static", "dynamic"],
                    "adaptiveSignals": {"score": 0.9},
                },
                ensure_ascii=False,
            ),
        ) as mocked:
            result = web_broker.func(target="https://example.com", mode="fetch")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "read")
        self.assertEqual(payload["title"], "Example")
        self.assertNotIn("attemptedModes", payload)
        self.assertNotIn("adaptiveSignals", payload)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["intent"], "auto")
        self.assertEqual(mocked.call_args.kwargs["target"], "https://example.com")

    def test_web_broker_debug_mode_moves_transport_fields_under_debug(self):
        with patch(
            "core.tools.web_fetcher.web_fetch.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "query": "v8",
                    "provider": "bing",
                    "results": [{"title": "V8", "url": "https://example.com", "snippet": "demo"}],
                    "attemptedProviders": [{"provider": "bing", "status": "ok", "resultCount": 1}],
                    "searchUrl": "https://www.bing.com/search?q=v8",
                },
                ensure_ascii=False,
            ),
        ):
            result = web_broker.func(target="v8", mode="search", debug=True)

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "search")
        self.assertIn("debug", payload)
        self.assertEqual(payload["debug"]["searchUrl"], "https://www.bing.com/search?q=v8")

    def test_web_broker_read_mode_forces_read_intent(self):
        with patch("core.tools.web_fetcher.web_fetch.func", return_value='{"ok": true, "mode": "read"}') as mocked:
            result = web_broker.func(target="https://example.com/doc", mode="read", fetch_mode="dynamic")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(mocked.call_args.kwargs["intent"], "read")
        self.assertEqual(mocked.call_args.kwargs["mode"], "dynamic")

    def test_s3_broker_upload_mode_returns_structured_json(self):
        with patch(
            "core.tools.s3_tools.upload_file_to_s3",
            return_value={
                "bucket": "demo-bucket",
                "key": "demo.txt",
                "url": "https://cdn.example.com/demo.txt",
                "contentType": "text/plain",
                "size": 42,
            },
        ) as mocked:
            result = s3_broker.func(mode="upload", file_path="E:/tmp/demo.txt", key="demo.txt", prefix="demo")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "upload")
        self.assertEqual(payload["bucket"], "demo-bucket")
        self.assertEqual(payload["key"], "demo.txt")
        mocked.assert_called_once_with("E:/tmp/demo.txt", key="demo.txt", prefix="demo")

    def test_s3_broker_download_requires_destination(self):
        payload = json.loads(s3_broker.func(mode="download", key="demo.txt", destination_path=""))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "missing_destination_path")

    def test_run_system_command_auto_redirects_session_preferred_commands(self):
        payload = json.loads(run_system_command.func(command="npm run dev", mode="auto"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "command_session_redirect")
        self.assertEqual(payload["redirect"]["tool"], "command_session_broker")
        self.assertEqual(payload["redirect"]["args"]["mode"], "start")

    def test_command_session_broker_start_returns_process_link_contract(self):
        with patch(
            "core.native_tools._launch_background_command",
            return_value={
                "commandId": "cmd123",
                "mode": "interactive",
                "tty": "pty",
                "sessionId": "chat-session-1",
                "runId": "run-1",
                "status": {
                    "is_running": True,
                    "interactive": True,
                    "awaiting_input": False,
                    "observation_state": "busy",
                },
                "interactive": True,
                "profile": "chat_cli",
                "profileReason": "ai_cli_detected",
                "initialOutput": "Booting...",
            },
        ):
            payload = json.loads(command_session_broker.func(mode="start", command="qwen"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "start")
        self.assertEqual(payload["kind"], "command_session")
        self.assertEqual(payload["commandId"], "cmd123")
        self.assertEqual(payload["linkedProcess"]["processId"], "cmd123")
        self.assertEqual(payload["recommendedNextAction"], "observe")

    def test_delegation_broker_dispatch_starts_external_worker_session(self):
        descriptor = {
            "id": "coding-cli-worker",
            "name": "Coding CLI Worker",
            "description": "External coding worker",
            "enabled": True,
            "workerType": "coding_cli",
            "capabilitySnapshot": {
                "agentClass": "external_worker",
                "domainTags": ["software_engineering"],
                "operationCapabilities": ["implement"],
                "externalWorkerSuitability": "high",
            },
            "launchProfile": {
                "commandTemplate": "worker --task {task_brief_b64}",
                "cwdPolicy": "inherit_workspace",
                "envPassThrough": [],
                "startupTimeoutSeconds": 10,
            },
            "sessionMode": "interactive",
            "allowedSideEffects": ["workspace_write"],
            "resultSchema": {
                "type": "v8_worker_result_v1",
                "markers": ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"],
            },
        }

        with patch("core.native_tools.storage.get_all_agents", return_value=[]), patch(
            "core.native_tools.storage.get_supervisor_config",
            return_value={"delegation": {"externalWorkers": [descriptor]}},
        ), patch(
            "core.native_tools.command_session_broker.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "mode": "start",
                    "kind": "command_session",
                    "commandId": "cmd-ext-1",
                    "sessionId": "cmd-ext-1",
                    "runId": "run-ext-1",
                    "state": "running",
                    "summary": "worker started",
                    "recommendedNextAction": "observe",
                }
            ),
        ) as mocked_start:
            command = delegation_broker.func(
                mode="dispatch",
                tasks=[
                    {
                        "taskBriefId": "task-impl",
                        "goal": "Implement the requested patch",
                        "requiredCapabilities": ["software_engineering", "implement"],
                        "executionLaneHint": "external_worker",
                        "preferredWorkerType": "coding_cli",
                    }
                ],
                state={"run_id": "run-supervisor-1", "workspace_path": "E:/Projects/v8chat"},
            )

        payload = json.loads(command.update["messages"][0].content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "dispatch")
        self.assertEqual(payload["recommendedNextAction"], "observe")
        self.assertEqual(payload["items"][0]["lane"], "external_worker")
        self.assertEqual(payload["items"][0]["targetId"], "coding-cli-worker")
        self.assertEqual(payload["items"][0]["commandSession"]["commandId"], "cmd-ext-1")
        self.assertFalse(payload["items"][0]["resultSchemaMatched"])
        mocked_start.assert_called_once()
        self.assertEqual(mocked_start.call_args.kwargs["mode"], "start")

    def test_delegation_broker_observe_parses_worker_result_block(self):
        descriptor = {
            "id": "research-writer-worker",
            "name": "Research / Writing Worker",
            "description": "External research worker",
            "enabled": True,
            "workerType": "research_writer",
            "capabilitySnapshot": {
                "agentClass": "external_worker",
                "domainTags": ["research", "writing"],
                "operationCapabilities": ["research", "write"],
                "externalWorkerSuitability": "high",
            },
            "launchProfile": {
                "commandTemplate": "worker --task {task_brief_b64}",
                "cwdPolicy": "inherit_workspace",
                "envPassThrough": [],
                "startupTimeoutSeconds": 10,
            },
            "sessionMode": "interactive",
            "allowedSideEffects": ["workspace_write"],
            "resultSchema": {
                "type": "v8_worker_result_v1",
                "markers": ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"],
            },
        }
        result_block = (
            "<V8_WORKER_RESULT>"
            + json.dumps(
                {
                    "summary": "Draft completed",
                    "localSelfCheck": "Checked structure and evidence coverage.",
                    "artifactRefs": [{"kind": "file", "path": "E:/Projects/v8chat/out.md"}],
                    "acceptanceHint": "Review draft tone and references before publishing.",
                },
                ensure_ascii=False,
            )
            + "</V8_WORKER_RESULT>"
        )

        with patch(
            "core.native_tools.storage.get_supervisor_config",
            return_value={"delegation": {"externalWorkers": [descriptor]}},
        ), patch(
            "core.native_tools.command_session_broker.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "mode": "observe",
                    "kind": "command_session",
                    "commandId": "cmd-ext-2",
                    "sessionId": "cmd-ext-2",
                    "runId": "run-ext-2",
                    "state": "completed",
                    "summary": "worker finished",
                    "deltaText": result_block,
                    "recommendedNextAction": "none",
                }
            ),
        ):
            command = delegation_broker.func(
                mode="observe",
                delegation_id="external::cmd-ext-2::task-draft::research-writer-worker",
                state={"run_id": "run-supervisor-2"},
            )

        payload = json.loads(command.update["messages"][0].content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["items"][0]["lane"], "external_worker")
        self.assertEqual(payload["items"][0]["targetId"], "research-writer-worker")
        self.assertTrue(payload["items"][0]["resultSchemaMatched"])
        self.assertEqual(payload["items"][0]["localSelfCheck"], "Checked structure and evidence coverage.")
        self.assertEqual(payload["items"][0]["artifactRefs"][0]["path"], "E:/Projects/v8chat/out.md")
        self.assertEqual(payload["items"][0]["acceptanceHint"], "Review draft tone and references before publishing.")


if __name__ == "__main__":
    unittest.main()
