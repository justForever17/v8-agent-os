from core.runtime_episode_runner import RuntimeEpisodeRunner
import core.tools.native.runtime as runtime_tool_module
from core.tools.native.runtime import (
    _approved_spec_execution_bundle,
    _enrich_route_need_for_episode,
    _resolve_spec_expected_paths,
    _task_briefs_from_spec_bundle,
    _task_sections_from_markdown,
)


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
    assert any(
        "Do not execute generated" in item
        for item in briefs[0]["context"]["engineeringExecutionContract"]["forbiddenScopes"]
    )


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


def test_backtick_table_fields_and_noisy_prose_do_not_expand_spec_write_set():
    tasks = _task_sections_from_markdown(
        """
## TASK-001 Build page

| field | value |
| --- | --- |
| `runtimeLane` | Engineering |
| `output` | `index.html` |
| `inputRefs` | `package.json`, `file://E:/input/mockup.png` |
| `acceptance` | Read package.json but only write index.html. |

## TASK-002 Write docs

| `runtimeLane` | Engineering |
| `output` | `README.md` |
| `acceptance` | Link to index.html without modifying it. |

## TASK-003 Record proof

| `runtimeLane` | Engineering |
| `expectedArtifacts` | `PROOF.json` |
| `acceptance` | Inspect the workspace root and input files. |
""",
        ["TASK-001", "TASK-002", "TASK-003"],
    )
    bundle = _bundle()
    bundle["tasks"] = tasks

    briefs = _task_briefs_from_spec_bundle(bundle, "engineering")

    assert [brief["writeSet"] for brief in briefs] == [["index.html"], ["README.md"], ["PROOF.json"]]
    flattened = [path for brief in briefs for path in brief["writeSet"]]
    assert "package.json" not in flattened
    assert not any(path.startswith("file://") for path in flattened)
    assert bundle["workspacePath"] not in flattened


def test_markdown_list_blocks_preserve_output_paths_and_keep_audit_read_only():
    tasks = _task_sections_from_markdown(
        """
## TASK-001 Author `index.html`

- **runtimeLane**: Engineering
- **expected output path**:
  - `.v8/live/index.html`
  - Single self-contained HTML file.
- **acceptance / proof**:
  - File exists and the counter works.

## TASK-002 Author `README.md`

- **runtimeLane**: Engineering
- **expected output path**:
  - `.v8/live/README.md`
- **acceptance / proof**:
  - File explains how to open the page.

## TASK-003 Live marker audit

- **runtimeLane**: Engineering static audit
- **expected output**:
  - A short audit note with grep evidence for index.html and README.md.
- **acceptance / proof**:
  - Existing files contain the marker; do not create another file.
""",
        ["TASK-001", "TASK-002", "TASK-003"],
    )
    bundle = _bundle()
    bundle["tasks"] = tasks

    briefs = _task_briefs_from_spec_bundle(bundle, "engineering")

    assert [brief["writeRequired"] for brief in briefs] == [True, True, False]
    assert [brief["writeSet"] for brief in briefs] == [
        [".v8/live/index.html"],
        [".v8/live/README.md"],
        [],
    ]
    assert briefs[2]["preferredAgentId"] == "verification-engineer"


def test_bare_spec_outputs_resolve_under_single_authoritative_target_directory():
    bundle = _bundle()
    bundle["targetOutputDirectories"] = [".v8/live-audit/spec-mode-v2/demo"]
    bundle["tasks"] = [
        {
            "taskId": "TASK-001",
            "title": "Build page",
            "runtimeLane": "Engineering",
            "expectedOutput": "`index.html`",
            "acceptance": "The page works.",
        },
        {
            "taskId": "TASK-002",
            "title": "Write docs",
            "runtimeLane": "Engineering",
            "expectedOutput": "`README.md`",
            "acceptance": "The documentation is complete.",
        },
    ]

    briefs = _task_briefs_from_spec_bundle(bundle, "engineering")

    assert [brief["writeSet"] for brief in briefs] == [
        [".v8/live-audit/spec-mode-v2/demo/index.html"],
        [".v8/live-audit/spec-mode-v2/demo/README.md"],
    ]


def test_spec_output_resolution_does_not_guess_with_ambiguous_targets():
    assert _resolve_spec_expected_paths(["index.html"], ["dist/a", "dist/b"]) == ["index.html"]
    assert _resolve_spec_expected_paths(["custom/index.html"], ["dist"]) == ["custom/index.html"]


def test_approved_spec_workspace_ignores_model_supplied_output_subdirectory(monkeypatch):
    captured = {}

    def read_spec(*, workspace_path, spec_id, max_chars):
        captured.update(workspace_path=workspace_path, spec_id=spec_id, max_chars=max_chars)
        return {
            "specBrief": {
                "specId": spec_id,
                "workspacePath": "E:/Projects/product-root",
                "targetOutputDirectories": ["dist/release"],
                "explicitDeliverableFiles": ["index.html", "README.md"],
                "approvedStages": ["requirements", "design", "tasks"],
                "pipelineControl": {"runtimeExecutionAllowed": True},
            },
            "stages": {},
        }

    monkeypatch.setattr(runtime_tool_module.spec_service, "read_spec", read_spec)
    bundle = _approved_spec_execution_bundle(
        {"specId": "spec_demo"},
        {"workspacePath": "E:/Projects/product-root/output/index"},
        state={
            "current_route_context": {
                "specId": "spec_demo",
                "specBrief": {
                    "specId": "spec_demo",
                    "workspacePath": "E:/Projects/product-root",
                    "pipelineControl": {"runtimeExecutionAllowed": True},
                },
            }
        },
    )

    assert captured["workspace_path"] == "E:/Projects/product-root"
    assert bundle["workspacePath"] == "E:/Projects/product-root"
    assert bundle["targetOutputDirectories"] == ["dist/release"]
    assert bundle["explicitDeliverableFiles"] == ["index.html", "README.md"]


