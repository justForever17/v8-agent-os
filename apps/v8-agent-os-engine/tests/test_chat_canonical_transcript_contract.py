from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from api.models import ChatRequest
from core.computer_use_tool_surface import select_supervisor_native_tools
from core.system_tools.baseline import BASELINE_SYSTEM_TOOL_NAMES
from erc import chat_canonical_transcript as transcript_module
from erc.chat_canonical_transcript import CanonicalTranscriptBuilder
from erc.canonical_model_events import LangChainCanonicalModelEventAdapter, consume_canonical_stream_value
from erc.snapshot_service import SnapshotService
import erc.snapshot_service as snapshot_service_module
import erc.session_realtime_contract as session_realtime_contract_module


class _FakeCanonicalDb:
    def __init__(self) -> None:
        self.messages: dict[str, dict] = {}
        self.artifacts: list[dict] = []

    def create_chat_canonical_message(self, **kwargs):
        self.messages[kwargs["message_id"]] = {
            "id": kwargs["message_id"],
            "session_id": kwargs["session_id"],
            "run_id": kwargs.get("run_id"),
            "ordinal": kwargs["ordinal"],
            "role": kwargs["role"],
            "state": kwargs["state"],
            "nodes": list(kwargs.get("nodes") or []),
            "artifacts": list(kwargs.get("artifacts") or []),
            "content_text": kwargs.get("content_text") or "",
            "reasoning_text": kwargs.get("reasoning_text"),
            "metadata": dict(kwargs.get("metadata") or {}),
            "version": 1,
            "created_at": "2026-04-15T00:00:00Z",
            "updated_at": "2026-04-15T00:00:00Z",
            "finalized_at": None,
        }

    def update_chat_canonical_message(self, message_id: str, **kwargs):
        row = self.messages[message_id]
        for key, value in kwargs.items():
            if key == "metadata":
                row["metadata"] = dict(value or {})
            elif key == "nodes":
                row["nodes"] = list(value or [])
            elif key == "artifacts":
                row["artifacts"] = list(value or [])
            elif key == "state":
                row["state"] = value
            elif key == "content_text":
                row["content_text"] = value or ""
            elif key == "reasoning_text":
                row["reasoning_text"] = value
            elif key == "finalized_at":
                row["finalized_at"] = value
        row["version"] = int(row.get("version") or 0) + 1
        return dict(row)

    def get_chat_canonical_message(self, message_id: str):
        row = self.messages.get(message_id)
        return dict(row) if row else None

    def get_chat_canonical_messages(self, session_id: str):
        rows = [dict(row) for row in self.messages.values() if row.get("session_id") == session_id]
        return sorted(rows, key=lambda row: int(row.get("ordinal") or 0))

    def list_runtime_artifacts(self, *, session_id: str, limit: int = 1000):
        return [dict(item) for item in self.artifacts if item.get("session_id") == session_id][:limit]


class _FakeSnapshotDb:
    def __init__(self) -> None:
        self.latest_seq = 0
        self.canonical_version = 0
        self.legacy_messages: list[dict] = []
        self.runtime_events: list[dict] = []
        self.snapshots: list[dict] = []

    def get_latest_runtime_seq(self, session_id: str):
        return self.latest_seq

    def get_chat_canonical_max_version(self, session_id: str):
        return self.canonical_version

    def get_messages(self, session_id: str):
        return list(self.legacy_messages)

    def get_runtime_events(self, session_id: str):
        return list(self.runtime_events)

    def add_runtime_snapshot(self, *, snapshot_id: str, session_id: str, run_id=None, latest_seq=None, snapshot_type: str, snapshot: dict):
        self.snapshots.append(
            {
                "id": snapshot_id,
                "session_id": session_id,
                "run_id": run_id,
                "latest_seq": latest_seq,
                "snapshot_type": snapshot_type,
                "snapshot": dict(snapshot),
            }
        )

    def get_latest_runtime_snapshot(self, session_id: str, snapshot_type: str):
        for row in reversed(self.snapshots):
            if row.get("session_id") == session_id and row.get("snapshot_type") == snapshot_type:
                return dict(row)
        return None


class _FakeProcessSurfaceDb:
    def get_chat_canonical_message_by_run(self, *, session_id: str, run_id: str, role: str = "assistant"):
        return {
            "id": "assistant-process-1",
            "session_id": session_id,
            "run_id": run_id,
            "role": role,
        }


