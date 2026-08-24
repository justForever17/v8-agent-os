from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from langchain_core.messages import AIMessage

import runtimes.chat.runtime as chat_runtime_module
import erc.chat_canonical_transcript as transcript_module
from core.database import DatabaseManager
from erc.runtime_context import bind_runtime_context
from runtimes.chat.runtime import ChatRuntime, ChatStreamState


class FakeChatRun:
    def __init__(self) -> None:
        self.active_run_id = None
        self.session_id = f"session_test_{uuid.uuid4().hex}"
        self.transport = "chat"
        self.request = SimpleNamespace(attachments=[], messages=[])
        binding = SimpleNamespace(
            project_id=None,
            workspace_id=None,
            workspace_path=None,
            resolved_scope="global",
            scope_source="test",
        )
        self.scope_result = SimpleNamespace(binding=binding, scope_chain=["global"])
        self.events: list[dict] = []
        self.run_handle = SimpleNamespace(refresh_chat_snapshot=lambda: None)

    def emit_runtime_event(self, topic: str, payload: dict, **kwargs):
        event = {"topic": topic, "payload": payload, **kwargs}
        self.events.append(event)
        return event


class ChatTranscriptCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.test_db = DatabaseManager(Path(self.temp_dir.name) / "state.db")
        self.runtime_db_patch = mock.patch.object(chat_runtime_module, "db", self.test_db)
        self.runtime_db_patch.start()
        self.addCleanup(self.runtime_db_patch.stop)
        self.transcript_db_patch = mock.patch.object(transcript_module, "db", self.test_db)
        self.transcript_db_patch.start()
        self.addCleanup(self.transcript_db_patch.stop)
        self.runtime = ChatRuntime()
        self.runtime._get_agent_profile = lambda _node: {"name": "智能主管", "avatar": "", "roleLabel": "主理人"}
        self.chat_run = FakeChatRun()
        chat_runtime_module.db.create_or_update_session(
            session_id=self.chat_run.session_id,
            title="canonical transcript cleanup test",
            user_id="test",
        )
        self.stream_state = ChatStreamState()
        self.workflow_patch = mock.patch.object(
            chat_runtime_module.workflow_ledger_service,
            "append_chat_projection",
            new=lambda **_kwargs: None,
        )
        self.workflow_patch.start()
        self.addCleanup(self.workflow_patch.stop)

    def test_final_state_extractor_never_promotes_think_only_content(self):
        think_only = {"messages": [AIMessage(content="<think>private reasoning</think>")]}
        mixed = {"messages": [AIMessage(content="<think>private reasoning</think>Visible answer")]}

        self.assertEqual(self.runtime._extract_final_assistant_text_from_state(think_only), "")
        self.assertEqual(self.runtime._extract_final_assistant_text_from_state(mixed), "Visible answer")

    def test_final_state_extractor_skips_subagent_owned_messages(self):
        child = AIMessage(
            content="child output must stay in the delegation handoff",
            additional_kwargs={
                "v8_owner_runtime_kind": "subagent",
                "v8_owner_agent_kind": "subagent",
                "v8_owner_agent_id": "worker-one",
            },
        )
        supervisor = AIMessage(content="supervisor accepted the handoff")

        self.assertEqual(
            self.runtime._extract_final_assistant_text_from_state({"messages": [supervisor, child]}),
            "supervisor accepted the handoff",
        )

    async def test_callback_metadata_keeps_subagent_text_out_of_supervisor_canonical_message(self):
        self.stream_state.valid_agent_node_names = ["supervisor", "worker-one"]

        emitted = await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_stream",
                "run_id": "model_child",
                "name": "V8ChatModelAdapter",
                "metadata": {
                    "langgraph_node": "parallel_delegate_task",
                    "v8_owner_runtime_kind": "subagent",
                    "v8_owner_agent_id": "worker-one",
                    "v8_owner_subagent_id": "worker-one",
                    "v8_owner_delegation_id": "delegation-one",
                },
                "data": {"chunk": "child-only narrative"},
            },
        )

        self.assertEqual("".join(self.stream_state.output_buffer), "")
        self.assertEqual(emitted[0]["ownerRuntimeId"], "subagent_swarm")
        self.assertEqual(emitted[0]["ownerAgentId"], "worker-one")
        self.assertFalse(emitted[0]["displayInMessage"])
        self.assertEqual(self.chat_run.events[-1]["topic"], "subagent.text.delta")
        self.assertIsNone(self.stream_state.assistant_message_id)

    async def test_subagent_model_stream_uses_its_own_reasoning_contract_and_owner(self):
        self.stream_state.valid_agent_node_names = ["supervisor", "worker-one"]
        self.stream_state.reasoning_surface_contract = {"mode": "hidden", "trust": "unknown"}
        minimax_surface = {
            "mode": "provider_reasoning",
            "trust": "official",
            "requestStyle": "minimax_interleaved_thinking",
            "responseFields": [
                "reasoning_details",
                "additional_kwargs.reasoning_details",
            ],
            "displayKind": "provider_reasoning",
        }

        with mock.patch.object(
            chat_runtime_module.llm_factory,
            "get_model_metadata",
            return_value={"reasoning_surface": minimax_surface},
        ) as metadata_lookup:
            emitted = await self.runtime.handle_stream_event(
                self.chat_run,
                self.stream_state,
                {
                    "event": "on_chat_model_stream",
                    "run_id": "model_child_reasoning",
                    "name": "V8ChatModelAdapter",
                    "metadata": {
                        "langgraph_node": "worker-one",
                        "v8_owner_runtime_kind": "subagent",
                        "v8_owner_agent_id": "worker-one",
                        "v8_owner_subagent_id": "worker-one",
                        "v8_owner_delegation_id": "delegation-one",
                    },
                    "data": {
                        "chunk": {
                            "additional_kwargs": {
                                "reasoning_details": [
                                    {"type": "reasoning.text", "text": "先核对委派边界。"}
                                ]
                            },
                            "response_metadata": {
                                "v8_model_ref": "minimax-cn::MiniMax-M3",
                                "v8_model_id": "MiniMax-M3",
                            },
                        }
                    },
                },
            )

        metadata_lookup.assert_called_once_with("minimax-cn::MiniMax-M3")
        topics = [event["topic"] for event in self.chat_run.events]
        self.assertIn("subagent.reasoning.delta", topics)
        self.assertNotIn("run.reasoning.delta", topics)
        self.assertEqual(self.stream_state.reasoning_buffer, [])
        self.assertIsNone(self.stream_state.assistant_message_id)
        self.assertEqual(emitted[0]["ownerRuntimeId"], "subagent_swarm")
        self.assertEqual(emitted[0]["ownerAgentId"], "worker-one")
        self.assertFalse(emitted[0]["displayInMessage"])
        self.assertEqual(self.chat_run.events[-1]["payload"]["snapshot"], "先核对委派边界。")

    def test_unknown_explicit_subagent_model_ref_does_not_inherit_supervisor_reasoning_surface(self):
        self.stream_state.reasoning_surface_contract = {
            "mode": "provider_reasoning",
            "trust": "official",
            "responseFields": ["reasoning_details"],
        }
        event = {
            "event": "on_chat_model_stream",
            "run_id": "unknown-child-model-run",
            "data": {
                "chunk": {
                    "response_metadata": {
                        "v8_model_ref": "other-provider::unknown-model",
                    }
                }
            },
        }

        with mock.patch.object(
            chat_runtime_module.llm_factory,
            "get_model_metadata",
            side_effect=KeyError("unknown model"),
        ):
            surface = self.runtime._reasoning_surface_for_event(self.stream_state, event)

        self.assertEqual(surface, {})
        self.assertEqual(
            self.stream_state.reasoning_surfaces_by_run["unknown-child-model-run"],
            {},
        )

    async def test_provider_internal_model_event_cannot_create_a_second_assistant_stream(self):
        emitted = await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_end",
                "run_id": "provider-inner-run",
                "name": "ChatOpenAI",
                "metadata": {
                    "langgraph_node": "parallel_delegate_task",
                    "v8_model_scope": "runtime_internal",
                },
                "tags": ["v8:provider-internal"],
                "data": {"output": {"content": "provider duplicate must stay hidden"}},
            },
        )

        self.assertEqual(emitted, [])
        self.assertEqual(self.stream_state.output_buffer, [])
        self.assertIsNone(self.stream_state.assistant_message_id)

    def test_supervisor_delegation_broker_call_remains_message_owned(self):
        owner = self.runtime._resolve_event_owner(
            self.stream_state,
            tool_name="delegation_broker",
            event_metadata={"langgraph_node": "supervisor_tools"},
        )

        self.assertEqual(owner["ownerRuntimeId"], "chat")
        self.assertEqual(owner["ownerAgentKind"], "supervisor")
        self.assertEqual(owner["ownerAgentId"], "supervisor")
        self.assertTrue(owner["displayInMessage"])

    async def test_non_monotonic_stream_text_is_not_hard_appended_and_terminal_text_stays_clean(self):
        prefix = "已在工作区中找到3张JPEG格式的图片，本地路径如下：\n"
        dirty_fragment = "文件：\n1. `C:\\Usersuny\\.v8"
        clean_final = (
            "已在工作区中找到3张JPEG格式的图片，本地路径如下：\n"
            "1. `C:\\Users\\sunny\\.v8-agent-os\\workspace\\downloaded_media\\image-1.jpeg`\n"
            "2. `C:\\Users\\sunny\\.v8-agent-os\\workspace\\downloaded_media\\image-2.jpeg`\n"
            "3. `C:\\Users\\sunny\\.v8-agent-os\\workspace\\downloaded_media\\image-3.jpeg`\n"
            "\n你可以直接访问这些路径使用。"
        )

        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_stream",
                "run_id": "model_text",
                "name": "V8ChatModelAdapter",
                "data": {"chunk": prefix},
            },
        )
        self.assertEqual("".join(self.stream_state.output_buffer), prefix)

        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_stream",
                "run_id": "model_text",
                "name": "V8ChatModelAdapter",
                "data": {"chunk": dirty_fragment},
            },
        )
        self.assertEqual("".join(self.stream_state.output_buffer), prefix)

        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_end",
                "run_id": "model_text",
                "name": "V8ChatModelAdapter",
                "data": {"output": {"content": clean_final}},
            },
        )

        self.assertEqual("".join(self.stream_state.output_buffer), clean_final)
        self.assertEqual(self.stream_state.authoritative_final_text, clean_final)
        self.assertNotIn("Usersuny", "".join(self.stream_state.output_buffer))
        self.assertEqual("".join(self.stream_state.output_buffer).count("已在工作区中找到3张JPEG格式的图片"), 1)

    async def test_terminal_reasoning_is_suppressed_after_narrative_started(self):
        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_stream",
                "run_id": "model_text",
                "name": "V8ChatModelAdapter",
                "data": {"chunk": "这是正文。"},
            },
        )

        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_end",
                "run_id": "model_text",
                "name": "V8ChatModelAdapter",
                "data": {"output": {"content": "这是正文。", "reasoning_content": "这是尾随思考，不该再出现。"}},
            },
        )

        topics = [event["topic"] for event in self.chat_run.events]
        self.assertNotIn("run.reasoning.delta", topics)
        self.assertEqual(self.stream_state.reasoning_buffer, [])

    async def test_unverified_reasoning_only_terminal_response_is_suppressed(self):
        private_reasoning = "只有思考，没有正文。"
        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_end",
                "run_id": "model_reasoning",
                "name": "V8ChatModelAdapter",
                "data": {"output": {"reasoning_content": private_reasoning}},
            },
        )

        topics = [event["topic"] for event in self.chat_run.events]
        self.assertNotIn("run.text.delta", topics)
        self.assertIn("run.reasoning.suppressed", topics)
        self.assertNotIn("run.reasoning.delta", topics)
        self.assertEqual(self.stream_state.reasoning_buffer, [])
        self.assertEqual("".join(self.stream_state.output_buffer), "")
        suppressed = next(
            event["payload"] for event in self.chat_run.events if event["topic"] == "run.reasoning.suppressed"
        )
        self.assertNotIn("preview", suppressed)
        self.assertNotIn(private_reasoning, str(suppressed))
        self.assertEqual(suppressed["contentLength"], len(private_reasoning))
        self.assertEqual(
            suppressed["contentSha256"],
            hashlib.sha256(private_reasoning.encode("utf-8")).hexdigest(),
        )

    async def test_explicit_reasoning_block_still_keeps_reasoning(self):
        self.stream_state.reasoning_surface_contract = {
            "mode": "typed_thinking",
            "trust": "official",
            "requestStyle": "anthropic_thinking",
            "responseFields": ["content[type=thinking]"],
            "displayKind": "raw_thinking",
        }
        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_stream",
                "run_id": "model_reasoning_block",
                "name": "V8ChatModelAdapter",
                "data": {"chunk": {"content": [{"type": "thinking", "text": "这是可信思考。"}]}},
            },
        )

        topics = [event["topic"] for event in self.chat_run.events]
        self.assertIn("run.reasoning.delta", topics)
        self.assertEqual("".join(self.stream_state.reasoning_buffer), "这是可信思考。")

    async def test_unverified_reasoning_token_deltas_are_suppressed(self):
        for token in ["用户", "正在", "查询。"]:
            await self.runtime.handle_stream_event(
                self.chat_run,
                self.stream_state,
                {
                    "event": "on_chat_model_stream",
                    "run_id": "model_reasoning_tokens",
                    "name": "V8ChatModelAdapter",
                    "data": {"chunk": {"additional_kwargs": {"reasoning_content": token}}},
                },
            )

        self.assertEqual(self.stream_state.reasoning_buffer, [])
        self.assertEqual("".join(self.stream_state.output_buffer), "")
        topics = [event["topic"] for event in self.chat_run.events]
        self.assertNotIn("run.text.delta", topics)
        self.assertIn("run.reasoning.suppressed", topics)
        self.assertNotIn("run.reasoning.delta", topics)
        suppressed_payloads = [
            event["payload"] for event in self.chat_run.events if event["topic"] == "run.reasoning.suppressed"
        ]
        self.assertTrue(suppressed_payloads)
        self.assertTrue(all("preview" not in payload for payload in suppressed_payloads))
        self.assertNotIn("用户", str(suppressed_payloads))
        self.assertNotIn("正在", str(suppressed_payloads))
        self.assertNotIn("查询", str(suppressed_payloads))

    async def test_reasoning_token_deltas_preserve_provider_spacing_and_paragraphs(self):
        self.stream_state.reasoning_surface_contract = {
            "mode": "provider_reasoning",
            "trust": "adapter_verified",
            "requestStyle": "openai_compatible",
            "responseFields": ["additional_kwargs.reasoning_content"],
            "displayKind": "provider_reasoning",
        }
        for token in ["I need", " to inspect", " the workspace.\n", "下一步读取文件。"]:
            await self.runtime.handle_stream_event(
                self.chat_run,
                self.stream_state,
                {
                    "event": "on_chat_model_stream",
                    "run_id": "model_reasoning_spacing",
                    "name": "V8ChatModelAdapter",
                    "data": {"chunk": {"additional_kwargs": {"reasoning_content": token}}},
                },
            )
        await self.runtime.flush_stream_state(self.chat_run, self.stream_state)

        expected = "I need to inspect the workspace.\n下一步读取文件。"
        self.assertEqual("".join(self.stream_state.reasoning_buffer), expected)
        reasoning_events = [
            event for event in self.chat_run.events
            if event["topic"] == "run.reasoning.delta"
        ]
        self.assertEqual("".join(str(event["payload"].get("content") or "") for event in reasoning_events), expected)
        self.assertLess(len(reasoning_events), 4)
        diagnostics = next(
            event["payload"]
            for event in self.chat_run.events
            if event["topic"] == "run.reasoning_stream.diagnostics"
        )
        self.assertEqual(diagnostics["rawReasoningChunkCount"], 4)
        self.assertEqual(diagnostics["emittedReasoningBatchCount"], len(reasoning_events))

    async def test_trusted_reasoning_cumulative_snapshots_patch_one_canonical_node(self):
        self.stream_state.reasoning_surface_contract = {
            "mode": "provider_reasoning",
            "trust": "adapter_verified",
            "requestStyle": "openai_compatible",
            "responseFields": ["additional_kwargs.reasoning_content"],
            "displayKind": "provider_reasoning",
        }
        for snapshot in ["用户正在", "用户正在查询", "用户正在查询 LangChain"]:
            await self.runtime.handle_stream_event(
                self.chat_run,
                self.stream_state,
                {
                    "event": "on_chat_model_stream",
                    "run_id": "model_reasoning_snapshot",
                    "name": "V8ChatModelAdapter",
                    "metadata": {"v8_trusted_reasoning": True},
                    "data": {"chunk": {"additional_kwargs": {"reasoning_content": snapshot}}},
                },
            )
        await self.runtime.flush_stream_state(self.chat_run, self.stream_state)

        row = chat_runtime_module.db.get_chat_canonical_message(self.stream_state.assistant_message_id)
        reasoning_nodes = [
            node for node in row["nodes"]
            if node.get("kind") == "execution" and node.get("executionType") == "reasoning"
        ]

        self.assertEqual(len(reasoning_nodes), 1)
        self.assertEqual(reasoning_nodes[0]["content"], "用户正在查询 LangChain")
        self.assertEqual("".join(self.stream_state.reasoning_buffer), "用户正在查询 LangChain")
        reasoning_events = [
            event for event in self.chat_run.events
            if event["topic"] == "run.reasoning.delta"
        ]
        self.assertTrue(reasoning_events)
        self.assertTrue(all("snapshot" not in event["payload"] for event in reasoning_events))

    async def test_tool_internal_model_stream_does_not_mutate_assistant_transcript(self):
        emitted = await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_stream",
                "run_id": "tool_internal_model",
                "name": "ToolInternalModel",
                "metadata": {"v8_model_scope": "tool_internal"},
                "data": {
                    "chunk": {
                        "content": "内部正文",
                        "additional_kwargs": {"reasoning_content": "内部思考"},
                    }
                },
            },
        )

        self.assertEqual(emitted, [])
        self.assertIsNone(self.stream_state.assistant_message_id)
        self.assertEqual(self.stream_state.output_buffer, [])
        self.assertEqual(self.stream_state.reasoning_buffer, [])

    async def test_model_stream_during_active_tool_does_not_become_assistant_text(self):
        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_start",
                "run_id": "vision_tool_run",
                "name": "vision_media_analyzer",
                "data": {
                    "input": {
                        "file_path": r"C:\Users\sunny\.v8-agent-os\workspace\uploads\sample.webp",
                        "prompt": "识别图片人物",
                    }
                },
            },
        )

        emitted = await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_stream",
                "run_id": "vision_internal_model",
                "name": "VisionInternalModel",
                "data": {"chunk": "这个角色是三月七。"},
            },
        )

        self.assertEqual(emitted, [])
        self.assertEqual(self.stream_state.output_buffer, [])
        self.assertEqual([event["topic"] for event in self.chat_run.events], ["tool.started"])
        row = chat_runtime_module.db.get_chat_canonical_message(self.stream_state.assistant_message_id)
        self.assertFalse(any(node.get("kind") == "narrative" for node in row["nodes"]))

        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_end",
                "run_id": "vision_tool_run",
                "name": "vision_media_analyzer",
                "data": {"output": "--- Vision Analysis Complete ---\n这个角色是三月七。"},
            },
        )
        self.assertEqual(self.stream_state.active_tool_call_ids, set())

    async def test_terminal_failure_closes_active_tool_and_assistant_message(self):
        self.chat_run.active_run_id = f"run_test_{uuid.uuid4().hex}"
        chat_runtime_module.db.create_run_record(
            self.chat_run.active_run_id,
            self.chat_run.session_id,
            run_type="chat",
            status="running",
        )
        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_start",
                "run_id": "command_tool_run",
                "name": "run_system_command",
                "data": {"input": {"command": "pip install python-docx"}},
            },
        )

        emitted = self.runtime._close_active_tool_calls_for_terminal(
            self.chat_run,
            self.stream_state,
            status="failed",
            reason="graph_stream_native_failure",
        )
        self.runtime.persist_final_assistant_message(
            self.chat_run,
            self.stream_state,
            state="failed",
            terminal_metadata={"terminalReason": "graph_stream_native_failure"},
        )

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["tool"]["status"], "failed")
        self.assertEqual(self.stream_state.active_tool_call_ids, set())
        self.assertEqual(self.stream_state.watchdog.active_tool_call_ids, set())
        row = chat_runtime_module.db.get_chat_canonical_message(self.stream_state.assistant_message_id)
        self.assertEqual(row["state"], "failed")
        tool_results = [
            node
            for node in row["nodes"]
            if node.get("kind") == "execution" and node.get("executionType") == "tool_result"
        ]
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(tool_results[0]["status"], "failed")
        self.assertEqual(row["metadata"]["terminalReason"], "graph_stream_native_failure")

    def test_interrupt_still_emits_terminal_event_when_message_finalization_fails(self):
        self.chat_run.active_run_id = f"run_test_{uuid.uuid4().hex}"
        with mock.patch.object(
            self.runtime,
            "_close_active_tool_calls_for_terminal",
            return_value=[],
        ), mock.patch.object(
            self.runtime,
            "persist_final_assistant_message",
            side_effect=OSError("storage unavailable"),
        ), mock.patch.object(
            self.runtime,
            "_expire_plugin_task_grants",
        ), mock.patch.object(
            self.runtime,
            "_abort_engineering_workspaces",
        ):
            events = self.runtime.finalize_interrupted_run(
                self.chat_run,
                {"command": "interrupt", "reason": "web_composer_interrupt"},
                self.stream_state,
            )

        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["status"], "interrupted")
        self.assertEqual(events[-1]["run_id"], self.chat_run.active_run_id)

    def test_persist_prefers_authoritative_final_text(self):
        self.stream_state.assistant_message_id = "assistant-canonical-test"
        canonical_row = {
            "id": "assistant-canonical-test",
            "session_id": self.chat_run.session_id,
            "run_id": self.chat_run.active_run_id,
            "role": "assistant",
            "state": "completed",
            "nodes": [
                {"id": "assistant-canonical-test:text", "kind": "narrative", "role": "assistant", "content": "干净正文"}
            ],
            "artifacts": [],
            "content_text": "干净正文",
            "reasoning_text": None,
            "metadata": {"agentName": "智能主管"},
            "version": 2,
        }

        with mock.patch.object(chat_runtime_module.canonical_transcript_builder, "set_message_state"), mock.patch.object(
            chat_runtime_module.db,
            "get_chat_canonical_message",
            return_value=canonical_row,
        ), mock.patch.object(chat_runtime_module.db, "add_message") as add_message_mock, mock.patch.object(
            chat_runtime_module.db,
            "attach_runtime_artifacts_to_message",
        ), mock.patch.object(chat_runtime_module.workflow_ledger_service, "clear_chat_projection"):
            self.runtime.persist_final_assistant_message(self.chat_run, self.stream_state)

        self.assertTrue(add_message_mock.called)
        self.assertEqual(add_message_mock.call_args.kwargs["content"], "干净正文")

    async def test_runtime_handoff_reconciliation_replaces_early_supervisor_text(self):
        self.chat_run.active_run_id = f"run_test_{uuid.uuid4().hex}"
        chat_runtime_module.db.create_run_record(
            self.chat_run.active_run_id,
            self.chat_run.session_id,
            run_type="chat",
            status="running",
        )
        message_id = self.runtime._ensure_assistant_canonical_message(self.chat_run, self.stream_state)
        profile = self.runtime._get_agent_profile(self.stream_state.current_agent)
        self.runtime._append_canonical_node(
            self.chat_run,
            self.stream_state,
            node={
                "id": f"{message_id}:narrative:early",
                "kind": "narrative",
                "role": "assistant",
                "content": "开始跑调研通道啦！",
                "timestamp": self.runtime._now_timestamp_ms(),
                "agentName": profile["name"],
                "agentAvatar": profile["avatar"],
                "agentRoleLabel": profile["roleLabel"],
            },
        )
        final_state = {
            "runtime_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "resume_supervisor",
                "state": "handoff_ready",
                "handoffCount": 2,
            },
            "current_route_context": {
                "handoffRefs": [
                    {
                        "kind": "research_evidence_bundle",
                        "status": "ready",
                        "compactSummary": "Research Runtime 已生成证据包。",
                    },
                    {
                        "kind": "engineering_patch_bundle",
                        "status": "ready",
                        "compactSummary": "Engineering Runtime 已消费证据并产出 work plan。",
                    },
                ]
            },
            "messages": [],
        }

        async def _fake_state(_bundle):
            return final_state

        with mock.patch.object(chat_runtime_module.supervisor_runner, "get_state_snapshot", side_effect=_fake_state):
            await self.runtime.reconcile_final_assistant_message(
                self.chat_run,
                self.stream_state,
                SimpleNamespace(runner_bundle=object()),
            )

        row = chat_runtime_module.db.get_chat_canonical_message(message_id)
        self.assertIn("等待 Supervisor 验收", row["content_text"])
        self.assertIn("Research Runtime 已生成证据包", row["content_text"])
        self.assertNotIn("开始跑调研通道啦", row["content_text"])

    async def test_runtime_handoff_reconciliation_replaces_pending_runtime_text_from_final_state(self):
        self.chat_run.active_run_id = f"run_test_{uuid.uuid4().hex}"
        chat_runtime_module.db.create_run_record(
            self.chat_run.active_run_id,
            self.chat_run.session_id,
            run_type="chat",
            status="running",
        )
        message_id = self.runtime._ensure_assistant_canonical_message(self.chat_run, self.stream_state)
        self.runtime._append_canonical_node(
            self.chat_run,
            self.stream_state,
            node={
                "id": f"{message_id}:narrative:pending",
                "kind": "narrative",
                "role": "assistant",
                "content": "Runtime 已排队，等待 handoff。让我检查执行状态。",
                "timestamp": self.runtime._now_timestamp_ms(),
            },
        )
        final_state = {
            "runtime_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "resume_supervisor",
                "state": "degraded_handoff_ready",
                "handoffCount": 1,
            },
            "current_route_context": {
                "handoffRefs": [
                    {
                        "kind": "engineering_patch_bundle",
                        "status": "degraded",
                        "compactSummary": "Skill 产物验证失败，缺少必需研究文件。",
                    },
                ]
            },
            "messages": [AIMessage(content="Runtime 已排队，等待 handoff。让我检查执行状态。")],
        }

        async def _fake_state(_bundle):
            return final_state

        with mock.patch.object(chat_runtime_module.supervisor_runner, "get_state_snapshot", side_effect=_fake_state):
            await self.runtime.reconcile_final_assistant_message(
                self.chat_run,
                self.stream_state,
                SimpleNamespace(runner_bundle=object()),
            )

        row = chat_runtime_module.db.get_chat_canonical_message(message_id)
        self.assertIn("等待 Supervisor 验收", row["content_text"])
        self.assertIn("Skill 产物验证失败", row["content_text"])
        self.assertNotIn("等待 handoff。让我检查执行状态", row["content_text"])

    async def test_tool_start_sanitizes_runtime_internal_input(self):
        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_start",
                "run_id": "tool_run_1",
                "name": "generate_image",
                "data": {
                    "input": {
                        "params": {"prompt": "雷电将军", "size": "1:1"},
                        "runtime": "ToolRuntime(state={'messages': ['bad']})",
                        "callbacks": {"manager": "internal"},
                        "config": {"debug": True},
                    }
                },
            },
        )

        self.assertEqual(len(self.chat_run.events), 1)
        tool_payload = self.chat_run.events[0]["payload"]["tool"]
        tool_call_id = tool_payload["toolCallId"]
        self.assertTrue(tool_call_id.startswith("call_v8_generate_image_"))
        self.assertEqual(tool_payload["toolInvocationId"], tool_call_id)
        tool_args = tool_payload["args"]
        self.assertEqual(tool_args, {"params": {"prompt": "雷电将军", "size": "1:1"}})
        self.assertEqual(
            self.stream_state.tool_calls_buffer,
            [{"id": tool_call_id, "name": "generate_image", "args": {"params": {"prompt": "雷电将军", "size": "1:1"}}}],
        )

    def test_message_tool_event_emit_backfills_missing_tool_id(self):
        payload = {
            "type": "tool_start",
            "tool": {
                "toolName": "memory_broker",
                "args": {"mode": "catalog"},
            },
            "timestamp": 0,
        }
        node = {
            "id": "assistant-message:tool_call:memory-catalog",
            "kind": "execution",
            "executionType": "tool_call",
            "toolName": "memory_broker",
            "args": {"mode": "catalog"},
        }

        emitted = self.runtime._emit_message_targeted_runtime_event(
            self.chat_run,
            self.stream_state,
            topic="tool.started",
            payload=payload,
            node=node,
        )

        tool_call_id = emitted["payload"]["tool"]["toolCallId"]
        self.assertTrue(tool_call_id.startswith("call_v8_memory_broker_"))
        self.assertEqual(emitted["payload"]["tool"]["toolInvocationId"], tool_call_id)
        self.assertEqual(emitted["payload"]["toolCallId"], tool_call_id)
        self.assertEqual(payload["tool"]["toolCallId"], tool_call_id)
        self.assertEqual(node["toolCallId"], tool_call_id)

    def test_runtime_scoped_tool_event_emit_backfills_missing_tool_id(self):
        payload = {
            "type": "tool_result",
            "tool": {
                "toolName": "research_broker",
                "result": "答案：已完成。",
            },
            "timestamp": 0,
        }
        node = {
            "id": "runtime:run-test:tool_result:research",
            "kind": "execution",
            "executionType": "tool_result",
            "toolName": "research_broker",
            "result": "答案：已完成。",
        }
        owner = {
            "ownerRuntimeId": "research",
            "ownerAgentKind": "runtime",
            "ownerAgentId": "research",
            "displayInMessage": False,
            "runtimeContext": {},
        }

        emitted = self.runtime._emit_owner_scoped_runtime_event(
            self.chat_run,
            self.stream_state,
            topic="research.tool.finished",
            payload=payload,
            owner=owner,
            event_kind="tool_result",
            stream_key="research:tool:missing-id",
            node=node,
        )

        tool_call_id = emitted["payload"]["tool"]["toolCallId"]
        self.assertTrue(tool_call_id.startswith("call_v8_research_broker_"))
        self.assertEqual(emitted["payload"]["tool"]["toolInvocationId"], tool_call_id)
        self.assertEqual(emitted["payload"]["toolCallId"], tool_call_id)
        self.assertEqual(payload["tool"]["toolCallId"], tool_call_id)
        self.assertEqual(node["toolCallId"], tool_call_id)

    async def test_subagent_runtime_context_owns_tool_events(self):
        with bind_runtime_context(
            runtime_kind="subagent",
            trigger_source="delegation_broker",
            session_id=self.chat_run.session_id,
            run_id="run-subagent-owner",
            delegation_id="delegation-test",
            subagent_id="implementation-engineer",
            workspace_path="E:\\Projects\\v8chat",
        ):
            emitted = await self.runtime.handle_stream_event(
                self.chat_run,
                self.stream_state,
                {
                    "event": "on_tool_start",
                    "run_id": "tool_run_subagent_write",
                    "name": "write_native_file",
                    "data": {
                        "input": {
                            "path": "demo.txt",
                            "content": "hello",
                        }
                    },
                },
            )

        self.assertEqual(self.chat_run.events[-1]["topic"], "subagent.tool.started")
        self.assertEqual(self.chat_run.events[-1]["agent_id"], "implementation-engineer")
        payload = self.chat_run.events[-1]["payload"]
        self.assertEqual(payload["ownerRuntimeId"], "subagent_swarm")
        self.assertEqual(payload["ownerAgentKind"], "subagent")
        self.assertEqual(payload["ownerAgentId"], "implementation-engineer")
        self.assertFalse(payload["displayInMessage"])
        self.assertEqual(payload["runtimeContext"]["runtime_kind"], "subagent")
        self.assertEqual(emitted[0]["ownerRuntimeId"], "subagent_swarm")
        self.assertEqual(emitted[0]["ownerAgentId"], "implementation-engineer")

    async def test_tool_result_reuses_start_tool_call_id_from_raw_input(self):
        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_start",
                "run_id": "tool_run_callback",
                "name": "generate_image",
                "data": {
                    "input": {
                        "toolCallId": "call_original",
                        "params": {"prompt": "雷电将军", "size": "1:1"},
                    }
                },
            },
        )

        emitted = await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_end",
                "run_id": "tool_run_callback",
                "name": "generate_image",
                "data": {
                    "output": SimpleNamespace(
                        content='[{"type":"image_url","image_url":{"url":"https://example.com/image.png"}}]',
                        tool_call_id="call_provider_side",
                    )
                },
            },
        )

        self.assertEqual([event["topic"] for event in self.chat_run.events], ["tool.started", "tool.finished"])
        start_tool_call_id = self.chat_run.events[0]["payload"]["tool"]["toolCallId"]
        self.assertTrue(start_tool_call_id.startswith("call_v8_generate_image_"))
        self.assertEqual(emitted[0]["tool"]["toolCallId"], start_tool_call_id)
        self.assertEqual(emitted[0]["tool"]["toolInvocationId"], start_tool_call_id)
        self.assertEqual(emitted[0]["tool"]["result"]["imageCount"], 1)
        self.assertEqual(self.stream_state.active_tool_call_ids, set())

    async def test_tool_result_maps_provider_shadow_back_to_canonical_tool_call_id(self):
        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_start",
                "run_id": "tool_run_start",
                "name": "web_broker",
                "data": {
                    "input": {
                        "toolCallId": "call_v8_test_tool",
                        "providerToolCallId": "provider_tool_call_1",
                        "providerStandard": "openai",
                        "mode": "search",
                        "target": "V8 Agent OS",
                    }
                },
            },
        )

        emitted = await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_end",
                "run_id": "tool_run_end",
                "name": "web_broker",
                "data": {
                    "output": {
                        "providerToolCallId": "provider_tool_call_1",
                        "ok": True,
                        "summary": "search completed",
                        "mode": "search",
                    }
                },
            },
        )

        self.assertEqual([event["topic"] for event in self.chat_run.events], ["tool.started", "tool.finished"])
        self.assertEqual(emitted[0]["tool"]["toolCallId"], "call_v8_test_tool")
        self.assertEqual(emitted[0]["tool"]["providerToolCallId"], "provider_tool_call_1")
        self.assertEqual(emitted[0]["tool"]["providerStandard"], "openai")
        self.assertEqual(self.stream_state.active_tool_call_ids, set())

    async def test_unmatched_tool_result_skips_orphan_result_card(self):
        emitted = await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_end",
                "run_id": "orphan_callback_run",
                "name": "generate_image",
                "data": {
                    "output": SimpleNamespace(
                        content="unexpected result",
                        tool_call_id="call_orphan",
                    )
                },
            },
        )

        self.assertEqual(emitted, [])
        self.assertEqual(self.chat_run.events[-1]["topic"], "tool_result.unmatched")

    async def test_ask_user_interrupt_uses_compact_tool_args(self):
        self.chat_run.run_handle = SimpleNamespace(
            request_ask_user_interaction=lambda request, assistant_message_id: {
                "id": "ask_interaction_1",
                "tool_call_id": request.get("toolCallId") or "call_ask_user",
                "assistant_message_id": assistant_message_id,
                "status": "pending",
                "question": request.get("question"),
                "prompt": request.get("prompt"),
            },
            refresh_chat_snapshot=lambda: None,
        )

        emitted = await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chain_stream",
                "run_id": "chain_interrupt",
                "name": "SupervisorGraph",
                "data": {
                    "chunk": {
                        "__interrupt__": [
                            {
                                "id": "interrupt_1",
                                "value": {
                                    "question": "请告诉我你要重点测试哪部分。",
                                    "prompt": "请告诉我你要重点测试哪部分。",
                                    "toolCallId": "call_ask_user",
                                    "approvalKind": "ask_user",
                                    "interactionKind": "ask_user",
                                    "interruptId": "interrupt_1",
                                },
                            }
                        ]
                    }
                },
            },
        )

        self.assertEqual(emitted[0]["tool"]["toolCallId"], "call_ask_user")
        self.assertEqual(
            emitted[0]["tool"]["args"],
            {
                "question": "请告诉我你要重点测试哪部分。",
            },
        )

    async def test_ask_user_runtime_gate_unavailable_does_not_create_fake_wait(self):
        self.chat_run.run_handle = SimpleNamespace(
            request_ask_user_interaction=lambda request, assistant_message_id: {
                "id": "ask_interaction_gate_recovered",
                "tool_call_id": request.get("toolCallId"),
                "assistant_message_id": assistant_message_id,
                "status": "pending",
                "question": request.get("question"),
                "prompt": request.get("prompt"),
            },
            refresh_chat_snapshot=lambda: None,
        )

        emitted = await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_end",
                "run_id": "ask_user_gate_callback",
                "name": "ask_user",
                "data": {
                    "input": {
                        "toolCallId": "call_v8_ask_user_gate_recovered",
                        "question": "请审批 requirements 阶段。",
                    },
                    "output": {
                        "ok": False,
                        "error": "ask_user_unavailable_in_runtime_gate",
                    },
                },
            },
        )

        self.assertEqual(emitted, [])
        self.assertIsNone(self.stream_state.interrupted_signal)
        self.assertEqual(self.chat_run.events[-1]["topic"], "ask_user.runtime_gate.unavailable")
        self.assertEqual(
            self.chat_run.events[-1]["payload"]["recommendedNextAction"],
            "Continue from the tool result or route to a runtime that can pause safely.",
        )

    def test_run_system_command_result_is_compacted_to_preview(self):
        compacted = self.runtime._compact_tool_result_value(
            "run_system_command",
            "line 1\n" + ("line 2\n" * 600),
        )

        self.assertIsInstance(compacted, str)
        self.assertIn("<stdout>", compacted)
        self.assertIn("line 1", compacted)
        self.assertIn("[stdout truncated", compacted)
        self.assertNotIn('"status"', compacted)

    def test_run_system_command_preserves_complete_bounded_json_output(self):
        rows = [
            {
                "name": f"repository-{index:02d}",
                "visibility": "PUBLIC",
                "url": f"https://example.test/repository-{index:02d}",
            }
            for index in range(25)
        ]
        compacted = self.runtime._compact_tool_result_value(
            "run_system_command",
            {
                "ok": True,
                "kind": "command_result",
                "command": "example-cli list --json",
                "keyOutput": json.dumps(rows),
            },
        )

        self.assertIn("repository-00", compacted)
        self.assertIn("repository-24", compacted)
        self.assertIn("[complete structured stdout: 25 items]", compacted)
        self.assertNotIn("[stdout truncated", compacted)

    def test_runtime_broker_result_does_not_persist_full_route_context(self):
        compacted = self.runtime._compact_tool_result_value(
            "runtime_broker",
            {
                "goto": "resume_supervisor",
                "update": {
                    "runtime_dispatch_status": {"state": "degraded_handoff_ready"},
                    "messages": [{"content": "Engineering Runtime 已返回降级证据。"}],
                    "current_route_context": {
                        "runtimeId": "engineering",
                        "episodeId": "episode-test",
                        "handoffRefs": [
                            {
                                "id": "handoff-test",
                                "status": "degraded",
                                "compactSummary": "缺少写入证据。",
                                "inputs": {"messages": ["x" * 800_000]},
                            }
                        ],
                        "capabilityEpisodes": [{"inputs": {"messages": ["y" * 800_000]}}],
                    },
                },
            },
        )

        serialized = json.dumps(compacted, ensure_ascii=False)
        self.assertLess(len(serialized), 10_000)
        self.assertIn("缺少写入证据", serialized)
        self.assertNotIn('"inputs"', serialized)
        self.assertNotIn("capabilityEpisodes", serialized)

    async def test_command_tool_result_agent_visible_uses_terminal_surface(self):
        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_start",
                "run_id": "command_run_callback",
                "name": "command_session_broker",
                "data": {
                    "input": {
                        "toolCallId": "call_v8_command_test",
                        "mode": "observe",
                        "sessionId": "cmd-test",
                    }
                },
            },
        )

        emitted = await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_tool_end",
                "run_id": "command_run_callback",
                "name": "command_session_broker",
                "data": {
                    "output": {
                        "ok": True,
                        "mode": "observe",
                        "kind": "command_session",
                        "sessionId": "cmd-test",
                        "command": "npx tsc -b",
                        "state": "completed",
                        "finalPreview": "src/index.ts(3,8): error TS2307: Cannot find module './index.css'.",
                        "returnCode": 2,
                        "summary": "命令会话已完成。",
                    }
                },
            },
        )

        visible = emitted[0]["tool"]["agentVisibleResult"]
        self.assertIn("$ npx tsc -b", visible)
        self.assertIn("<stdout>", visible)
        self.assertIn("TS2307", visible)
        self.assertIn("[exit code: 2]", visible)
        self.assertNotIn('"summary"', visible)
        self.assertNotIn('"state"', visible)


if __name__ == "__main__":
    unittest.main()

