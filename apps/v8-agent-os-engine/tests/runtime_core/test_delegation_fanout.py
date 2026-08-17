import json
from copy import deepcopy

from core.delegation_broker import (
    expand_delegation_task_briefs,
    normalize_task_brief,
    task_brief_contract_diagnostics,
    task_brief_query_text,
    task_brief_requires_child_delegation,
)
from core.engineering_capsule import effective_engineering_capsule
from core.tools.native.delegation import (
    _apply_legacy_dispatch_target_count,
    _compact_upstream_handoff_for_agent,
    _terminalize_grandchild_task_brief,
)
from graph.agent_factories import _format_delegated_task_contract
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
    recovered = normalize_task_brief(expanded[1])
    assert recovered["parentTaskBriefId"] == "audit"
    assert recovered["siblingIndex"] == 2
    assert recovered["siblingCount"] == 3
    assert "parentTaskBriefId" not in recovered.get("extensions", {})


def test_legacy_dispatch_target_count_uses_canonical_branch_identities() -> None:
    requested = _apply_legacy_dispatch_target_count(
        [
            {"taskBriefId": "research", "goal": "Collect evidence."},
            {"taskBriefId": "review", "goal": "Review the evidence."},
        ],
        4,
    )

    expanded = expand_delegation_task_briefs(requested)

    assert [item["taskBriefId"] for item in expanded] == [
        "research",
        "review#worker-1",
        "review#worker-2",
        "review#worker-3",
    ]
    assert [item["parentTaskBriefId"] for item in expanded[1:]] == ["review"] * 3


def test_task_brief_snake_aliases_round_trip_without_granting_unknown_policy() -> None:
    normalized = normalize_task_brief(
        {
            "task_brief_id": "alias-round-trip",
            "goal": "Preserve the complete verification contract.",
            "critical_files": ["core/runtime.py"],
            "verification_matrix": ["run focused tests"],
            "proof_expectations": ["test output"],
            "dependency": ["source-evidence"],
            "delegation_policy": {
                "allow_child_delegation": True,
                "child_delegation_budget": {"maxChildren": 1, "maxDepth": 1},
                "selectionRationale": "Prefer an independent verifier.",
            },
        }
    )

    assert normalized["criticalFiles"] == ["core/runtime.py"]
    assert normalized["verificationMatrix"] == ["run focused tests"]
    assert normalized["proofExpectations"] == ["test output"]
    assert normalized["dependencies"] == normalized["dependency"] == ["source-evidence"]
    assert normalized["delegationPolicy"] == {
        "allowChildDelegation": True,
        "requireChildDelegation": False,
        "childDelegationBudget": {"maxChildren": 1, "maxDepth": 1},
        "writeSetPartitions": [],
    }
    assert normalized["extensions"]["delegationPolicy.selectionRationale"] == (
        "Prefer an independent verifier."
    )
    assert "delegationPolicy.selectionRationale" in normalized["unsupportedFields"]
    assert "selectionRationale" not in normalized["delegationPolicy"]

    renormalized = normalize_task_brief(normalized)
    for field in (
        "criticalFiles",
        "verificationMatrix",
        "proofExpectations",
        "dependencies",
        "delegationPolicy",
        "extensions",
        "unsupportedFields",
    ):
        assert renormalized[field] == normalized[field]


def test_task_brief_alias_conflicts_remain_typed_and_do_not_guess() -> None:
    normalized = normalize_task_brief(
        {
            "taskBriefId": "alias-conflict",
            "task_brief_id": "different-id",
            "goal": "Report conflicts before execution.",
            "criticalFiles": ["canonical.py"],
            "critical_files": ["snake.py"],
            "delegationPolicy": {
                "allowChildDelegation": False,
                "allow_child_delegation": True,
            },
        }
    )

    diagnostics = normalized["contractDiagnostics"]
    assert {item["field"] for item in diagnostics} == {
        "taskBriefId",
        "criticalFiles",
        "delegationPolicy.allowChildDelegation",
    }
    assert all(item["code"] == "task_brief_alias_conflict" for item in diagnostics)
    assert all("values" not in item for item in diagnostics)


