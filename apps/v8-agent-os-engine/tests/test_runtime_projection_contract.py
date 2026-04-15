from __future__ import annotations

import sys
import unittest
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.runtime_projection import (
    project_chat_messages_from_events,
    merge_authoritative_timeline_messages,
    project_runtime_timeline_from_events,
)


class RuntimeProjectionContractTests(unittest.TestCase):
    def test_merge_prefers_richer_durable_assistant_content_for_same_run(self):
        projected_messages = [
            {
                "id": "assistant_run_x",
                "role": "assistant",
                "runId": "run_x",
                "content": "✅d",
                "parts": [
                    {"type": "text", "content": "✅"},
                    {"type": "text", "content": "d"},
                ],
                "timestamp": 1,
                "images": [],
                "artifacts": [],
            }
        ]
        durable_messages = [
            {
                "id": "durable_msg_x",
                "role": "assistant",
                "runId": "run_x",
                "content": "✅ 雷电将军的精美图片已经生成完成",
                "reasoningContent": "",
                "nodes": [
                    {"id": "durable_msg_x:content", "kind": "narrative", "content": "✅ 雷电将军的精美图片已经生成完成"}
                ],
                "timestamp": 2,
                "images": [],
                "artifacts": [],
            }
        ]

        merged = merge_authoritative_timeline_messages(projected_messages, durable_messages)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["content"], "✅ 雷电将军的精美图片已经生成完成")
        self.assertEqual(len(merged[0]["parts"]), 2)

    def test_extension_execution_completed_runtime_card_does_not_leak_message_preview(self):
        events = [
            {
                "event_id": "evt_extension_done",
                "run_id": "run_x",
                "seq": 1,
                "topic": "extension.execution.completed",
                "payload": {
                    "hasToolCalls": False,
                    "toolNames": [],
                    "messagePreview": "这段正式回复不应该再出现在 runtime 卡里",
                },
                "event_ts": "2026-04-15T09:10:17.866Z",
                "source": {},
            }
        ]

        timeline = project_runtime_timeline_from_events(events)

        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["summary"], "扩展候选执行完成")
        self.assertEqual(timeline[0]["status"], "completed")

    def test_run_state_changed_reads_to_status_payload(self):
        events = [
            {
                "event_id": "evt_state_changed",
                "run_id": "run_x",
                "seq": 2,
                "topic": "run.state.changed",
                "payload": {
                    "from_status": "running",
                    "to_status": "completed",
                    "reason": "stream_finished",
                },
                "event_ts": "2026-04-15T09:10:17.906Z",
                "source": {},
            }
        ]

        timeline = project_runtime_timeline_from_events(events)

        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["summary"], "运行状态已切换为：completed")
        self.assertEqual(timeline[0]["status"], "completed")

    def test_tool_started_projection_sanitizes_runtime_internal_args(self):
        events = [
            {
                "event_id": "evt_tool_started",
                "run_id": "run_x",
                "seq": 3,
                "topic": "tool.started",
                "payload": {
                    "tool": {
                        "toolCallId": "tool_1",
                        "toolName": "generate_image",
                        "args": {
                            "params": {"prompt": "雷电将军", "size": "1:1"},
                            "runtime": "ToolRuntime(state={'messages': ['bad']})",
                            "callbacks": {"manager": "internal"},
                            "config": {"debug": True},
                        },
                    }
                },
                "event_ts": "2026-04-15T09:10:17.916Z",
                "source": {},
            }
        ]

        messages = project_chat_messages_from_events(events)

        self.assertEqual(len(messages), 1)
        parts = messages[0].get("parts") or []
        self.assertEqual(len(parts), 1)
        self.assertEqual(
            parts[0]["args"],
            {"params": {"prompt": "雷电将军", "size": "1:1"}},
        )


if __name__ == "__main__":
    unittest.main()
