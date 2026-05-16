from core.delegation_broker import expand_delegation_task_briefs


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
