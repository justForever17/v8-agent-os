from core.delegation_result_contract import build_delegation_result_contract


def test_delegation_result_contract_preserves_lineage_acceptance_and_artifact_evidence():
    contract = build_delegation_result_contract(
        {
            "taskBriefId": "TASK-002",
            "delegationId": "delegation-child",
            "parentDelegationId": "delegation-parent",
            "parentInvocationId": "invoke-parent",
            "delegationDepth": 2,
            "invocationId": "invoke-child",
            "targetId": "docs-worker",
            "targetLabel": "Docs Worker",
            "status": "ok",
            "artifactRefs": [{"path": "README.md"}],
            "missingExpectedArtifacts": ["PROOF.json"],
            "toolsUsed": ["write_native_file"],
            "availableTools": ["read_native_file", "write_native_file"],
            "requiredTool": "write_native_file",
            "requiredToolVisible": True,
            "toolCallCount": 3,
            "writeToolCallCount": 1,
            "writeToolSucceeded": True,
            "toolPolicy": {"mode": "allowlist", "allowedTools": ["write_native_file"]},
            "expectedOutputs": ["README.md"],
            "behaviorScope": ["Only update README.md"],
            "acceptanceContract": "README exists and is accurate.",
            "resultText": "README written exactly as requested.",
            "compactTranscript": "README written and checked.",
            "localSelfCheck": "README exists; proof file still missing.",
            "acceptanceHint": "Accept README, retry proof task.",
        }
    )

    assert contract["taskBriefId"] == "TASK-002"
    assert contract["parentDelegationId"] == "delegation-parent"
    assert contract["delegationDepth"] == 2
    assert contract["artifactRefs"] == [{"path": "README.md"}]
    assert contract["missingArtifactEvidence"] == ["PROOF.json"]
    assert contract["supervisorAcceptance"]["status"] == "pending"
    assert contract["resultSchemaMatched"] is True
    assert contract["toolsUsed"] == ["write_native_file"]
    assert contract["availableTools"] == ["read_native_file", "write_native_file"]
    assert contract["requiredTool"] == "write_native_file"
    assert contract["requiredToolVisible"] is True
    assert contract["writeToolSucceeded"] is True
    assert contract["toolPolicy"]["mode"] == "allowlist"
    assert contract["expectedOutputs"] == ["README.md"]
    assert contract["behaviorScope"] == ["Only update README.md"]
    assert contract["acceptanceContract"] == "README exists and is accurate."
    assert contract["resultText"] == "README written exactly as requested."