def test_worker_brief_alias_conflicts_are_reported_before_macro_expansion() -> None:
    diagnostics = task_brief_contract_diagnostics(
        [
            {
                "taskBriefId": "parent",
                "goal": "Review two bounded scopes.",
                "workerBriefs": [
                    {
                        "taskBriefId": "worker-a",
                        "task_brief_id": "worker-other",
                        "writeSet": ["safe.py"],
                        "write_set": ["other.py"],
                        "toolPolicy": {
                            "allowedTools": ["read_native_file"],
                            "allowed_tools": ["run_system_command"],
                        },
                        "delegationPolicy": {
                            "allowChildDelegation": True,
                            "allow_child_delegation": False,
                        },
                        "engineeringTaskCapsule": {
                            "writeSet": [],
                            "write_set": ["danger.py"],
                        },
                    }
                ],
            }
        ]
    )

    conflicts = diagnostics["aliasConflicts"]
    assert {item["field"] for item in conflicts} == {
        "taskBriefId",
        "writeSet",
        "toolPolicy.allowedTools",
        "delegationPolicy.allowChildDelegation",
        "engineeringTaskCapsule.writeSet",
    }
    assert all(item["parentIndex"] == 0 for item in conflicts)
    assert all(item["parentTaskBriefId"] == "parent" for item in conflicts)
    assert all(item["workerIndex"] == 0 for item in conflicts)


def test_engineering_capsule_presence_does_not_promote_conflicting_legacy_alias() -> None:
    capsule = effective_engineering_capsule(
        {
            "taskBriefId": "explicit-empty-write-set",
            "engineeringTaskCapsule": {
                "writeSet": [],
                "write_set": ["danger.py"],
            },
        }
    )

    assert capsule["writeSet"] == []
    assert capsule["writeRequired"] is False
    assert capsule["executionMode"] == "read_only"


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


def test_worker_brief_preserves_complete_contract_and_visible_scoped_context():
    expanded = expand_delegation_task_briefs(
        [
            {
                "taskBriefId": "audit",
                "goal": "Audit two independent surfaces.",
                "context": {
                    "shared": "parent facts",
                    "toolPolicy": {"allowedTools": ["must-not-leak-tool"]},
                    "handoffContract": {"rawRef": "must-not-leak.py"},
                    "apiToken": "must-not-leak",
                    "auth": "Bearer PARENT-AUTH-SECRET",
                    "tokenBudget": 900,
                    "cookiePolicy": "same-site",
                    "authorizationMode": "interactive",
                },
                "expectedOutputs": ["generic output"],
                "dependencies": ["parent-step"],
                "targetCount": 2,
                "workerBriefs": [
                    {
                        "schema_version": "v8.task_brief.v2",
                        "goal": "Audit surface A.",
                        "context": {
                            "approvedClaim": "surface-a-only",
                            "headers": {"auth": "Bearer WORKER-AUTH-SECRET"},
                        },
                        "expected_output": "surface A report",
                        "expected_artifacts": ["reports/a.json"],
                        "constraint": ["Do not inspect surface B."],
                        "evidence_refs": ["research://episode_surface_a/evidence"],
                        "detail_refs": ["detail://surface-a"],
                        "spec_refs": {"specId": "SPEC-A", "taskId": "TASK-A"},
                        "dependency": ["source-a"],
                        "side_effect_policy": {"mode": "none"},
                        "execution_budget": {"maxTokens": 1200},
                        "failure_policy": {"onError": "return_blocker"},
                        "target_agent_name": "Verification Engineer",
                        "futureEvidencePolicy": {
                            "claims": [{"claimId": "surface-a", "required": True}]
                        },
                    },
                    {"goal": "Audit surface B."},
                ],
            }
        ]
    )

    worker = expanded[0]
    assert worker["schemaVersion"] == "v8.task_brief.v2"
    assert worker["expectedOutputs"] == ["surface A report"]
    assert worker["expectedArtifacts"] == ["reports/a.json"]
    assert worker["constraints"] == ["Do not inspect surface B."]
    assert worker["evidenceRefs"] == ["research://episode_surface_a/evidence"]
    assert worker["detailRefs"] == ["detail://surface-a"]
    assert worker["specRefs"] == {"specId": "SPEC-A", "taskId": "TASK-A"}
    assert worker["dependencies"] == worker["dependency"] == ["source-a"]
    assert worker["sideEffectPolicy"] == {"mode": "none"}
    assert worker["budget"] == {"maxTokens": 1200}
    assert worker["failurePolicy"] == {"onError": "return_blocker"}
    assert worker["targetAgentName"] == "Verification Engineer"
    assert worker["extensions"]["futureEvidencePolicy"] == {
        "claims": [{"claimId": "surface-a", "required": True}]
    }
    assert worker["unsupportedFields"] == ["futureEvidencePolicy"]

    prompt = _format_delegated_task_contract(worker)
    assert "Shared Context" in prompt
    assert "Worker Context" in prompt
    assert "surface-a-only" in prompt
    assert "parent facts" in prompt
    assert "must-not-leak.py" not in prompt
    assert "must-not-leak" not in prompt
    assert "PARENT-AUTH-SECRET" not in prompt
    assert "WORKER-AUTH-SECRET" not in prompt
    assert "<redacted>" in prompt
    assert "tokenBudget" in prompt
    assert "cookiePolicy" in prompt
    assert "authorizationMode" in prompt
    assert '"claimId": "surface-a"' in prompt
    assert "'claimId'" not in prompt


