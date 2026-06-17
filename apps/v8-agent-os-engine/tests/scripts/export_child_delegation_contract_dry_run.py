from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

if "chromadb" not in sys.modules:
    class _FakeChromaCollection:
        def upsert(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {}

    class _FakeChromaClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def get_or_create_collection(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeChromaCollection()

    sys.modules["chromadb"] = type("chromadb", (), {"PersistentClient": _FakeChromaClient})()


from core.runtime_episode_runner import RuntimeEpisodeRunner  # noqa: E402
from core.runtime_episodes import build_handoff_ref, build_runtime_episode  # noqa: E402
from graph.agent_factories import _build_agent_system_content, _format_delegated_plan_context  # noqa: E402
from graph.parallel_support import _child_request_from_send_state  # noqa: E402


REPO_ROOT = ENGINE_ROOT.parents[1]
OUTPUT_ROOT = REPO_ROOT / "docs" / "chatruntime" / "runtime_deep_observation_reports"


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _contains_all(text: str, terms: list[str]) -> bool:
    return all(term in text for term in terms)


def build_matrix() -> dict[str, Any]:
    parent_branch = {
        "agentId": "skill-workflow-curator",
        "agentName": "Skill Workflow Curator",
        "delegationId": "delegation-parent-skill",
        "invocationId": "invoke-parent-skill",
        "taskBriefId": "TASK-PARENT-SKILL",
        "reason": "生成角色 skill，需要孙 agent 先做资料核查。",
        "allowChildDelegation": True,
        "childDelegationBudget": {"maxChildren": 2, "maxDepth": 1, "maxTotalNodes": 3},
    }
    child_task_brief = {
        "taskBriefId": "TASK-CHILD-RESEARCH",
        "title": "调研绝区零角色玲的可引用资料",
        "goal": "围绕绝区零角色“玲”收集官方设定、技能描述、台词风格、视觉关键词和可引用来源，返回给父 subagent 作为 skill 写作证据。",
        "brief": "不要只返回 ID。输出去噪资料摘要、来源表、缺证项和可用于 SKILL.md 的表达约束。",
        "requiredCapabilities": ["research", "source_evidence", "fact_check"],
        "runtimeAccess": ["research.core", "memory.read", "delegation.recursive"],
        "acceptanceContract": "Typed handoff must include answer, sources, score, limitations, rejectedEvidence, and detailRef.",
        "context": {
            "specRefs": ["REQ-001", "DES-002", "TASK-003"],
            "handoffContract": {
                "type": "research_evidence_pack",
                "requiredFields": ["answer", "sources", "score", "limitations", "detailRef"],
                "completionRule": "父 subagent 能直接基于证据继续写 skill，而不是再猜测孙 agent 做了什么。",
            },
        },
    }
    child_state = {
        "messages": [],
        "todos": [],
        "parallel_branch": {
            "agentId": "web-research-architect",
            "agentName": "Web Research Architect",
            "delegationId": "delegation-child-research",
            "invocationId": "invoke-child-research",
            "taskBriefId": "TASK-CHILD-RESEARCH",
            "reason": "调研绝区零角色玲的可引用资料，返回来源表和缺证项。",
            "taskBrief": child_task_brief,
            "runtimeAccess": ["research.core", "memory.read", "delegation.recursive"],
            "delegationDepth": 2,
            "allowChildDelegation": False,
        },
    }

    child_request = _child_request_from_send_state(
        child_state,
        source_branch=parent_branch,
        source_agent_id="skill-workflow-curator",
    )
    if child_request is None:
        raise RuntimeError("failed to build child delegation request")

    worker_brief, child_branch = RuntimeEpisodeRunner._child_worker_brief_from_request(
        child_request,
        workspace_path="E:/Projects/test3",
    )
    child_episode = build_runtime_episode(
        need={
            "kind": "delegation",
            "source": "subagent",
            "reason": child_request.get("childTaskGoal"),
            "needId": child_request.get("childDelegationId"),
            "parentEpisodeId": "episode-parent-skill",
            "inputs": {
                "targetCount": 1,
                "workerBriefs": [worker_brief],
                "allowChildDelegation": bool(child_branch.get("allowChildDelegation")),
                "childDelegationBudget": child_branch.get("childDelegationBudget") or {},
                "workspacePath": "E:/Projects/test3",
            },
        },
        kind="delegation",
        state="queued",
        required_runtime_access=["delegation.recursive"],
        parent_episode_id="episode-parent-skill",
        continuation_target="runtime_episode_runner",
        extra={
            "sourceInvocationId": child_request.get("sourceInvocationId"),
            "childInvocationId": child_request.get("childInvocationId"),
            "childTaskBriefId": child_request.get("childTaskBriefId"),
            "childAgentId": worker_brief.get("agentId"),
            "childAgentName": worker_brief.get("agentName"),
            "workspacePath": "E:/Projects/test3",
        },
    )
    planner_context = {
        "planId": "plan-child-contract-dry-run",
        "executionStrategy": "delegation_child_research",
        "planSummary": "父 subagent 委派孙 agent 做资料核查，再回流证据给父 subagent 继续写 skill。",
        "globalAcceptanceContract": "父子孙链路必须传递可读任务目标和可读 handoff，不允许只传 ID。",
    }
    grandchild_plan_context = _format_delegated_plan_context(worker_brief, planner_context)
    grandchild_prompt = _build_agent_system_content(
        agent_name=str(worker_brief.get("agentName") or "Web Research Architect"),
        agent_system_prompt=(
            "You are a source-backed research worker. Follow the delegated task brief, "
            "return a typed evidence handoff, and do not broaden the task."
        ),
        env_context=(
            "<environment>\n"
            "OS: Windows\n"
            "Active Workspace Root: E:/Projects/test3\n"
            "Main V8 Workspace Store: E:/Projects/test3\n"
            "</environment>\n"
        ),
        delegated_plan_context=grandchild_plan_context,
    )
    child_handoff = build_handoff_ref(
        producer_episode_id=child_episode["episodeId"],
        kind="delegation",
        compact_summary=(
            "孙 agent 完成资料核查：已整理绝区零角色玲的官方设定线索、可引用来源表、缺证项，"
            "父 subagent 可继续按 skill-creator 模板写 SKILL.md。"
        ),
        status="ready",
        confidence="medium",
        consumer_hint="父 subagent 应读取 childHandoffs[0].compactSummary 与 detailRef，再继续写作或请求补证。",
        extra={
            "answer": "玲的 skill 写作应围绕官方设定、行为风格、台词语气和视觉关键词展开。",
            "sources": [
                {"title": "官方角色资料页", "url": "https://example.invalid/official-ling", "score": 0.91},
                {"title": "版本公告", "url": "https://example.invalid/patch-note", "score": 0.83},
            ],
            "score": {"confidence": 0.82, "reuse": "usable_with_limitations"},
            "limitations": ["当前 dry-run 不访问真实网络，来源 URL 为占位。"],
            "detailRef": "runtime-handoff://child-ling-research",
        },
    )
    parent_resume_surface = {
        "resumedFrom": "child_handoffs",
        "childEpisodeIds": [child_episode["episodeId"]],
        "handoffIds": [child_handoff.get("handoffId") or child_handoff.get("handoffRefId")],
        "childHandoffs": [child_handoff],
        "parentVisibleSummary": (
            "Delegation handoff_ready after 1 child delegation handoff(s).\n"
            f"{child_handoff['compactSummary']}"
        ),
    }
    validations = {
        "child_request_has_full_task_brief": isinstance(child_request.get("childTaskBrief"), dict)
        and child_request["childTaskBrief"].get("goal") == child_task_brief["goal"],
        "child_task_goal_is_semantic_not_id": "绝区零角色“玲”" in str(child_request.get("childTaskGoal") or "")
        and str(child_request.get("childTaskGoal")) != str(child_request.get("childTaskBriefId")),
        "worker_brief_preserves_goal_and_acceptance": _contains_all(
            json.dumps(worker_brief, ensure_ascii=False),
            ["绝区零角色“玲”", "Typed handoff must include", "research.core"],
        ),
        "grandchild_prompt_contains_executable_context": _contains_all(
            grandchild_prompt,
            ["Assigned Task Brief", "绝区零角色“玲”", "Acceptance Contract", "Global Acceptance Contract"],
        ),
        "grandchild_prompt_contains_stable_operating_charter": _contains_all(
            grandchild_prompt,
            [
                "delegated_agent_operating_charter",
                "You are a delegated V8OS worker",
                "Child tasks must contain a real goal",
                "Approval/ask-user events are handled by the user-facing layer",
            ],
        ),
        "parent_resume_surface_is_human_readable": _contains_all(
            parent_resume_surface["parentVisibleSummary"],
            ["孙 agent 完成资料核查", "父 subagent", "SKILL.md"],
        ),
    }
    return {
        "description": "Dry-run matrix for subagent -> grandchild task contract and handoff readability. No model call, no DB write.",
        "scenario": "skill subagent delegates research grandchild for a role skill task",
        "validations": validations,
        "passed": all(validations.values()),
        "childRequest": child_request,
        "childEpisodeDraft": child_episode,
        "workerBrief": worker_brief,
        "grandchildPromptExcerpt": grandchild_prompt,
        "childHandoff": child_handoff,
        "parentResumeSurface": parent_resume_surface,
    }


def write_report(matrix: dict[str, Any]) -> dict[str, str]:
    stamp = _now_stamp()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_ROOT / f"{stamp}_child_delegation_contract_dry_run.json"
    md_path = OUTPUT_ROOT / f"{stamp}_child_delegation_contract_dry_run.md"
    json_path.write_text(json.dumps(_jsonable(matrix), ensure_ascii=False, indent=2), encoding="utf-8")
    validation_lines = "\n".join(
        f"- {'PASS' if ok else 'FAIL'} `{name}`"
        for name, ok in matrix["validations"].items()
    )
    md = f"""# Child Delegation Contract Dry Run

No model call, no database write, no workspace mutation.

## Result

Overall: **{'PASS' if matrix['passed'] else 'FAIL'}**

{validation_lines}

## Child Request

```json
{json.dumps(matrix['childRequest'], ensure_ascii=False, indent=2)}
```

## Child Episode Worker Brief

```json
{json.dumps(matrix['workerBrief'], ensure_ascii=False, indent=2)}
```

## Grandchild Agent Prompt Excerpt

```text
{matrix['grandchildPromptExcerpt']}
```

## Parent Resume / Handoff Surface

```json
{json.dumps(matrix['parentResumeSurface'], ensure_ascii=False, indent=2)}
```
"""
    md_path.write_text(md, encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path)}


def main() -> int:
    matrix = build_matrix()
    paths = write_report(matrix)
    print(json.dumps({**paths, "passed": matrix["passed"]}, ensure_ascii=False))
    return 0 if matrix["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