def test_execution_bundle_keeps_markdown_fields_when_traceability_values_are_blank(monkeypatch):
    tasks_markdown = """
## TASK-001 Author page

- **runtimeLane**: Engineering
- **expected output path**:
  - `.v8/live/index.html`
- **acceptance / proof**:
  - File exists.

## TASK-002 Audit page

- **runtimeLane**: Engineering static audit
- **expected output**:
  - A short audit note about index.html.
"""

    def read_spec(*, workspace_path, spec_id, max_chars):
        return {
            "specBrief": {
                "specId": spec_id,
                "workspacePath": workspace_path,
                "approvedStages": ["requirements", "design", "tasks"],
                "pipelineControl": {"runtimeExecutionAllowed": True},
                "traceability": {
                    "tasks": [
                        {"taskId": "TASK-001", "expectedOutput": "", "runtimeLane": "Engineering"},
                        {"taskId": "TASK-002", "expectedOutput": "", "runtimeLane": "Engineering static audit"},
                    ]
                },
            },
            "stages": {
                "tasks": {
                    "relativePath": ".v8/specs/demo/tasks.md",
                    "ids": ["TASK-001", "TASK-002"],
                    "content": tasks_markdown,
                }
            },
        }

    monkeypatch.setattr(runtime_tool_module.spec_service, "read_spec", read_spec)
    bundle = _approved_spec_execution_bundle(
        {"specId": "spec_demo"},
        {"workspacePath": "E:/Projects/product-root"},
        state={"workspace_path": "E:/Projects/product-root"},
    )
    briefs = _task_briefs_from_spec_bundle(bundle, "engineering")

    assert [brief["writeRequired"] for brief in briefs] == [True, False]
    assert briefs[0]["writeSet"] == [".v8/live/index.html"]
    assert briefs[1]["writeSet"] == []


def test_spec_write_task_without_explicit_artifact_is_blocked_before_dispatch(monkeypatch):
    bundle = _bundle()
    bundle["tasks"] = [
        {
            "taskId": "TASK-001",
            "title": "Write artifact",
            "runtimeLane": "Engineering",
            "taskExcerpt": "Write the requested artifact, then verify it.",
            "acceptance": "The implementation is complete.",
        }
    ]
    monkeypatch.setattr(runtime_tool_module, "_approved_spec_execution_bundle", lambda *_args, **_kwargs: bundle)

    enriched = _enrich_route_need_for_episode(
        {"specId": "spec_demo", "inputs": {}},
        kind="engineering",
        state={"current_route_context": {"specId": "spec_demo"}},
    )

    quality = enriched["inputs"]["routeBriefQuality"]
    assert quality["blocking"] is True
    assert quality["reason"] == "spec_write_artifact_contract_missing"
    assert quality["taskBriefIds"] == ["TASK-001"]
    assert enriched["inputs"]["workerBriefs"][0]["writeSet"] == []


def test_nuwa_like_spec_tasks_are_distributed_without_route_layer_compression():
    bundle = _bundle()
    bundle["specId"] = "spec_nuwa"
    bundle["workspacePath"] = "E:/Projects/test2"
    bundle["documents"]["requirements"]["content"] = (
        "# 玲（绝区零）角色视角 Skill 生成需求\n\n"
        "## 概述\n\n"
        "基于 huashu-nuwa 和 skill-creator，对米哈游游戏《绝区零》（Zenless Zone Zero）角色「玲」生成角色视角 Skill。\n\n"
        "## 调研对象\n\n"
        "- 角色：玲（Ling）\n"
        "- 作品：《绝区零》（Zenless Zone Zero）\n"
        "- 身份：法厄同之一，与哲共同经营绳匠业务。\n\n"
        "## 交付物清单\n\n"
        "- references/research/01-06.md\n"
        "- SKILL.md\n"
    )
    bundle["documents"]["design"]["content"] = (
        "# 玲（绝区零）角色视角 Skill 设计文档\n\n"
        "## 1. 总体架构\n\n"
        "huashu-nuwa 流程 → Research Runtime（多路调研）→ Engineering Runtime（文件构建）→ 验证交付。\n\n"
        "## 2. 调研策略设计\n\n"
        "每个 Research task 都必须保留《绝区零》、Zenless Zone Zero、玲、法厄同、哲这些公共身份线索。\n"
    )
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
    for brief in briefs:
        context = brief["context"]
        assert "绝区零" in context["sharedSpecContext"]
        assert "Zenless Zone Zero" in context["sharedSpecContext"]
        assert "法厄同" in context["extensionsRouteQuery"]
        assert "玲" in context["extensionsRouteQuery"]
        assert brief["routeQuery"] == context["extensionsRouteQuery"]