def test_task_query_redacts_auth_without_erasing_non_secret_policy_context():
    query = task_brief_query_text(
        {
            "taskBriefId": "safe-query",
            "goal": "Review the supplied context.",
            "context": {
                "headers": {"auth": "Bearer DIRECT-AUTH-SECRET"},
                "tokenBudget": 700,
                "cookiePolicy": "same-site",
                "authorizationMode": "interactive",
            },
        }
    )

    assert "DIRECT-AUTH-SECRET" not in query
    assert "<redacted>" in query
    assert "tokenBudget" in query
    assert "cookiePolicy" in query
    assert "authorizationMode" in query


def test_compact_upstream_handoff_reports_omissions_and_preserves_recovery_contract():
    compact = _compact_upstream_handoff_for_agent(
        {
            "producerEpisodeId": "episode-parent",
            "handoffRefId": "handoff-parent",
            "kind": "research_evidence_bundle",
            "status": "ready",
            "compactSummary": "A" * 3500 + "B" * 3500,
            "refs": [f"research://source/{index}" for index in range(10)],
            "researchRefs": ["research://source/9", "research://source/10"],
            "proofRefs": [f"proof://{index}" for index in range(9)],
            "detailRef": "research://bundle/evidence-parent",
            "rawRef": "toolobs://runtime-parent",
            "detailTool": "research_broker(mode='get_evidence', evidence_bundle_id='evidence-parent')",
            "childHandoffs": [
                {
                    "producerEpisodeId": f"episode-child-{index}",
                    "handoffRefId": f"handoff-child-{index}",
                    "status": "ready",
                    "summary": f"child {index}",
                }
                for index in range(8)
            ],
        }
    )

    assert len(compact["summary"]) == 6000
    assert "[truncated]" in compact["summary"]
    assert compact["summary"].endswith("B" * 10)
    assert compact["refs"] == [f"research://source/{index}" for index in range(8)]
    assert len(compact["childResults"]) == 6
    assert compact["detailRef"] == "research://bundle/evidence-parent"
    assert compact["rawRef"] == "toolobs://runtime-parent"
    assert compact["detailTool"].startswith("research_broker")

    truncation = compact["truncation"]
    assert truncation["truncated"] is True
    assert truncation["omittedUnit"] == "fields"
    assert truncation["omittedCount"] == len(truncation["omittedByField"])
    assert truncation["omittedByField"]["summary"]["unit"] == "characters"
    assert truncation["omittedByField"]["refs"] == {"omittedCount": 3, "unit": "items"}
    assert truncation["omittedByField"]["proofRefs"] == {"omittedCount": 1, "unit": "items"}
    assert truncation["omittedByField"]["childResults"] == {"omittedCount": 2, "unit": "items"}
    assert truncation["recoveryAvailable"] is True
    assert truncation["recoveryRefs"] == {
        "detailRef": "research://bundle/evidence-parent",
        "rawRef": "toolobs://runtime-parent",
    }
    assert "tool_observation_detail(rawRef)" in truncation["recoveryTools"]


def test_compact_upstream_handoff_distinguishes_parent_redelivery_from_child_readable_ref():
    compact = _compact_upstream_handoff_for_agent(
        {
            "handoffRefId": "handoff-parent-only",
            "status": "ready",
            "summary": "missing-detail-" * 700,
        }
    )

    truncation = compact["truncation"]
    assert truncation["recoveryAvailable"] is False
    assert truncation["parentRecoveryRef"] == "handoff-parent-only"
    assert "not as a child-readable URI" in truncation["recoveryGuidance"]
    assert "recoveryRefs" not in truncation