class ChatCanonicalTranscriptContractTests(unittest.TestCase):
    def test_canonical_model_event_reasoning_accepts_token_deltas(self):
        snapshots: dict[str, str] = {}

        first_delta, first_snapshot = consume_canonical_stream_value(
            snapshots,
            "model-a",
            "用户",
            allow_token_delta=True,
        )
        second_delta, second_snapshot = consume_canonical_stream_value(
            snapshots,
            "model-a",
            "正在查询",
            allow_token_delta=True,
        )

        self.assertEqual(first_delta, "用户")
        self.assertEqual(first_snapshot, "用户")
        self.assertEqual(second_delta, "正在查询")
        self.assertEqual(second_snapshot, "用户正在查询")
        self.assertEqual(snapshots["model-a"], "用户正在查询")

    def test_canonical_model_event_text_rejects_non_monotonic_stream_replay(self):
        snapshots: dict[str, str] = {}

        first_delta, first_snapshot = consume_canonical_stream_value(
            snapshots,
            "model-a",
            "已找到文件：",
            allow_token_delta=False,
        )
        dirty_delta, dirty_snapshot = consume_canonical_stream_value(
            snapshots,
            "model-a",
            "C:\\Usersuny\\.v8",
            allow_token_delta=False,
        )

        self.assertEqual(first_delta, "已找到文件：")
        self.assertEqual(first_snapshot, "已找到文件：")
        self.assertEqual(dirty_delta, "")
        self.assertEqual(dirty_snapshot, "已找到文件：")
        self.assertEqual(snapshots["model-a"], "已找到文件：")

    def test_model_event_adapter_classifies_langgraph_tool_nodes_as_internal(self):
        adapter = LangChainCanonicalModelEventAdapter()

        self.assertEqual(
            adapter.scope_for_event({"metadata": {"langgraph_node": "supervisor_tools"}}),
            "tool_internal",
        )
        self.assertEqual(
            adapter.scope_for_event({"metadata": {"langgraph_path": ("__pregel_pull", "supervisor_tools")}}),
            "tool_internal",
        )
        self.assertEqual(
            adapter.scope_for_event({"metadata": {"langgraph_node": "supervisor"}}),
            "assistant_root",
        )

    def test_current_run_view_uses_keyword_only_canonical_lookup(self):
        run_record = {
            "id": "run-process",
            "session_id": "session-process",
            "status": "running",
            "metadata": {},
        }

        with mock.patch.object(session_realtime_contract_module, "db", _FakeProcessSurfaceDb()):
            view = session_realtime_contract_module.build_current_run_view(run_record)

        self.assertEqual(view["messageId"], "assistant-process-1")
        self.assertEqual(view["status"], "running")

    def test_chat_request_accepts_structured_attachments_and_legacy_file_urls(self):
        request = ChatRequest.model_validate(
            {
                "messages": [{"role": "user", "content": ""}],
                "clientMessageId": "user-client-1",
                "fileUrls": ["https://example.test/a.png"],
                "attachments": [
                    {
                        "name": "b.png",
                        "publicUrl": "https://example.test/b.png",
                        "mimeType": "image/png",
                        "size": 123,
                    }
                ],
                "data": {
                    "clientMessageId": "user-client-1",
                    "fileUrls": ["https://example.test/c.png"],
                    "attachments": [{"url": "https://example.test/d.png", "source": "web"}],
                },
            }
        )

        self.assertEqual(request.client_message_id, "user-client-1")
        self.assertEqual(request.data.client_message_id, "user-client-1")
        self.assertEqual(request.attachments[0].public_url, "https://example.test/b.png")
        self.assertEqual(request.data.attachments[0].url, "https://example.test/d.png")
        self.assertEqual(request.data.fileUrls, ["https://example.test/c.png"])

    def test_baseline_tools_expose_s3_and_hide_list_native_directory(self):
        self.assertIn("ask_user", BASELINE_SYSTEM_TOOL_NAMES)
        self.assertNotIn("list_native_directory", BASELINE_SYSTEM_TOOL_NAMES)
        self.assertIn("s3_upload_file", BASELINE_SYSTEM_TOOL_NAMES)
        self.assertIn("s3_list_objects", BASELINE_SYSTEM_TOOL_NAMES)
        self.assertIn("s3_download_file", BASELINE_SYSTEM_TOOL_NAMES)

    def test_ask_user_survives_supervisor_default_tool_filter(self):
        class _Tool:
            def __init__(self, name: str):
                self.name = name

        selected = select_supervisor_native_tools(
            filtered_native_tools=[_Tool("ask_user")],
            supervisor_allowed_tools=None,
            config_allowed_tools=None,
        )

        self.assertEqual([tool.name for tool in selected], ["ask_user"])

    def test_process_message_index_ignores_command_tool_without_command_session(self):
        snapshot = {
            "session_id": "session-process",
            "messages": [
                {
                    "id": "assistant-process",
                    "runId": "run-process",
                    "nodes": [
                        {
                            "id": "assistant-process:tool_call:1",
                            "kind": "execution",
                            "executionType": "tool_call",
                            "toolName": "run_system_command",
                            "toolCallId": "tool-call-1",
                            "args": {"command": "dir"},
                            "result": None,
                        }
                    ],
                }
            ],
        }

        self.assertEqual(session_realtime_contract_module._build_process_message_index(snapshot), {})

    def test_builder_preserves_stable_node_timeline_and_derived_exports(self):
        fake_db = _FakeCanonicalDb()
        builder = CanonicalTranscriptBuilder()

        with mock.patch.object(transcript_module, "db", fake_db):
            builder.create_message(
                message_id="user-1",
                session_id="session-canonical",
                run_id="run-canonical",
                ordinal=1,
                role="user",
                state="completed",
                nodes=[{"id": "user-1:narrative", "kind": "narrative", "role": "user", "content": "生成一张图"}],
                metadata={"timestamp": 1},
            )
            builder.create_message(
                message_id="assistant-1",
                session_id="session-canonical",
                run_id="run-canonical",
                ordinal=2,
                role="assistant",
                state="streaming",
                nodes=[{"id": "assistant-1:start", "kind": "execution", "executionType": "agent_start"}],
                metadata={"timestamp": 2, "agentName": "智能主管"},
            )

            def append(node):
                builder.mutate_message(
                    "assistant-1",
                    lambda nodes, _metadata: ([*nodes, node], node["id"]),
                    state="streaming",
                )

            append({"id": "assistant-1:reasoning:model-a", "kind": "execution", "executionType": "reasoning", "content": "先想"})
            append({"id": "assistant-1:text:model-a", "kind": "narrative", "role": "assistant", "content": "开始"})
            append(
                {
                    "id": "assistant-1:tool_call:tool-1",
                    "kind": "execution",
                    "executionType": "tool_call",
                    "toolCallId": "tool-1",
                    "toolName": "generate_image",
                    "args": {"params": {"prompt": "雷电将军"}},
                }
            )
            append(
                {
                    "id": "assistant-1:tool_result:tool-1",
                    "kind": "execution",
                    "executionType": "tool_result",
                    "toolCallId": "tool-1",
                    "toolName": "generate_image",
                    "result": {"url": "https://example.test/image.jpeg"},
                }
            )
            append({"id": "assistant-1:reasoning:model-b", "kind": "execution", "executionType": "reasoning", "content": "再想"})
            append({"id": "assistant-1:text:model-b", "kind": "narrative", "role": "assistant", "content": "完成"})
            builder.set_message_state("assistant-1", state="completed", finalize=True)
            fake_db.artifacts.append(
                {
                    "id": "artifact-1",
                    "session_id": "session-canonical",
                    "message_id": "assistant-1",
                    "artifact_kind": "image",
                    "previewUrl": "https://example.test/image.jpeg",
                }
            )

            messages = transcript_module.build_canonical_chat_messages("session-canonical")
            legacy = transcript_module.export_legacy_message_payload(fake_db.get_chat_canonical_message("assistant-1"))

        self.assertEqual([message["id"] for message in messages], ["user-1", "assistant-1"])
        assistant = messages[1]
        self.assertEqual(assistant["content"], "开始完成")
        self.assertEqual(assistant["reasoningContent"], "先想再想")
        self.assertEqual(
            [node.get("executionType") or node.get("kind") for node in assistant["nodes"]],
            ["agent_start", "reasoning", "narrative", "tool_call", "tool_result", "reasoning", "narrative", "artifact"],
        )
        self.assertEqual(assistant["toolInvocations"][0]["result"], {"url": "https://example.test/image.jpeg"})
        self.assertEqual(legacy["content"], "开始完成")
        self.assertEqual(legacy["reasoning_content"], "先想再想")

    def test_snapshot_refresh_uses_canonical_version_even_without_new_runtime_seq(self):
        fake_db = _FakeSnapshotDb()
        service = SnapshotService()
        canonical_messages = [
            {
                "id": "assistant-1",
                "role": "assistant",
                "runId": "run-1",
                "state": "completed",
                "version": 1,
                "content": "完整正文",
                "nodes": [{"id": "assistant-1:text", "kind": "narrative", "role": "assistant", "content": "完整正文"}],
            }
        ]

        with mock.patch.object(snapshot_service_module, "db", fake_db), mock.patch.object(
            snapshot_service_module,
            "build_canonical_chat_messages",
            return_value=canonical_messages,
        ):
            fake_db.latest_seq = 10
            fake_db.canonical_version = 1
            first = service.ensure_chat_projection_row("session-snapshot")
            self.assertEqual(first["snapshot"]["canonicalVersion"], 1)
            self.assertEqual(first["snapshot"]["messages"][0]["content"], "完整正文")

            fake_db.canonical_version = 2
            second = service.ensure_chat_projection_row("session-snapshot")

        self.assertEqual(second["snapshot"]["canonicalVersion"], 2)
        self.assertEqual(len(fake_db.snapshots), 2)

    def test_legacy_only_chat_fails_closed_instead_of_rebuilding_from_events(self):
        fake_db = _FakeSnapshotDb()
        fake_db.latest_seq = 7
        fake_db.canonical_version = 0
        fake_db.legacy_messages = [{"id": "legacy", "role": "assistant", "content": "旧正文"}]
        service = SnapshotService()

        with mock.patch.object(snapshot_service_module, "db", fake_db), mock.patch.object(
            snapshot_service_module,
            "build_canonical_chat_messages",
            return_value=[],
        ):
            snapshot = service.refresh_chat_projection("legacy-session")

        self.assertTrue(snapshot["legacyChatUnsupported"])
        self.assertEqual(snapshot["messages"], [])
        self.assertEqual(snapshot["latest_seq"], 7)


if __name__ == "__main__":
    unittest.main()
