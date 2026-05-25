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

    assistant_text = "\n".join(
        [
            "这是一条本地种子的执行地图演示会话。",
            "",
            "预期在 Phone/Web 的“执行地图”里能看到：",
            "- Supervisor 根节点",
            "- Research episode 生成 evidence handoff",
            "- Engineering episode 消费调研证据",
            "- Subagent Swarm 先出现一次未确认派发，再出现真实子 agent 任务",
            "- 子 agent 请求 child delegation，并由孙 agent 回传 handoff",
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
                "id": f"{assistant_message_id}:narrative:seed",
                "kind": "narrative",
                "content": assistant_text,
                "timestamp": timestamp + 500,
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
