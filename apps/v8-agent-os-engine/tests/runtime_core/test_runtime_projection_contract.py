from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


from core.runtime_projection import (
    format_durable_chat_messages,
    project_chat_messages_from_events,
    merge_authoritative_timeline_messages,
    project_runtime_timeline_from_events,
    select_runtime_timeline_window,
)


class RuntimeProjectionContractTests(unittest.TestCase):
    def test_durable_supervisor_message_uses_current_admin_display_profile(self):
        rows = [
            {
                "id": "assistant_old",
                "role": "assistant",
                "content": "done",
                "created_at": "2026-07-29T00:00:00Z",
                "agent_id": "supervisor",
                "agent_name": "Supervisor",
                "agent_avatar": "old-avatar",
                "agent_role_label": "SUPERVISOR",
                "metadata": {"agentType": "supervisor"},
            }
        ]

        with patch(
            "core.runtime_projection.storage.get_supervisor_profile",
            return_value={"name": "智能主管", "roleLabel": "主理人", "avatar": "new-avatar"},
        ):
            messages = format_durable_chat_messages(rows)

        self.assertEqual(messages[0]["agentName"], "智能主管")
        self.assertEqual(messages[0]["agentRoleLabel"], "主理人")
        self.assertEqual(messages[0]["agentAvatar"], "new-avatar")
        self.assertEqual(messages[0]["nodes"][0]["agentName"], "智能主管")

    def test_durable_subagent_message_keeps_configured_identity(self):
        rows = [
            {
                "id": "assistant_subagent",
                "role": "assistant",
                "content": "reviewed",
                "created_at": "2026-07-29T00:00:00Z",
                "agent_id": "reviewer",
                "agent_name": "质量复核员",
                "agent_avatar": "reviewer-avatar",
                "agent_role_label": "验收",
                "metadata": {"agentType": "agent"},
            }
        ]

        messages = format_durable_chat_messages(rows)

        self.assertEqual(messages[0]["agentName"], "质量复核员")
        self.assertEqual(messages[0]["agentRoleLabel"], "验收")
        self.assertEqual(messages[0]["agentAvatar"], "reviewer-avatar")

    def test_agent_started_uses_canonical_id_not_configurable_labels(self):
        events = [
            {
                "event_id": "evt_supervisor_started",
                "run_id": "run_supervisor",
                "seq": 1,
                "topic": "agent.started",
                "payload": {
                    "agent": {
                        "id": "supervisor",
                        "name": "Supervisor",
                        "avatar": "old-avatar",
                        "roleLabel": "SUPERVISOR",
                    }
                },
                "event_ts": "2026-07-29T00:00:00Z",
                "source": {},
            }
        ]

        with patch(
            "core.runtime_projection.storage.get_supervisor_profile",
            return_value={"name": "智能主管", "roleLabel": "主理人", "avatar": "new-avatar"},
        ):
            messages = project_chat_messages_from_events(events)

        self.assertEqual(messages[0]["agentType"], "supervisor")
        self.assertEqual(messages[0]["agentName"], "智能主管")
        self.assertEqual(messages[0]["agentRoleLabel"], "主理人")

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
        self.assertNotIn("messagePreview", timeline[0]["metadata"])

    def test_extension_candidate_projection_keeps_counts_not_candidate_payloads(self):
        events = [
            {
                "event_id": "evt_extension_route",
                "run_id": "run_x",
                "seq": 1,
                "topic": "extension.route.selected",
                "payload": {
                    "skillCandidates": [{"name": "gh", "description": "large private payload"}],
                    "mcpToolCandidates": [{"name": "github.search", "inputSchema": {"type": "object"}}],
                    "routing": {"private": "do not project"},
                },
                "source": {},
            }
        ]

        timeline = project_runtime_timeline_from_events(events)

        self.assertEqual(timeline[0]["metadata"], {"skillCount": 1, "mcpToolCount": 1})
        self.assertNotIn("large private payload", str(timeline))

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

    def test_canvas_graph_run_state_projects_only_canonical_metadata(self):
        events = [
            {
                "event_id": "evt_canvas_retry",
                "session_id": "session-a",
                "run_id": "chat-run-a",
                "seq": 3,
                "topic": "canvas.graph.run.state",
                "payload": {
                    "schema": "v8.creative_canvas_graph_run_state.v1",
                    "sessionId": "session-a",
                    "workspaceId": "workspace-a",
                    "graphId": "graph-a",
                    "graphRunId": "graph-run-a",
                    "canvasOperationId": "canvas-op-a",
                    "runId": "chat-run-a",
                    "status": "running",
                    "transition": "retry_failed_branch",
                    "retryOfGraphRunId": "graph-run-a",
                    "nodeStates": {"private-node": {"providerPayload": "do-not-project"}},
                    "workspacePath": "C:/private/workspace",
                    "providerResponse": {"secret": "do-not-project"},
                },
                "event_ts": "2026-08-15T00:00:00Z",
                "source": {"component": "creative_canvas_graph"},
            }
        ]

        timeline = project_runtime_timeline_from_events(events)

        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["runtimeId"], "creative_media")
        self.assertEqual(timeline[0]["topic"], "canvas.graph.run.state")
        self.assertEqual(timeline[0]["status"], "running")
        self.assertEqual(timeline[0]["metadata"], {
            "schema": "v8.creative_canvas_graph_run_state.v1",
            "sessionId": "session-a",
            "workspaceId": "workspace-a",
            "graphId": "graph-a",
            "graphRunId": "graph-run-a",
            "canvasOperationId": "canvas-op-a",
            "runId": "chat-run-a",
            "status": "running",
            "transition": "retry_failed_branch",
            "retryOfGraphRunId": "graph-run-a",
        })
        self.assertNotIn("do-not-project", str(timeline))
        self.assertNotIn("C:/private/workspace", str(timeline))

    def test_canvas_graph_run_state_rejects_noncanonical_authority_and_retry_lineage(self):
        canonical_payload = {
            "schema": "v8.creative_canvas_graph_run_state.v1",
            "sessionId": "session-a",
            "workspaceId": "workspace-a",
            "graphId": "graph-a",
            "graphRunId": "graph-run-a",
            "canvasOperationId": "canvas-op-a",
            "runId": None,
            "status": "running",
        }
        events = [
            {
                "event_id": "evt_canvas_wrong_session",
                "session_id": "session-b",
                "run_id": None,
                "seq": 4,
                "topic": "canvas.graph.run.state",
                "payload": canonical_payload,
                "source": {},
            },
            {
                "event_id": "evt_canvas_alias_conflict",
                "session_id": "session-a",
                "run_id": None,
                "seq": 5,
                "topic": "canvas.graph.run.state",
                "payload": {**canonical_payload, "session_id": "session-b"},
                "source": {},
            },
            {
                "event_id": "evt_canvas_retry_without_lineage",
                "session_id": "session-a",
                "run_id": None,
                "seq": 6,
                "topic": "canvas.graph.run.state",
                "payload": {**canonical_payload, "transition": "retry_failed_branch"},
                "source": {},
            },
        ]

        self.assertEqual(project_runtime_timeline_from_events(events), [])

    def test_canvas_remote_terminal_reconciliation_projects_retry_recovery(self):
        event = {
            "event_id": "evt_canvas_remote_terminal",
            "session_id": "session-a",
            "run_id": None,
            "seq": 7,
            "topic": "canvas.graph.run.state",
            "payload": {
                "schema": "v8.creative_canvas_graph_run_state.v1",
                "sessionId": "session-a",
                "workspaceId": "workspace-a",
                "graphId": "graph-a",
                "graphRunId": "graph-run-a",
                "canvasOperationId": "canvas-op-a",
                "runId": None,
                "status": "failed",
                "transition": "remote_terminal_reconciled",
                "recovery": {"canRetry": True, "mode": "failed_branch"},
            },
            "source": {"component": "creative_canvas_graph"},
        }

        timeline = project_runtime_timeline_from_events([event])

        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["status"], "failed")
        self.assertEqual(timeline[0]["metadata"]["transition"], "remote_terminal_reconciled")
        self.assertEqual(
            timeline[0]["metadata"]["recovery"],
            {"canRetry": True, "mode": "failed_branch"},
        )

    def test_canvas_graph_run_state_survives_compact_reload_window(self):
        events = [
            {
                "event_id": "evt_canvas_completed",
                "session_id": "session-a",
                "run_id": None,
                "seq": 1,
                "topic": "canvas.graph.run.state",
                "payload": {
                    "schema": "v8.creative_canvas_graph_run_state.v1",
                    "sessionId": "session-a",
                    "workspaceId": "workspace-a",
                    "graphId": "graph-a",
                    "graphRunId": "graph-run-a",
                    "canvasOperationId": "canvas-op-a",
                    "runId": None,
                    "status": "completed",
                },
                "source": {},
            },
            {
                "event_id": "evt_lane_queued",
                "run_id": "chat-run-b",
                "seq": 2,
                "topic": "run.lane.queued",
                "payload": {},
                "source": {},
            },
            {
                "event_id": "evt_lane_acquired",
                "run_id": "chat-run-b",
                "seq": 3,
                "topic": "run.lane.acquired",
                "payload": {},
                "source": {},
            },
            {
                "event_id": "evt_lane_released",
                "run_id": "chat-run-b",
                "seq": 4,
                "topic": "run.lane.released",
                "payload": {},
                "source": {},
            },
        ]

        timeline = project_runtime_timeline_from_events(events)
        compact = select_runtime_timeline_window(timeline, recent_limit=2, milestone_limit=1)

        self.assertEqual([entry["seq"] for entry in compact], [1, 3, 4])
        self.assertEqual(compact[0]["topic"], "canvas.graph.run.state")
        self.assertEqual(compact[0]["metadata"]["status"], "completed")

    def test_runtime_episode_projection_routes_by_nested_kind(self):
        events = [
            {
                "event_id": "evt_research_episode",
                "run_id": "run_x",
                "seq": 3,
                "topic": "runtime.episode.queued",
                "payload": {
                    "episode": {
                        "episodeId": "episode_research",
                        "kind": "research",
                        "state": "queued",
                        "reason": "查证项目资料",
                    }
                },
                "event_ts": "2026-04-15T09:10:17.916Z",
                "source": {},
            },
            {
                "event_id": "evt_research_handoff",
                "run_id": "run_x",
                "seq": 4,
                "topic": "handoff.ref.created",
                "payload": {
                    "handoffRef": {
                        "handoffRefId": "handoff_research",
                        "producerEpisodeId": "episode_research",
                        "kind": "research_evidence_bundle",
                        "status": "ready",
                        "compactSummary": "调研证据包 ready",
                    }
                },
                "event_ts": "2026-04-15T09:10:18.916Z",
                "source": {},
            },
        ]

        timeline = project_runtime_timeline_from_events(events)

        self.assertEqual(timeline[0]["runtimeId"], "research")
        self.assertEqual(timeline[0]["metadata"]["episodeId"], "episode_research")
        self.assertEqual(timeline[1]["runtimeId"], "research")
        self.assertEqual(timeline[1]["metadata"]["episodeId"], "episode_research")

    def test_runtime_episode_progress_projects_only_sanitized_subagent_timeline_node(self):
        events = [
            {
                "event_id": "evt_subagent_tool",
                "run_id": "run_subagent",
                "seq": 9,
                "topic": "runtime.episode.progress",
                "payload": {
                    "episodeId": "subagent::delegation_one::reviewer",
                    "kind": "delegation",
                    "state": "active",
                    "progress": {
                        "stage": "tool_execution",
                        "status": "running",
                        "summary": "正在读取文件。",
                        "agentName": "Reviewer",
                        "rawOutput": "must-not-project",
                        "timelineNode": {
                            "id": "tool-1:call",
                            "kind": "execution",
                            "executionType": "tool_call",
                            "topic": "subagent.tool.started",
                            "toolName": "read_native_file",
                            "toolCallId": "tool-1",
                            "args": {"path": "README.md"},
                            "providerMetadata": {"secret": "must-not-project"},
                        },
                    },
                    "scheduler": {"lease": "must-not-project"},
                },
                "event_ts": "2026-07-25T12:00:00Z",
                "source": {},
            }
        ]

        timeline = project_runtime_timeline_from_events(events)

        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["runtimeId"], "subagent_swarm")
        self.assertEqual(timeline[0]["metadata"]["progress"]["timelineNode"]["topic"], "subagent.tool.started")
        self.assertEqual(
            timeline[0]["metadata"]["dedupeKey"],
            "subagent-timeline:subagent::delegation_one::reviewer:tool-1:call",
        )
        self.assertNotIn("rawOutput", str(timeline[0]))
        self.assertNotIn("providerMetadata", str(timeline[0]))
        self.assertNotIn("scheduler", str(timeline[0]))
        self.assertNotIn("must-not-project", str(timeline[0]))

    def test_runtime_episode_progress_without_timeline_node_stays_runtime_surface_only(self):
        events = [
            {
                "event_id": "evt_scheduler_progress",
                "run_id": "run_subagent",
                "seq": 10,
                "topic": "runtime.episode.progress",
                "payload": {
                    "episodeId": "subagent::delegation_one::reviewer",
                    "kind": "delegation",
                    "progress": {"stage": "working", "summary": "internal scheduler update"},
                },
                "event_ts": "2026-07-25T12:00:01Z",
                "source": {},
            }
        ]

        self.assertEqual(project_runtime_timeline_from_events(events), [])

    def test_subagent_text_delta_projects_bounded_content_without_runtime_diagnostics(self):
        events = [
            {
                "event_id": "evt_subagent_text",
                "run_id": "run_subagent",
                "seq": 11,
                "topic": "subagent.text.delta",
                "payload": {
                    "snapshot": "**复核结论**\n\nREADME 首标题清晰。",
                    "segmentKey": "segment-1",
                    "ownerAgentId": "reviewer",
                    "runtimeContext": {
                        "delegation_id": "subagent::delegation_one::reviewer",
                        "workspace_path": "C:/private/worktree",
                    },
                    "_diagnostics": {"provider": "must-not-project"},
                },
                "event_ts": "2026-07-25T12:00:02Z",
                "source": {},
            }
        ]

        timeline = project_runtime_timeline_from_events(events)

        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["topic"], "subagent.text.delta")
        self.assertEqual(timeline[0]["metadata"]["content"], "**复核结论**\n\nREADME 首标题清晰。")
        self.assertEqual(timeline[0]["metadata"]["delegationId"], "subagent::delegation_one::reviewer")
        self.assertNotIn("workspace_path", str(timeline[0]))
        self.assertNotIn("_diagnostics", str(timeline[0]))
        self.assertNotIn("must-not-project", str(timeline[0]))

    def test_runtime_episode_projection_keeps_compact_handoff_without_raw_task_contract(self):
        events = [
            {
                "event_id": "evt_creative_complete",
                "run_id": "run_creative",
                "seq": 5,
                "topic": "runtime.episode.completed",
                "payload": {
                    "episode": {
                        "episodeId": "episode_creative",
                        "kind": "creative_media",
                        "state": "completed",
                        "reason": "准备创意媒体执行方案",
                        "need": {"inputs": {"privatePrompt": "do not project"}},
                    },
                    "handoffRefs": [
                        {
                            "handoffRefId": "handoff_creative",
                            "producerEpisodeId": "episode_creative",
                            "kind": "asset_bundle",
                            "status": "ready",
                            "compactSummary": "Creative Media recipe compiled",
                            "handoffStage": "compiled",
                            "requiresContinuation": True,
                            "recommendedNextAction": "Create provider jobs",
                            "privateProviderPayload": {"secret": "do not project"},
                        }
                    ],
                    "resume": {"resume_error": "internal scheduling detail"},
                },
                "source": {"agent_id": "supervisor"},
            }
        ]

        timeline = project_runtime_timeline_from_events(events)
        metadata = timeline[0]["metadata"]

        self.assertEqual(metadata["episodeId"], "episode_creative")
        self.assertTrue(metadata["requiresContinuation"])
        self.assertEqual(metadata["handoff"]["handoffStage"], "compiled")
        self.assertEqual(metadata["handoffRefs"][0]["handoffRefId"], "handoff_creative")
        self.assertNotIn("episode", metadata)
        self.assertNotIn("resume", metadata)
        self.assertNotIn("privateProviderPayload", str(metadata))
        self.assertNotIn("do not project", str(metadata))

    def test_delegation_broker_missing_result_marks_dispatch_unconfirmed(self):
        events = [
            {
                "event_id": "evt_delegation_start",
                "run_id": "run_x",
                "seq": 5,
                "topic": "tool.started",
                "payload": {
                    "tool": {
                        "toolCallId": "call_delegation",
                        "toolName": "delegation_broker",
                        "args": {"mode": "dispatch", "targetCount": 3},
                    }
                },
                "event_ts": "2026-04-15T09:10:19.916Z",
                "source": {},
            },
            {
                "event_id": "evt_run_end",
                "run_id": "run_x",
                "seq": 6,
                "topic": "run.state.changed",
                "payload": {"to_status": "completed"},
                "event_ts": "2026-04-15T09:10:20.916Z",
                "source": {},
            },
        ]

        timeline = project_runtime_timeline_from_events(events)
        missing = [entry for entry in timeline if entry.get("metadata", {}).get("missingResult")]

        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["runtimeId"], "subagent_swarm")
        self.assertEqual(missing[0]["status"], "missing_result")
        self.assertIn("未确认实际派发", missing[0]["summary"])

    def test_creative_media_tool_projection_keeps_actor_but_not_raw_result(self):
        events = [
            {
                "event_id": "evt_creative_start",
                "run_id": "run_creative",
                "seq": 10,
                "topic": "creative_media.tool.started",
                "payload": {
                    "runtimeId": "creative_media",
                    "ownerAgentKind": "runtime",
                    "ownerAgentId": "supervisor",
                    "tool": {
                        "toolCallId": "call_creative",
                        "toolName": "creative_media_jobs",
                        "args": {"prompt": "private prompt"},
                    },
                },
                "source": {"agent_id": "supervisor"},
            },
            {
                "event_id": "evt_creative_finish",
                "run_id": "run_creative",
                "seq": 11,
                "topic": "creative_media.tool.finished",
                "payload": {
                    "runtimeId": "creative_media",
                    "ownerAgentKind": "runtime",
                    "ownerAgentId": "supervisor",
                    "tool": {
                        "toolCallId": "call_creative",
                        "toolName": "creative_media_jobs",
                        "result": {"ok": True, "status": "failed", "raw": "private provider payload"},
                    },
                },
                "source": {"agent_id": "supervisor"},
            },
        ]

        timeline = project_runtime_timeline_from_events(events)

        self.assertEqual([entry["topic"] for entry in timeline], [
            "creative_media.tool.started",
            "creative_media.tool.finished",
        ])
        self.assertEqual(timeline[0]["runtimeId"], "creative_media")
        self.assertEqual(timeline[0]["metadata"]["ownerAgentId"], "supervisor")
        self.assertEqual(timeline[1]["status"], "failed")
        self.assertFalse(timeline[1]["metadata"]["ok"])
        self.assertNotIn("args", timeline[0]["metadata"])
        self.assertNotIn("result", timeline[1]["metadata"])
        self.assertNotIn("private provider payload", str(timeline))

    def test_compact_runtime_window_keeps_old_handoff_amid_recent_noise(self):
        timeline = [
            {"id": "handoff", "seq": 1, "topic": "handoff.ref.created"},
            *[
                {"id": f"noise-{index}", "seq": index + 2, "topic": "extension.route.selected"}
                for index in range(220)
            ],
        ]

        compact = select_runtime_timeline_window(timeline, recent_limit=160, milestone_limit=32)

        self.assertEqual(len(compact), 161)
        self.assertEqual(compact[0]["id"], "handoff")
        self.assertEqual(compact[-1]["id"], "noise-219")

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

    def test_user_recorded_projection_keeps_attachments_for_client_preview(self):
        events = [
            {
                "event_id": "evt_user_recorded",
                "run_id": "run_audio",
                "seq": 1,
                "topic": "message.user.recorded",
                "payload": {
                    "message_id": "msg_user_audio",
                    "content": "",
                    "images": ["/api/workspace/files/.v8/uploads/voice.mp3"],
                    "attachments": [
                        {
                            "name": "voice.mp3",
                            "url": "/api/workspace/files/.v8/uploads/voice.mp3",
                            "publicUrl": "/api/workspace/files/.v8/uploads/voice.mp3",
                            "mimeType": "audio/mpeg",
                            "mediaKind": "audio",
                        }
                    ],
                    "metadata": {},
                },
                "event_ts": "2026-04-15T09:10:17.916Z",
                "source": {},
            }
        ]

        messages = project_chat_messages_from_events(events)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "")
        self.assertEqual(messages[0]["images"], [])
        self.assertEqual(messages[0]["metadata"]["attachments"][0]["name"], "voice.mp3")


if __name__ == "__main__":
    unittest.main()

