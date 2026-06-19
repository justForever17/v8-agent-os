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


def test_nuwa_like_spec_tasks_are_distributed_without_route_layer_compression():
    bundle = _bundle()
    bundle["specId"] = "spec_nuwa"
    bundle["workspacePath"] = "E:/Projects/test2"
    bundle["documents"]["tasks"]["ids"] = [f"TASK-{index:03d}" for index in range(1, 12)]
    bundle["tasks"] = [
        *[
            {
                "taskId": f"TASK-{index:03d}",
                "title": f"调研维度 {index}",
                "runtimeLane": "Research",
                "expectedOutput": f"`references/research/{index:02d}-dimension.md`",
                "acceptance": "输出有来源的研究文件。",
                "taskExcerpt": "按女娲方法调研一个独立维度并写入对应 research markdown。",
            }
            for index in range(1, 7)
        ],
        {
            "taskId": "TASK-007",
            "title": "初始化 skill 目录",
            "runtimeLane": "Engineering",
            "expectedOutput": "创建目录，不写核心内容。",
            "taskExcerpt": "创建目标 skill 目录和 references/research 目录。",
        },
        {
            "taskId": "TASK-008",
            "title": "构建 SKILL.md",
            "runtimeLane": "Engineering",
            "expectedOutput": "`SKILL.md`",
            "dependsOn": [f"TASK-{index:03d}" for index in range(1, 7)] + ["TASK-007"],
            "taskExcerpt": "基于六份 research 文件生成 SKILL.md。",
        },
        {
            "taskId": "TASK-009",
            "title": "验证资料引用",
            "runtimeLane": "Research",
            "expectedOutput": "验证报告，确认来源和研究文件质量。",
            "dependsOn": ["TASK-008"],
            "taskExcerpt": "验证 skill 成品是否正确引用研究资料。",
        },
        {
            "taskId": "TASK-010",
            "title": "验证表达 DNA",
            "runtimeLane": "Research",
            "expectedOutput": "验证报告，确认表达 DNA 覆盖。",
            "dependsOn": ["TASK-008"],
            "taskExcerpt": "检查表达 DNA 是否符合女娲方法。",
        },
        {
            "taskId": "TASK-011",
            "title": "最终交付摘要",
            "runtimeLane": "Engineering",
            "expectedOutput": "最终交付摘要。",
            "dependsOn": ["TASK-007", "TASK-008", "TASK-009", "TASK-010"],
            "taskExcerpt": "汇总产物、验证结果和风险。",
        },
    ]

    briefs = _task_briefs_from_spec_bundle(bundle, "engineering")
    ids = [brief["taskBriefId"] for brief in briefs]

    assert ids == [f"TASK-{index:03d}" for index in range(1, 12)]
    assert all(briefs[index]["writeRequired"] for index in range(6))
    assert briefs[0]["engineeringTaskCapsule"]["expectedArtifacts"] == ["references/research/01-dimension.md"]
    assert briefs[7]["dependency"] == [f"TASK-{index:03d}" for index in range(1, 7)] + ["TASK-007"]
    assert briefs[8]["preferredAgentId"] == "verification-engineer"
    assert briefs[8]["dependency"] == ["TASK-008"]
    assert briefs[9]["preferredAgentId"] == "verification-engineer"
    assert briefs[9]["dependency"] == ["TASK-008"]
    assert briefs[10]["dependency"] == ["TASK-007", "TASK-008", "TASK-009", "TASK-010"]
