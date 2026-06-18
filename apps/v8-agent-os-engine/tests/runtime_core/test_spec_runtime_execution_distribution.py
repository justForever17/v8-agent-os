from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.tools.native.runtime import _task_briefs_from_spec_bundle


def _bundle() -> dict:
    return {
        "kind": "SpecExecutionBundle",
        "status": "ready",
        "specId": "spec_demo",
        "workspacePath": "E:/Projects/test3",
        "documents": {
            "requirements": {
                "detailRef": "spec://spec_demo/requirements",
                "relativePath": ".v8/specs/demo/requirements.md",
                "ids": ["REQ-001", "REQ-002"],
                "content": (
                    "# Requirements\n\n"
                    "## REQ-001 Counter\n\nThe page SHALL show a counter.\n\n"
                    "## REQ-002 Documentation\n\nThe delivery SHALL include a README.\n"
                ),
            },
            "design": {
                "detailRef": "spec://spec_demo/design",
                "relativePath": ".v8/specs/demo/design.md",
                "ids": ["DES-001", "DES-002"],
                "content": (
                    "# Design\n\n"
                    "## DES-001 Browser implementation\n\nUse HTML, CSS and JavaScript. REQ-001\n\n"
                    "## DES-002 Documentation\n\nWrite a concise README. REQ-002\n"
                ),
            },
            "tasks": {
                "detailRef": "spec://spec_demo/tasks",
                "relativePath": ".v8/specs/demo/tasks.md",
                "ids": ["TASK-001", "TASK-002", "TASK-003"],
                "content": "# Tasks",
            },
        },
        "traceability": {"frameworkDigest": "HTML, CSS and JavaScript"},
        "tasks": [
            {
                "taskId": "TASK-001",
                "title": "Build the page",
                "runtimeLane": "Engineering",
                "specRefs": ["REQ-001", "DES-001"],
                "requirementRefs": ["REQ-001"],
                "designRefs": ["DES-001"],
                "expectedOutput": "`.v8/demo/index.html`",
                "acceptance": "The counter works.",
                "proofRequired": "File and browser proof.",
                "taskExcerpt": "Build `.v8/demo/index.html` from REQ-001 and DES-001.",
            },
            {
                "taskId": "TASK-002",
                "title": "Write README",
                "runtimeLane": "Engineering",
                "specRefs": ["REQ-002", "DES-002"],
                "requirementRefs": ["REQ-002"],
                "designRefs": ["DES-002"],
                "expectedOutput": "`.v8/demo/README.md`",
                "acceptance": "README explains how to open the page.",
                "proofRequired": "File proof.",
                "taskExcerpt": "Write `.v8/demo/README.md` from REQ-002 and DES-002.",
            },
            {
                "taskId": "TASK-003",
                "title": "验收所有交付物",
                "runtimeLane": "Engineering",
                "specRefs": ["REQ-001", "REQ-002"],
                "requirementRefs": ["REQ-001", "REQ-002"],
                "designRefs": ["DES-001", "DES-002"],
                "expectedOutput": "验收结论报告，逐项标记 Pass/Fail",
                "acceptance": "检查 index.html 与 README.md 后所有需求均为 Pass。",
                "proofRequired": "验收报告",
                "taskExcerpt": "验证已有 index.html 和 README.md，不创建新的交付文件。",
            },
        ],
    }


def test_spec_task_distribution_selects_execution_specialists_and_injects_stage_slices():
    briefs = _task_briefs_from_spec_bundle(_bundle(), "engineering")

    assert [brief["taskBriefId"] for brief in briefs] == ["TASK-001", "TASK-002", "TASK-003"]
    assert [brief["preferredAgentId"] for brief in briefs] == [
        "frontend-product-engineer",
        "docs-delivery-writer",
        "verification-engineer",
    ]
    assert [brief["writeRequired"] for brief in briefs] == [True, True, False]

    page_context = briefs[0]["context"]
    assert "REQ-001 Counter" in page_context["approvedRequirementSlice"]
    assert "REQ-002 Documentation" not in page_context["approvedRequirementSlice"]
    assert "DES-001 Browser implementation" in page_context["approvedDesignSlice"]
    assert page_context["specDocumentPaths"]["tasks"] == ".v8/specs/demo/tasks.md"
    assert "not URLs" in page_context["specRefUsage"]

    expected = [
        brief["engineeringTaskCapsule"]["expectedArtifacts"]
        for brief in briefs
    ]
    assert expected == [
        [".v8/demo/index.html"],
        [".v8/demo/README.md"],
        [],
    ]


def test_engineering_expected_artifact_check_requires_every_declared_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    briefs = _task_briefs_from_spec_bundle(_bundle(), "engineering")
    (workspace / ".v8" / "demo").mkdir(parents=True)
    (workspace / ".v8" / "demo" / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")

    missing = RuntimeEpisodeRunner._engineering_missing_expected_artifacts(
        workspace_path=str(workspace),
        worker_briefs=briefs,
    )

    assert missing == [".v8/demo/README.md"]
