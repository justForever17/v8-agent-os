from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.database import db  # noqa: E402
from erc.chat_canonical_transcript import CanonicalTranscriptBuilder  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event(
    *,
    session_id: str,
    run_id: str,
    seq: int,
    topic: str,
    payload: dict[str, Any],
    runtime_id: str,
) -> None:
    db.add_runtime_event(
        {
            "event_id": f"evt_exec_map_demo_{seq:03d}_{uuid.uuid4().hex[:8]}",
            "session_id": session_id,
            "run_id": run_id,
            "seq": seq,
            "kind": "event",
            "topic": topic,
            "ts": _now_iso(),
            "source": {
                "component": "seed_execution_map_demo_session",
                "runtimeId": runtime_id,
                "agent_id": "supervisor",
            },
            "payload": payload,
        }
    )


def main() -> None:
    session_id = f"seed_execution_map_demo_{uuid.uuid4().hex[:12]}"
    run_id = f"run_execution_map_demo_{uuid.uuid4().hex[:12]}"
    user_message_id = f"msg_user_exec_map_{uuid.uuid4().hex[:12]}"
    assistant_message_id = f"msg_assistant_exec_map_{uuid.uuid4().hex[:12]}"
    timestamp = int(time.time() * 1000)
    builder = CanonicalTranscriptBuilder()

    db.create_or_update_session(
        session_id,
        title="执行地图演示：调研 + 工程 + 子代理",
        user_id="anonymous",
        agent_id="supervisor",
        metadata={
            "source": "seed_execution_map_demo_session",
            "demoKind": "runtime_execution_graph",
            "hiddenFromHistory": False,
        },
    )
    db.create_run_record(
        run_id,
        session_id=session_id,
        conversation_id=session_id,
        user_id="anonymous",
        run_type="chat",
        status="completed",
        trigger_source="seed_execution_map_demo_session",
        agent_id="supervisor",
        metadata={
            "source": "seed_execution_map_demo_session",
            "demoKind": "runtime_execution_graph",
        },
    )

    builder.create_message(
        message_id=user_message_id,
        session_id=session_id,
        run_id=run_id,
        ordinal=db.get_next_chat_canonical_ordinal(session_id),
        role="user",
        state="completed",
        metadata={"timestamp": timestamp, "clientMessageId": user_message_id},
        nodes=[
            {
                "id": f"{user_message_id}:narrative:seed",
                "kind": "narrative",
                "content": "演示一次调研 + 工程 + 子 agent + 孙 agent 的执行地图。",
                "timestamp": timestamp,
            }
        ],
    )

    assistant_intro = "我先确认这条任务需要调研证据、工程方案和子代理复核，然后把它路由成可观察的执行链。"
    assistant_mid = "Research 和 Engineering 已进入运行时路径；接下来我会让子代理复核关键假设，并把子/孙 agent 的活动摘要挂在这条消息里。"
    assistant_final = "\n".join(
        [
            "演示链路完成：Research 产出 evidence，Engineering 消费证据，Subagent 与孙 agent 完成复核。",
            "",
            "你可以在这条 Supervisor 气泡里看到子代理活动摘要；完整运行视图仍在“对话运行 / 执行地图”里。",
        ]
    )
    builder.create_message(
        message_id=assistant_message_id,
        session_id=session_id,
        run_id=run_id,
        ordinal=db.get_next_chat_canonical_ordinal(session_id),
        role="assistant",
        state="completed",
        metadata={
            "timestamp": timestamp + 500,
            "agentId": "supervisor",
            "agentName": "智能主管",
            "agentAvatar": "supervisor",
            "agentRoleLabel": "主理人",
            "source": "seed_execution_map_demo_session",
        },
        nodes=[
            {
                "id": f"{assistant_message_id}:reasoning:route",
                "kind": "execution",
                "executionType": "reasoning",
                "content": "判断任务边界：需要先调研，再进入工程方案，最后请子代理做独立复核。",
                "reasoningKind": "planning",
                "timestamp": timestamp + 500,
                "agentName": "智能主管",
                "agentAvatar": "supervisor",
                "agentRoleLabel": "主理人",
            },
            {
                "id": f"{assistant_message_id}:narrative:intro",
                "kind": "narrative",
                "content": assistant_intro,
                "timestamp": timestamp + 650,
                "agentName": "智能主管",
                "agentAvatar": "supervisor",
                "agentRoleLabel": "主理人",
            },
            {
                "id": f"{assistant_message_id}:tool_call:runtime_broker",
                "kind": "execution",
                "executionType": "tool_call",
                "toolCallId": "call_seed_runtime_route",
                "toolInvocationId": "call_seed_runtime_route",
                "toolName": "runtime_broker",
                "args": {
                    "mode": "route",
                    "need": {
                        "kind": "research",
                        "taskBrief": "收集 API、UI 和工程可行性证据。",
                    },
                },
                "timestamp": timestamp + 800,
                "agentName": "智能主管",
                "agentAvatar": "supervisor",
                "agentRoleLabel": "主理人",
            },
            {
                "id": f"{assistant_message_id}:tool_result:runtime_broker",
                "kind": "execution",
                "executionType": "tool_result",
                "toolCallId": "call_seed_runtime_route",
                "toolInvocationId": "call_seed_runtime_route",
                "toolName": "runtime_broker",
                "result": {
                    "ok": True,
                    "queuedEpisodeId": "ep_research_demo",
                    "episodeKind": "research",
                    "state": "queued",
                    "nextAction": "wait_episode",
                },
                "agentVisibleResult": "Research episode 已入队，等待 evidence handoff。",
                "timestamp": timestamp + 980,
                "agentName": "智能主管",
                "agentAvatar": "supervisor",
                "agentRoleLabel": "主理人",
            },
            {
                "id": f"{assistant_message_id}:narrative:mid",
                "kind": "narrative",
                "content": assistant_mid,
                "timestamp": timestamp + 1180,
                "agentName": "智能主管",
                "agentAvatar": "supervisor",
                "agentRoleLabel": "主理人",
            },
            {
                "id": f"{assistant_message_id}:tool_call:delegation_broker",
                "kind": "execution",
                "executionType": "tool_call",
                "toolCallId": "call_seed_delegation",
                "toolInvocationId": "call_seed_delegation",
                "toolName": "delegation_broker",
                "args": {
                    "mode": "dispatch",
                    "family": "engineering_review",
                    "tasks": [
                        {
                            "taskBrief": "复核工程方案中的 API 依赖、UI 风险和 proof 口径。",
                            "expectedHandoff": "subagent_result_bundle",
                        }
                    ],
                },
                "timestamp": timestamp + 1350,
                "agentName": "智能主管",
                "agentAvatar": "supervisor",
                "agentRoleLabel": "主理人",
            },
            {
                "id": f"{assistant_message_id}:tool_result:delegation_broker",
                "kind": "execution",
                "executionType": "tool_result",
                "toolCallId": "call_seed_delegation",
                "toolInvocationId": "call_seed_delegation",
                "toolName": "delegation_broker",
                "result": {
                    "ok": True,
                    "dispatchStatus": "task_confirmed",
                    "taskBriefIds": ["task_subagent_review_demo"],
                },
                "agentVisibleResult": "子代理任务已确认，后续由执行活动节点展示。",
                "timestamp": timestamp + 1500,
                "agentName": "智能主管",
                "agentAvatar": "supervisor",
                "agentRoleLabel": "主理人",
            },
            {
                "id": f"{assistant_message_id}:reasoning:merge",
                "kind": "execution",
                "executionType": "reasoning",
                "content": "等待子代理和孙 agent handoff 后，将只合并关键结论，不把内部日志塞进回复。",
                "reasoningKind": "merge",
                "timestamp": timestamp + 1650,
                "agentName": "智能主管",
                "agentAvatar": "supervisor",
                "agentRoleLabel": "主理人",
            },
            {
                "id": f"{assistant_message_id}:narrative:final",
                "kind": "narrative",
                "content": assistant_final,
                "timestamp": timestamp + 1800,
                "agentName": "智能主管",
                "agentAvatar": "supervisor",
                "agentRoleLabel": "主理人",
            }
        ],
    )

    seq = db.get_next_runtime_seq(session_id)
    events = [
        (
            "capability.need.detected",
            "planner_lane",
            {
                "need": {
                    "needId": "need_research_demo",
                    "kind": "research",
                    "source": "planner",
                    "reason": "需要先核对 API 与 UI 方案事实",
                    "requiredRuntimeAccess": ["memory.read", "web.read"],
                }
            },
        ),
        (
            "runtime.episode.queued",
            "research",
            {
                "episode": {
                    "episodeId": "ep_research_demo",
                    "kind": "research",
                    "state": "queued",
                    "parentEpisodeId": "supervisor",
                    "reason": "Research Runtime 排队收集证据",
                }
            },
        ),
        (
            "runtime.episode.active",
            "research",
            {
                "episode": {
                    "episodeId": "ep_research_demo",
                    "kind": "research",
                    "state": "active",
                    "parentEpisodeId": "supervisor",
                    "reason": "正在整理 evidence bundle",
                }
            },
        ),
        (
            "research_broker.result",
            "research",
            {
                "episodeId": "ep_research_demo",
                "kind": "research",
                "evidenceBundleId": "evidence_demo_001",
                "summary": "已形成 Research evidence bundle：官方文档、源码事实和冲突项已分层。",
                "confidence": "high",
            },
        ),
        (
            "handoff.ref.created",
            "research",
            {
                "handoffRef": {
                    "handoffRefId": "handoff_research_demo",
                    "kind": "research_evidence_bundle",
                    "producerEpisodeId": "ep_research_demo",
                    "status": "completed",
                    "confidence": "high",
                    "evidenceBundleId": "evidence_demo_001",
                    "compactSummary": "Research 证据包已交给 Engineering。",
                }
            },
        ),
        (
            "runtime.episode.completed",
            "research",
            {
                "episode": {
                    "episodeId": "ep_research_demo",
                    "kind": "research",
                    "state": "completed",
                    "parentEpisodeId": "supervisor",
                    "handoffRefs": ["handoff_research_demo"],
                    "reason": "Research Runtime 完成",
                }
            },
        ),
        (
            "runtime.episode.queued",
            "engineering",
            {
                "episode": {
                    "episodeId": "ep_engineering_demo",
                    "kind": "engineering",
                    "state": "queued",
                    "parentEpisodeId": "supervisor",
                    "handoffRefs": ["handoff_research_demo"],
                    "reason": "Engineering Runtime 准备消费 evidence 并生成 workset/proof",
                }
            },
        ),
        (
            "runtime.episode.active",
            "engineering",
            {
                "episode": {
                    "episodeId": "ep_engineering_demo",
                    "kind": "engineering",
                    "state": "active",
                    "parentEpisodeId": "supervisor",
                    "handoffRefs": ["handoff_research_demo"],
                    "reason": "工程主链已激活，正在规划实现与验证",
                }
            },
        ),
        (
            "delegation.dispatch_attempted",
            "subagent_swarm",
            {
                "delegationId": "delegation_attempt_no_task_demo",
                "kind": "delegation",
                "parentEpisodeId": "ep_engineering_demo",
                "status": "missing_tasks",
                "missingTasks": True,
                "dispatchStatus": "missing_tasks",
                "summary": "尝试派发子代理，但没有形成真实任务。",
            },
        ),
        (
            "runtime.episode.active",
            "subagent_swarm",
            {
                "episode": {
                    "episodeId": "ep_subagent_demo",
                    "kind": "delegation",
                    "state": "active",
                    "parentEpisodeId": "ep_engineering_demo",
                    "reason": "已确认拉起 2 个审计/实现子 agent",
                }
            },
        ),
        (
            "subagent.task.started",
            "subagent_swarm",
            {
                "taskBriefId": "task_subagent_review_demo",
                "kind": "delegation",
                "parentDelegationId": "ep_subagent_demo",
                "status": "active",
                "summary": "子 agent 正在审计工程实现边界。",
            },
        ),
        (
            "delegation.child.requested",
            "subagent_swarm",
            {
                "childDelegation": {
                    "delegationId": "child_delegation_demo",
                    "kind": "delegation",
                    "parentDelegationId": "task_subagent_review_demo",
                    "status": "active",
                    "reason": "子 agent 请求孙 agent 复核验证证据。",
                }
            },
        ),
        (
            "runtime.episode.active",
            "subagent_swarm",
            {
                "episode": {
                    "episodeId": "ep_grandchild_demo",
                    "kind": "delegation",
                    "state": "active",
                    "parentEpisodeId": "ep_subagent_demo",
                    "reason": "孙 agent 正在独立复核 proof。",
                }
            },
        ),
        (
            "handoff.ref.created",
            "subagent_swarm",
            {
                "handoffRef": {
                    "handoffRefId": "handoff_grandchild_demo",
                    "kind": "subagent_result_bundle",
                    "producerEpisodeId": "ep_grandchild_demo",
                    "status": "completed",
                    "compactSummary": "孙 agent 已回传 proof 复核结果。",
                }
            },
        ),
        (
            "runtime.episode.completed",
            "subagent_swarm",
            {
                "episode": {
                    "episodeId": "ep_grandchild_demo",
                    "kind": "delegation",
                    "state": "completed",
                    "parentEpisodeId": "ep_subagent_demo",
                    "handoffRefs": ["handoff_grandchild_demo"],
                    "reason": "孙 agent 完成",
                }
            },
        ),
        (
            "handoff.ref.created",
            "subagent_swarm",
            {
                "handoffRef": {
                    "handoffRefId": "handoff_subagent_demo",
                    "kind": "subagent_result_bundle",
                    "producerEpisodeId": "ep_subagent_demo",
                    "status": "completed",
                    "compactSummary": "Subagent Swarm 已合并子/孙 agent 结果。",
                }
            },
        ),
        (
            "runtime.episode.completed",
            "subagent_swarm",
            {
                "episode": {
                    "episodeId": "ep_subagent_demo",
                    "kind": "delegation",
                    "state": "completed",
                    "parentEpisodeId": "ep_engineering_demo",
                    "handoffRefs": ["handoff_subagent_demo"],
                    "reason": "子代理调度完成",
                }
            },
        ),
        (
            "handoff.ref.created",
            "engineering",
            {
                "handoffRef": {
                    "handoffRefId": "handoff_engineering_demo",
                    "kind": "verification_report",
                    "producerEpisodeId": "ep_engineering_demo",
                    "status": "completed",
                    "compactSummary": "Engineering 已完成 patch/proof 汇总。",
                }
            },
        ),
        (
            "runtime.episode.completed",
            "engineering",
            {
                "episode": {
                    "episodeId": "ep_engineering_demo",
                    "kind": "engineering",
                    "state": "completed",
                    "parentEpisodeId": "supervisor",
                    "handoffRefs": ["handoff_engineering_demo"],
                    "reason": "Engineering Runtime 完成并回流 Supervisor",
                }
            },
        ),
        (
            "context.prepared",
            "context_governance",
            {
                "recall_audit": {
                    "injection_allowed": True,
                    "summary": "已注入与执行地图演示相关的 memory refs。",
                },
                "durable_flush": {"reason": "memory_recall_injected"},
            },
        ),
    ]

    for offset, (topic, runtime_id, payload) in enumerate(events):
        _event(
            session_id=session_id,
            run_id=run_id,
            seq=seq + offset,
            topic=topic,
            payload=payload,
            runtime_id=runtime_id,
        )

    print(f"seedSessionId={session_id}")
    print("title=执行地图演示：调研 + 工程 + 子代理")
    print("open=Phone/Web 历史中打开该会话，然后点“执行地图”。")


if __name__ == "__main__":
    main()