def test_compact_upstream_handoff_accepts_non_toolobs_raw_ref_with_explicit_detail_tool():
    compact = _compact_upstream_handoff_for_agent(
        {
            "handoffRefId": "handoff-media-detail",
            "status": "ready",
            "summary": "media evidence " * 700,
            "rawRef": "creative-ledger://asset/42",
            "detailTool": "creative_media_assets(action='get', assetId='42')",
        }
    )

    truncation = compact["truncation"]
    assert truncation["recoveryAvailable"] is True
    assert truncation["recoveryRefs"]["rawRef"] == "creative-ledger://asset/42"
    assert truncation["recoveryTools"] == [
        "creative_media_assets(action='get', assetId='42')"
    ]


def test_prompt_truncation_exposes_omission_size_and_exact_recovery_refs():
    prompt = _format_delegated_task_contract(
        {
            "taskBriefId": "prompt-truncation",
            "goal": "Use bounded shared and worker context without assuming omitted facts.",
            "context": {
                "sharedContext": {
                    "detailRef": "detail://shared-context",
                    "detailTool": "read_section(detailRef)",
                    "facts": "shared-start-" + ("S" * 4200) + "-shared-tail",
                },
                "workerContext": {
                    "rawRef": "toolobs://worker-context",
                    "notes": "worker-start-" + ("W" * 3200) + "-worker-tail",
                },
            },
        }
    )

    assert "Shared Context" in prompt
    assert "Worker Context" in prompt
    assert '"omittedCount":' in prompt
    assert '"omittedUnit": "characters"' in prompt
    assert '"detailRef": "detail://shared-context"' in prompt
    assert '"rawRef": "toolobs://worker-context"' in prompt
    assert "tool_observation_detail(rawRef)" in prompt
    assert "shared-tail" not in prompt
    assert "worker-tail" not in prompt


def test_prompt_redacts_sensitive_extension_values_without_mutating_runtime_contract():
    task_brief = {
        "taskBriefId": "extension-redaction",
        "goal": "Consume forward-compatible diagnostics.",
        "extensions": {
            "provider": {
                "apiKey": "sk-live-do-not-render",
                "accessToken": "access-do-not-render",
                "refresh_token": "refresh-do-not-render",
                "privateKey": "private-do-not-render",
                "openaiApiKey": "provider-key-do-not-render",
                "githubToken": "github-do-not-render",
                "tokenBudget": 4096,
            },
            "password": "password-do-not-render",
            "cookie": "cookie-do-not-render",
            "auth": "Bearer auth-do-not-render",
            "authorization": "Bearer do-not-render",
            "diagnosticMode": "bounded",
        },
        "unsupportedFields": ["provider", "password", "cookie", "authorization", "diagnosticMode"],
    }
    canonical_before = deepcopy(task_brief)

    prompt = _format_delegated_task_contract(task_brief)

    assert task_brief == canonical_before
    for secret in (
        "sk-live-do-not-render",
        "access-do-not-render",
        "refresh-do-not-render",
        "private-do-not-render",
        "provider-key-do-not-render",
        "github-do-not-render",
        "password-do-not-render",
        "cookie-do-not-render",
        "auth-do-not-render",
        "Bearer do-not-render",
    ):
        assert secret not in prompt
    assert prompt.count("<redacted>") >= 10
    assert '"tokenBudget": 4096' in prompt
    assert '"diagnosticMode": "bounded"' in prompt
    assert "Extensions redaction" in prompt
    assert "extensions.provider.apiKey" in prompt
    assert "canonical runtime values were not modified" in prompt
    assert "Unsupported Fields" in prompt


def test_worker_brief_narrowing_replaces_parent_capsule_and_tool_policy_duplicates():
    expanded = expand_delegation_task_briefs(
        [
            {
                "taskBriefId": "implementation",
                "goal": "Implement two independent files.",
                "writeRequired": True,
                "writeSet": ["src/a.py", "src/b.py"],
                "expectedArtifacts": ["src/a.py", "src/b.py"],
                "expectedOutputs": ["working implementation"],
                "acceptanceContract": ["Both files pass focused verification."],
                "toolPolicy": {
                    "mode": "allowlist",
                    "allowedTools": [
                        "read_native_file",
                        "write_native_file",
                        "run_system_command",
                    ],
                },
                "targetCount": 2,
                "workerBriefs": [
                    {
                        "goal": "Implement only src/a.py.",
                        "write_set": ["src/a.py"],
                        "tool_policy": {
                            "mode": "allowlist",
                            "allowedTools": ["read_native_file", "write_native_file"],
                        },
                    },
                    {"goal": "Implement only src/b.py.", "writeSet": ["src/b.py"]},
                ],
            }
        ]
    )

    first = expanded[0]
    assert first["writeSet"] == ["src/a.py"]
    assert first["expectedArtifacts"] == ["src/a.py"]
    assert first["engineeringTaskCapsule"]["writeSet"] == ["src/a.py"]
    assert first["engineeringTaskCapsule"]["expectedArtifacts"] == ["src/a.py"]
    assert first["engineeringTaskCapsule"]["taskId"] == "implementation#worker-1"
    assert first["allowedTools"] == ["read_native_file", "write_native_file"]
    assert first["toolPolicy"]["allowedTools"] == [
        "read_native_file",
        "write_native_file",
    ]
    assert "run_system_command" not in first["allowedTools"]


