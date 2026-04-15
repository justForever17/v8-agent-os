from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

import runtimes.chat.runtime as chat_runtime_module
from runtimes.chat.runtime import ChatRuntime, ChatStreamState


class FakeChatRun:
    def __init__(self) -> None:
        self.active_run_id = "run_test"
        self.session_id = "session_test"
        self.transport = "chat"
        binding = SimpleNamespace(project_id=None, workspace_id=None, resolved_scope="global")
        self.scope_result = SimpleNamespace(binding=binding)
        self.events: list[dict] = []

    def emit_runtime_event(self, topic: str, payload: dict, **kwargs):
        event = {"topic": topic, "payload": payload, **kwargs}
        self.events.append(event)
        return event


class ChatTranscriptCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.runtime = ChatRuntime()
        self.runtime._get_agent_profile = lambda _node: {"name": "智能主管", "avatar": "", "roleLabel": "主理人"}
        self.chat_run = FakeChatRun()
        self.stream_state = ChatStreamState()
        self.workflow_patch = mock.patch.object(
            chat_runtime_module.workflow_ledger_service,
            "append_chat_projection",
            new=lambda **_kwargs: None,
        )
        self.workflow_patch.start()

    def tearDown(self) -> None:
        self.workflow_patch.stop()

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

    async def test_reasoning_only_terminal_response_still_keeps_reasoning(self):
        await self.runtime.handle_stream_event(
            self.chat_run,
            self.stream_state,
            {
                "event": "on_chat_model_end",
                "run_id": "model_reasoning",
                "name": "V8ChatModelAdapter",
                "data": {"output": {"reasoning_content": "只有思考，没有正文。"}},
            },
        )

        topics = [event["topic"] for event in self.chat_run.events]
        self.assertIn("run.reasoning.delta", topics)
        self.assertEqual("".join(self.stream_state.reasoning_buffer), "只有思考，没有正文。")

    def test_persist_prefers_authoritative_final_text(self):
        self.stream_state.output_buffer = ["坏前缀", "和残片"]
        self.stream_state.authoritative_final_text = "干净正文"

        with mock.patch.object(chat_runtime_module.db, "add_message") as add_message_mock, mock.patch.object(
            chat_runtime_module.db,
            "attach_runtime_artifacts_to_message",
        ), mock.patch.object(chat_runtime_module.workflow_ledger_service, "clear_chat_projection"):
            self.runtime.persist_final_assistant_message(self.chat_run, self.stream_state)

        self.assertTrue(add_message_mock.called)
        self.assertEqual(add_message_mock.call_args.kwargs["content"], "干净正文")
        self.assertEqual(self.stream_state.output_buffer, ["干净正文"])

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
        tool_args = self.chat_run.events[0]["payload"]["tool"]["args"]
        self.assertEqual(tool_args, {"params": {"prompt": "雷电将军", "size": "1:1"}})
        self.assertEqual(
            self.stream_state.tool_calls_buffer,
            [{"id": "tool_run_1", "name": "generate_image", "args": {"params": {"prompt": "雷电将军", "size": "1:1"}}}],
        )


if __name__ == "__main__":
    unittest.main()
