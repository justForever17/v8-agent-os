import json

from core.delegation_broker import (
    expand_delegation_task_briefs,
    normalize_task_brief,
    task_brief_query_text,
    task_brief_requires_child_delegation,
)
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


def test_incomplete_child_dispatch_preserves_explicit_grandchild_verification_contract():
    parent_task = normalize_task_brief(
        {
            "taskBriefId": "parent-exact-proof",
            "goal": "Fix the file and independently verify it.",
            "context": {
                "workspacePath": "C:/managed/parent",
                "mandatoryGrandchildBrief": {
                    "goal": "Read and execute the final Python file.",
                    "readSet": ["src/result.py"],
                    "writeSet": [],
                    "expectedOutputs": ["command, exit code, stdout, and stderr"],
                    "acceptanceContract": [
                        "执行 python src/result.py",
                        "stdout 严格等于 exact-proof",
                        "stderr 为空",
                    ],
                    "toolPolicy": {
                        "mode": "allowlist",
                        "allowedTools": ["read_native_file", "run_system_command"],
                    },
                },
            },
            "writeSet": ["src/result.py"],
            "requireChildDelegation": True,
        }
    )

    request = _fallback_child_delegation_request(
        branch={
            "invocationId": "invoke-exact",
            "delegationId": "delegation-exact",
            "delegationDepth": 1,
            "reason": parent_task["goal"],
            "taskBrief": parent_task,
        },
        summary={},
    )

    child_task = request["childTaskBrief"]
    assert child_task["goal"] == "Read and execute the final Python file."
    assert child_task["readSet"] == ["src/result.py"]
    assert child_task["writeSet"] == []
    assert child_task["toolPolicy"]["allowedTools"] == ["read_native_file", "run_system_command"]
    assert child_task["allowChildDelegation"] is False
    assert child_task["requireChildDelegation"] is False
    assert task_brief_requires_child_delegation(child_task) is False
    evidence_contract = child_task["context"]["verificationEvidenceContract"]
    assert evidence_contract["requiredCommands"] == ["python src/result.py"]
    assert evidence_contract["expectedStdout"] == ["exact-proof"]
    assert evidence_contract["expectEmptyStderr"] is True


def test_grandchild_fallback_projects_evidence_without_parent_topology_acceptance():
    parent_task = normalize_task_brief(
        {
            "taskBriefId": "parent-topology",
            "goal": "Fix and verify the Python target.",
            "writeSet": ["src/result.py"],
            "expectedOutputs": ["Implementation handoff", "grandchild handoff"],
            "acceptanceContract": {
                "must": [
                    "直接子 Agent must delegate a 孙 Agent and return its parentEpisodeId.",
                    "孙 Agent 独立读取并实际执行 src/result.py.",
                    "执行退出码为 0，stdout 严格为 exact-result，stderr 为空.",
                    "Supervisor must accept the final handoff.",
                ]
            },
            "requireChildDelegation": True,
        }
    )

    request = _fallback_child_delegation_request(
        branch={
            "invocationId": "invoke-topology",
            "delegationId": "delegation-topology",
            "delegationDepth": 1,
            "reason": parent_task["goal"],
            "taskBrief": parent_task,
        },
        summary={},
    )

    child_task = request["childTaskBrief"]
    query_text = task_brief_query_text(child_task)
    acceptance_text = json.dumps(child_task["acceptanceContract"], ensure_ascii=False)
    assert "parentEpisodeId" not in acceptance_text
    assert "Supervisor" not in acceptance_text
    assert "delegate a 孙 Agent" not in acceptance_text
    assert "python src/result.py" in acceptance_text
    assert "exact-result" in acceptance_text
    assert "inheritedEngineeringContract" not in query_text
    assert "parentEpisodeId" not in query_text
    assert "python src/result.py" in query_text
    assert "exact-result" in query_text
    assert child_task["expectedOutputs"] == [
        "Successful read evidence for every declared verification path",
        "Successful command evidence with command, exit code, exact stdout, and stderr",
        "Compact independent verification handoff for the parent Agent",
    ]
    assert child_task["toolPolicy"] == {"mode": "default"}
    assert child_task["context"]["verificationEvidenceContract"] == {
        "requiredReadPaths": ["src/result.py"],
        "requiredCommands": ["python src/result.py"],
        "requiredCommandTargets": [],
        "expectedStdout": ["exact-result"],
        "expectEmptyStderr": True,
    }


def test_task_brief_query_keeps_peer_scope_and_dependency_evidence_out_of_user_like_instruction():
    task = normalize_task_brief(
        {
            "taskBriefId": "skeleton",
            "goal": "Create only the package skeleton.",
            "writeSet": ["src/package.py"],
            "context": {
                "notes": "Use the existing project conventions.",
                "activeCollaborators": [
                    {
                        "taskBriefId": "reports",
                        "name": "Implementation Engineer",
                        "workSummary": "Generate reports/result.json.",
                    }
                ],
                "collaborationBoundary": "Do not absorb peer work.",
                "dependencyResults": [{"taskBriefId": "research", "summary": "Evidence ready."}],
            },
            "expectedOutputs": ["Package skeleton"],
            "acceptanceContract": ["src/package.py exists"],
        }
    )

    query_text = task_brief_query_text(task)

    assert "Create only the package skeleton." in query_text
    assert "Use the existing project conventions." in query_text
    assert "Generate reports/result.json" not in query_text
    assert "Do not absorb peer work" not in query_text
    assert "Evidence ready" not in query_text


def test_explicit_terminal_child_requirement_overrides_inherited_grandchild_words():
    task = normalize_task_brief(
        {
            "taskBriefId": "terminal-verifier",
            "goal": "Verify the disposable grandchild result.",
            "acceptanceContract": [
                "孙 Agent 的 parentEpisodeId 指向直接子 Agent",
                "执行目标文件并返回 stdout",
            ],
            "allowChildDelegation": False,
            "requireChildDelegation": False,
            "childDelegationPolicyExplicit": True,
        }
    )

    assert task["allowChildDelegation"] is False
    assert task["requireChildDelegation"] is False
    assert task_brief_requires_child_delegation(task) is False


def test_negative_child_delegation_acceptance_is_not_reversed_into_a_requirement():
    task = normalize_task_brief(
        {
            "taskBriefId": "direct-verifier",
            "goal": "Verify the result without another worker.",
            "acceptanceContract": {
                "must": [
                    "不得创建或委派孙 Agent；全部验证必须由 Verification Engineer 本人完成",
                    "Do not create a child agent or nested delegation.",
                ]
            },
            "allowChildDelegation": False,
            "childDelegationPolicyExplicit": True,
        }
    )

    assert task["allowChildDelegation"] is False
    assert task["requireChildDelegation"] is False
    assert task_brief_requires_child_delegation(task) is False


def test_task_brief_normalization_preserves_explicit_verification_evidence_contract():
    task = normalize_task_brief(
        {
            "taskBriefId": "verify-structured-evidence",
            "goal": "Verify one file.",
            "verificationEvidenceContract": {
                "requiredReadPaths": ["src/result.py"],
                "requiredCommands": ["python src/result.py"],
                "expectedStdout": ["exact-result"],
            },
        }
    )

    assert task["verificationEvidenceContract"] == {
        "requiredReadPaths": ["src/result.py"],
        "requiredCommands": ["python src/result.py"],
        "expectedStdout": ["exact-result"],
    }