def test_explicit_extensions_are_always_marked_non_authoritative():
    brief = normalize_task_brief(
        {
            "taskBriefId": "extension-context",
            "goal": "Use forward context without granting authority.",
            "extensions": {
                "allowedTools": ["write_native_file"],
                "sideEffectPolicy": {"mode": "unbounded"},
            },
        }
    )

    assert brief["unsupportedFields"] == ["allowedTools", "sideEffectPolicy"]
    prompt = _format_delegated_task_contract(brief)
    assert "Extension discipline" in prompt
    assert "do not grant tools, side effects, workspace writes, or routing authority" in prompt


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


def test_terminal_grandchild_projects_runtime_tools_and_plugins_to_parent_scope():
    parent = {
        "runtimeAccess": ["research.core", "delegation.recursive"],
        "allowedTools": ["read_native_file", "research_broker"],
        "pluginReferences": [
            {"pluginId": "docs", "componentIds": ["read", "cite"]},
        ],
    }
    child = {
        "runtimeAccess": ["research.core", "memory.read", "delegation.recursive"],
        "allowedTools": ["read_native_file", "run_system_command", "delegation_broker"],
        "pluginReferences": [
            {"pluginId": "docs", "componentIds": ["cite", "write"]},
            {"pluginId": "unavailable", "componentIds": ["x"]},
        ],
        "toolPolicy": {
            "allowedTools": ["read_native_file", "run_system_command", "delegation_broker"]
        },
    }

    terminal = _terminalize_grandchild_task_brief(child, parent_task_brief=parent)

    assert terminal["runtimeAccess"] == ["research.core"]
    assert terminal["allowedTools"] == ["read_native_file"]
    assert terminal["toolPolicy"]["mode"] == "allowlist"
    assert terminal["toolPolicy"]["allowedTools"] == ["read_native_file"]
    assert "delegation_broker" in terminal["toolPolicy"]["forbiddenTools"]
    assert terminal["pluginReferences"] == [
        {"pluginId": "docs", "componentIds": ["cite"]}
    ]
    assert terminal["allowChildDelegation"] is False

    equal_scope = _terminalize_grandchild_task_brief(
        {
            "runtimeAccess": ["research.core"],
            "pluginReferences": [
                {"pluginId": "docs", "componentIds": ["read", "cite"]},
            ],
        },
        parent_task_brief=parent,
    )
    assert equal_scope["pluginReferences"] == []


def test_terminal_grandchild_fails_closed_when_parent_has_no_explicit_scope():
    terminal = _terminalize_grandchild_task_brief(
        {
            "runtimeAccess": ["research.core"],
            "allowedTools": ["research_broker"],
            "pluginReferences": [{"pluginId": "docs", "componentIds": ["read"]}],
            "toolPolicy": {"allowedTools": ["research_broker"]},
        },
        parent_task_brief={"goal": "Verify one bounded result."},
    )

    assert terminal["runtimeAccess"] == []
    assert terminal["allowedTools"] == []
    assert terminal["toolPolicy"]["mode"] == "none"
    assert terminal["toolPolicy"]["allowedTools"] == []
    assert terminal["pluginReferences"] == []


def test_terminal_grandchild_keeps_default_read_verifier_surface_when_parent_is_default():
    terminal = _terminalize_grandchild_task_brief(
        {
            "goal": "Independently verify the parent result.",
            "runtimeAccess": [],
            "toolPolicy": {"mode": "default", "allowedTools": []},
        },
        parent_task_brief={
            "goal": "Produce the result to verify.",
            "runtimeAccess": [],
            "toolPolicy": {"mode": "default", "allowedTools": []},
        },
        parent_resolved_tools=["read_native_file", "tool_observation_detail"],
    )

    assert terminal["toolPolicy"]["mode"] == "default"
    assert terminal["allowedTools"] == []
    assert "delegation_broker" in terminal["forbiddenTools"]
