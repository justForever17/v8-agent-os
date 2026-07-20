from core.delegation_broker import expand_delegation_task_briefs, normalize_task_brief
from graph.parallel_support import (
    _fallback_child_delegation_request,
    _subagent_reported_terminal_failure,
)


def test_target_count_expands_one_macro_task_into_workers():
    expanded = expand_delegation_task_briefs(
        [
            {
                "taskBriefId": "audit",
                "goal": "Audit the codebase",
                "targetCount": 3,
                "familyHint": "engineering",
            }
        ]
    )

    assert len(expanded) == 3
    assert [item["taskBriefId"] for item in expanded] == [
        "audit#worker-1",
        "audit#worker-2",
        "audit#worker-3",
    ]
    assert all(item["parentTaskBriefId"] == "audit" for item in expanded)
    assert all(item["targetCount"] == 1 for item in expanded)


def test_worker_briefs_override_branch_goal_without_requiring_multiple_tasks():
    expanded = expand_delegation_task_briefs(
        [
            {
                "taskBriefId": "research",
                "goal": "Research model behavior",
                "workerBriefs": [
                    {"goal": "Check official documentation", "requiredCapabilities": ["docs"]},
                    {"goal": "Check issue reports", "requiredCapabilities": ["issue_search"]},
                ],
            }
        ]
    )

    assert len(expanded) == 2
    assert [item["goal"] for item in expanded] == [
        "Check official documentation",
        "Check issue reports",
    ]
    assert expanded[0]["requiredCapabilities"] == ["docs"]
    assert expanded[1]["requiredCapabilities"] == ["issue_search"]


def test_explicit_blocker_handoff_cannot_be_normalized_to_success():
    assert _subagent_reported_terminal_failure(
        "## Blocker / Degraded Handoff\n\nThe independent verification could not run."
    ) == ("blocked", "subagent_reported_terminal_failure")
    assert _subagent_reported_terminal_failure(
        "## 验证结果\n\n**阻断原因**：当前验证胶囊没有命令执行能力。"
    ) == ("blocked", "subagent_reported_terminal_failure")
    assert _subagent_reported_terminal_failure(
        "### Verdict\n**NOT VERIFIED** — the parent artifact still fails at runtime."
    ) == ("failed", "subagent_reported_verification_failure")
    assert _subagent_reported_terminal_failure(
        "### 验收结论\n**未通过**：输出与合同不一致。"
    ) == ("failed", "subagent_reported_verification_failure")


def test_empty_blocker_section_does_not_poison_successful_handoff():
    assert _subagent_reported_terminal_failure(
        """### Risks / Blockers / Notes

- **None.** All acceptance items have primary evidence.

### Local Self-Check Status

**LOCAL SELF-CHECK: PASS**
"""
    ) is None
    assert _subagent_reported_terminal_failure(
        """## Blockers

No blockers. The independent verification passed.
"""
    ) is None


def test_in_graph_incomplete_child_dispatch_repairs_to_read_only_parent_mirror():
    parent_task = normalize_task_brief(
        {
            "taskBriefId": "parent-write",
            "goal": "Implement the assigned file and delegate independent verification.",
            "context": {"workspacePath": "C:/managed/parent"},
            "writeRequired": True,
            "writeSet": ["src/result.py"],
            "expectedOutputs": ["src/result.py"],
            "acceptanceContract": "The file runs successfully.",
        }
    )
    request = _fallback_child_delegation_request(
        branch={
            "invocationId": "invoke-parent",
            "delegationId": "delegation-parent",
            "delegationDepth": 1,
            "agentId": "frontend-product-engineer",
            "agentName": "Frontend Product Engineer",
            "reason": parent_task["goal"],
            "taskBrief": parent_task,
        },
        summary={
            "error": "delegation_child_requested",
            "nestedDispatchCount": 1,
        },
    )

    child_branch = request["send"]["arg"]["parallel_branch"]
    child_task = child_branch["taskBrief"]
    assert request["sourceDelegationId"] == "delegation-parent"
    assert request["sourceAgentId"] == "frontend-product-engineer"
    assert child_branch["delegationDepth"] == 2
    assert child_branch["allowChildDelegation"] is False
    assert child_task["engineeringTaskCapsule"]["executionMode"] == "verify"
    assert child_task["writeSet"] == []
    assert child_task["readSet"] == ["src/result.py"]
