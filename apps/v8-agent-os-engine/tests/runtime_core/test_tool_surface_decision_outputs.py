from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from core.tool_surface import apply_tool_surface_budget
from runtimes.extensions.skills.loader import _read_skill_text_file


def _visible(tool_name: str, payload: dict, *, budget: int = 2500) -> str:
    message = ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        name=tool_name,
        tool_call_id=f"call-{tool_name}",
    )
    return str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": budget},
            tool_name=tool_name,
        ).content
    )


def _assert_not_json_wrapper(text: str) -> None:
    assert not text.lstrip().startswith("{")
    assert "_v8ToolSurface" not in text
    assert '"ok"' not in text
    assert "recommendedNextAction" not in text


def test_runtime_broker_default_is_decision_summary():
    visible = _visible(
        "runtime_broker",
        {
            "mode": "list",
            "ok": True,
            "availableGroups": [
                {"group": "research.core", "kind": "research", "label": "Research core"},
                {"group": "creative_media.core", "kind": "creative_media", "label": "Creative Media core"},
            ],
            "recommendedNextAction": "Grant one needed group.",
            "omitted": {"toolNames": 48},
        },
    )

    assert visible.startswith("Runtime route menu")
    assert "research.core" in visible
    assert "Runtime route menu" in visible
    assert "Candidate routes" in visible
    _assert_not_json_wrapper(visible)


def test_workspace_broker_default_hides_binding_and_tree():
    visible = _visible(
        "workspace_broker",
        {
            "ok": True,
            "kind": "workspace_inventory",
            "workspaceRoot": "E:\\Projects\\demo",
            "token": "abc1234567890",
            "nonEmpty": True,
            "topDirs": ["src", "tests", "node_modules"],
            "projectMarkers": [{"path": "package.json", "kind": "package.json"}],
            "workspaceBinding": {"large": "diagnostic"},
            "tree": [{"name": "src"}],
            "recommendedNextAction": "Continue existing project.",
        },
    )

    assert "Workspace inventory" in visible
    assert "E:\\Projects\\demo" in visible
    assert "package.json" in visible
    assert "workspaceBinding" not in visible
    assert '"tree"' not in visible
    _assert_not_json_wrapper(visible)


def test_research_plan_hides_shard_defaults():
    visible = _visible(
        "research_broker",
        {
            "ok": True,
            "mode": "plan",
            "kind": "research_plan",
            "question": "Compare official docs",
            "researchIntent": "source quality",
            "experienceFirstPolicy": {"summary": "Search reusable packs first."},
            "shardDefaults": {"allowedTools": ["web_search", "web_read"], "deadlineMs": 45000},
            "limits": {"effectiveMaxShards": 2, "effectiveMaxRounds": 1},
            "shards": [
                {"shardId": "shard_1", "kind": "baseline", "query": "Compare official docs", "reason": "baseline"},
                {"shardId": "shard_2", "kind": "official_docs", "query": "Compare official docs API", "reason": "official"},
            ],
        },
    )

    assert "Research plan" in visible
    assert "Shard briefs" in visible
    assert "allowedTools" not in visible
    assert "deadlineMs" not in visible
    _assert_not_json_wrapper(visible)


def test_research_result_pack_exposes_answer_sources_and_score_not_process_logs():
    visible = _visible(
        "research_broker",
        {
            "ok": True,
            "kind": "research_evidence_bundle",
            "question": "如何接入官方 API?",
            "researchAnswerPack": {
                "answer": "应优先使用官方 SDK，并按官方文档配置鉴权、重试和错误处理。",
                "sources": [
                    {
                        "title": "Official API Guide",
                        "url": "https://docs.example.com/api",
                        "host": "docs.example.com",
                    }
                ],
                "score": {"label": "high confidence; official source backed", "confidence": "high"},
                "limitations": ["缺少当前项目版本约束，需要结合本地 package 继续核对。"],
            },
            "finalExperiencePack": {
                "question": "如何接入官方 API?",
                "keyFindings": [
                    {
                        "claim": "官方 SDK 负责基础鉴权封装，但业务层仍需处理速率限制。",
                        "sourceTitle": "Official API Guide",
                    }
                ],
            },
            "sourceMatrix": [
                {
                    "title": "Search provider raw result",
                    "url": "https://search.example.com/raw",
                    "snippet": "provider-only diagnostic",
                }
            ],
            "loopReport": {"rounds": [{"query": "raw process log"}]},
            "architectPrompt": "diagnostic prompt must not leak",
        },
    )

    assert "Research result pack" in visible
    assert "应优先使用官方 SDK" in visible
    assert "官方 SDK 负责基础鉴权封装" in visible
    assert "Official API Guide" in visible
    assert "https://docs.example.com/api" in visible
    assert "high confidence" in visible
    assert "sourceMatrix" not in visible
    assert "loopReport" not in visible
    assert "architectPrompt" not in visible
    assert "provider-only diagnostic" not in visible
    _assert_not_json_wrapper(visible)


def test_computer_use_route_hides_matches_and_manual_controls():
    visible = _visible(
        "computer_use_resolve_execution_route",
        {
            "ok": True,
            "recommendedMode": "hybrid_mode",
            "recommendedTool": "computer_use_execute_task",
            "recommendedAction": "run_hybrid_with_computer_use",
            "recommendedMatch": {
                "id": "system.github.star_repository",
                "name": "GitHub Star Repository",
                "score": 0.41,
                "confidence": 0.81,
            },
            "matches": [{"id": "too much"}],
            "manualControls": {"humanCanApprove": True},
        },
    )

    assert "Computer Use route" in visible
    assert "system.github.star_repository" in visible
    assert "computer_use_execute_task" in visible
    assert "manualControls" not in visible
    assert '"matches"' not in visible
    _assert_not_json_wrapper(visible)


def test_creative_media_list_jobs_is_short_queue_surface():
    visible = _visible(
        "creative_media_list_jobs",
        {
            "ok": True,
            "statusCounts": {"failed": 8, "succeeded": 9},
            "jobs": [
                {
                    "jobId": "cm_15ff203da04c46b0a39506b5a9ade2c2",
                    "operationKind": "image.generate",
                    "status": "failed",
                    "providerId": "openai",
                    "model": "gpt-image-2",
                    "error": "RemoteProtocolError: Server disconnected without sending a response.",
                    "providerResponse": {"large": "raw"},
                }
            ],
            "detailTool": "creative_media_get_job(job_id=...)",
        },
    )

    assert "Creative Media jobs" in visible
    assert "Status: failed=8, succeeded=9" in visible
    assert "RemoteProtocolError" in visible
    assert "providerResponse" not in visible
    _assert_not_json_wrapper(visible)


def test_memory_broker_route_exposes_selected_evidence_not_ranking_matrix():
    visible = _visible(
        "memory_broker",
        {
            "ok": True,
            "mode": "route",
            "query": "之前调研过三月七吗",
            "selectedDomains": ["research_experience", "memory_core"],
            "summary": "Routed query to research experience and memory core.",
            "evidencePacks": [
                {
                    "sourceDomain": "research_experience",
                    "whySelected": "topic fingerprint and source-backed answer match the query.",
                    "confidence": "high",
                    "selectedEvidence": [
                        {
                            "id": "rxp_sanyueqi",
                            "title": "三月七角色调研",
                            "answer": "三月七是《崩坏：星穹铁道》的列车组成员，调研应以官方角色设定和剧情文本为主。",
                            "claimDigest": [
                                "三月七的表达风格偏活泼、直接，并常用拍照和记录作为角色行为线索。"
                            ],
                            "sources": [
                                {"title": "官方角色资料", "url": "https://sr.mihoyo.com/role/march7th"}
                            ],
                            "score": {"confidence": "high", "authorityScore": 78},
                            "rankingFeatures": {"internal": "do-not-show"},
                        }
                    ],
                    "rejectedEvidence": [
                        {"id": "rxp_noise", "reason": "low_quality_pack; source text unreadable"}
                    ],
                    "recommendedNextAction": "Reuse selected evidence only if the current task asks about the same character.",
                }
            ],
            "rankingMatrix": [{"candidate": "raw"}],
            "graphTraversal": {"internal": "raw"},
        },
        budget=4200,
    )

    assert "Memory broker: route" in visible
    assert "Evidence packs:" in visible
    assert "research_experience" in visible
    assert "三月七是《崩坏：星穹铁道》" in visible
    assert "三月七的表达风格偏活泼" in visible
    assert "https://sr.mihoyo.com/role/march7th" in visible
    assert "low_quality_pack" in visible
    assert "rankingMatrix" not in visible
    assert "graphTraversal" not in visible
    assert "rankingFeatures" not in visible
    _assert_not_json_wrapper(visible)


def test_computer_use_list_apps_limits_aliases_and_windows():
    visible = _visible(
        "computer_use_list_apps",
        {
            "ok": True,
            "count": 20,
            "apps": [
                {
                    "appId": "vscode",
                    "displayName": "Visual Studio Code",
                    "isRunning": True,
                    "launchable": True,
                    "topWindowTitle": "Codex",
                    "aliases": ["VS Code", "vscode", "visual studio code"],
                    "windows": [{"handle": 1}],
                }
            ],
        },
    )

    assert "Computer Use apps" in visible
    assert "vscode" in visible
    assert "VS Code" in visible
    assert "visual studio code" not in visible
    assert "windows" not in visible
    _assert_not_json_wrapper(visible)


def test_computer_use_observation_filters_blank_candidates():
    visible = _visible(
        "computer_use_observe_scene",
        {
            "ok": True,
            "summary": "Observed current scene.",
            "candidates": [
                {"confidence": 0.92},
                {"role": "Pane", "confidence": 0.9},
                {"name": "Search box", "confidence": 0.81},
                {"role": "button", "confidence": 0.7},
            ],
        },
    )

    assert "Computer Use observation" in visible
    assert "Search box" in visible
    assert "button" in visible
    assert "-  confidence=0.92" not in visible
    assert "Pane confidence" not in visible
    _assert_not_json_wrapper(visible)


def test_web_broker_search_exposes_sources_not_control_json():
    visible = _visible(
        "web_broker",
        {
            "ok": True,
            "mode": "search",
            "query": "V8 Agent OS runtime episode",
            "resultCount": 2,
            "sourceQualitySummary": {
                "quality": "mixed",
                "recommendedNextAction": "Read the official docs first.",
            },
            "results": [
                {
                    "title": "Runtime Episodes Guide",
                    "url": "https://example.com/runtime-episodes",
                    "snippet": "Canonical episode queue and typed handoff details.",
                    "sourceQualityHints": {"large": "diagnostic-only"},
                },
                {
                    "title": "Worker Leases",
                    "url": "https://example.com/leases",
                    "snippet": "Heartbeat and lease generation behavior.",
                },
            ],
            "trace": {"raw": "diagnostic"},
        },
    )

    assert "Web broker (search)" in visible
    assert "V8 Agent OS runtime episode" in visible
    assert "Runtime Episodes Guide" in visible
    assert "https://example.com/runtime-episodes" in visible
    assert "sourceQualityHints" not in visible
    assert '"trace"' not in visible
    _assert_not_json_wrapper(visible)


def test_web_broker_read_exposes_content_and_url():
    visible = _visible(
        "web_broker",
        {
            "ok": True,
            "mode": "read",
            "title": "Official API docs",
            "finalUrl": "https://example.com/api",
            "textPreview": "Use this endpoint to create durable runtime episodes.",
            "links": [{"title": "Reference", "url": "https://example.com/ref"}],
            "contentChars": 4096,
            "htmlChars": 9999,
            "usedBrowserProfile": False,
            "providerAttemptMatrix": [{"provider": "duckduckgo", "status": "ok"}],
            "networkRoute": "global_proxy",
            "sourceRouter": {"selectedProvider": "duckduckgo"},
        },
    )

    assert "Web broker (read)" in visible
    assert "Official API docs" in visible
    assert "https://example.com/api" in visible
    assert "durable runtime episodes" in visible
    assert "contentChars" not in visible
    assert "htmlChars" not in visible
    assert "usedBrowserProfile" not in visible
    assert "providerAttemptMatrix" not in visible
    assert "networkRoute" not in visible
    assert "sourceRouter" not in visible
    _assert_not_json_wrapper(visible)


def test_web_broker_read_preserves_useful_content_shape():
    visible = _visible(
        "web_broker",
        {
            "ok": True,
            "mode": "read",
            "title": "Reference page",
            "finalUrl": "https://example.com/reference",
            "text": "Steps:\n1. Create an episode.\n2. Wait for typed handoff.\n\n| Field | Meaning |\n| episodeId | durable run unit |",
        },
        budget=3200,
    )

    assert "Content:" in visible
    assert "1. Create an episode." in visible
    assert "| Field | Meaning |" in visible
    assert "https://example.com/reference" in visible
    _assert_not_json_wrapper(visible)


def test_delegation_broker_exposes_tasks_without_selection_diagnostics():
    visible = _visible(
        "delegation_broker",
        {
            "ok": True,
            "mode": "dispatch",
            "summary": "Dispatched 2 worker tasks.",
            "tasks": [
                {
                    "taskGoal": "Review research evidence.",
                    "target": "evidence-reviewer",
                    "status": "started",
                    "selectionTrace": {"large": "diagnostic-only"},
                },
                {
                    "taskGoal": "Draft implementation risks.",
                    "target": "engineering-reviewer",
                    "status": "queued",
                },
            ],
            "traceRef": "diag://trace",
        },
    )

    assert "Delegation broker (dispatch)" in visible
    assert "Review research evidence" in visible
    assert "engineering-reviewer" in visible
    assert "selectionTrace" not in visible
    assert "traceRef" not in visible
    _assert_not_json_wrapper(visible)


def test_fetch_skill_instructions_keeps_method_and_hides_loader_paths():
    message = ToolMessage(
        content=(
            "=== SKILL ENTRYPOINTS ===\n"
            "Skill Name: huashu-nuwa\n"
            "Skill Root: C:/Users/sunny/.agents/skills/huashu-nuwa\n"
            "Directory Structure: very large tree\n"
            "=== CONTINUATION MANIFEST ===\n"
            "- references/template.md\n"
            "=== INSTRUCTIONS SUMMARY ===\n"
            "Read the source material, extract the mental model, and produce a runnable skill.\n"
            "Never invent citations.\n"
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1600},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert visible.startswith("Skill instructions")
    assert "huashu-nuwa" not in visible
    assert "extract the mental model" in visible
    assert "Never invent citations" in visible
    assert "Skill Root:" not in visible
    assert "Directory Structure:" not in visible
    assert "C:/Users/sunny" not in visible
    assert visible.index("Instructions:") < visible.index("Continuation manifest")
    assert "not a replacement for the instructions above" in visible


def test_fetch_skill_instructions_drops_manifest_before_truncating_main_contract():
    main_body = "Main contract line.\n" * 80
    manifest_body = "- references/generated.md\n" * 200
    message = ToolMessage(
        content=(
            "Skill Name: big-skill\n"
            "=== CONTINUATION MANIFEST ===\n"
            f"{manifest_body}"
            "=== INSTRUCTIONS SUMMARY ===\n"
            f"{main_body}"
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill-big",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 2600},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert "Instructions:" in visible
    assert "Main contract line." in visible
    assert "Continuation manifest" not in visible
    assert "too large" not in visible


def test_fetch_skill_instructions_truncates_main_contract_with_same_document_offset():
    message = ToolMessage(
        content=(
            "Skill Name: too-large-skill\n"
            "=== INSTRUCTIONS SUMMARY ===\n"
            + ("Do not start from a partial skill contract.\n" * 120)
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill-too-large",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1200},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert visible.startswith("Skill instructions")
    assert "Do not start from a partial skill contract." in visible
    assert "main SKILL.md truncated at offset" in visible
    assert "fetch_skill_instructions(skill_name='too-large-skill', detail_level='full', offset=" in visible
    assert "do not start implementing from a partial SKILL.md" in visible
    assert "blocked until the complete main SKILL.md contract can be read" not in visible


def test_fetch_skill_relative_file_keeps_resource_document_content():
    message = ToolMessage(
        content=(
            "=== SKILL FILE ===\n"
            "Skill Name: demo-skill\n"
            "Relative Path: references/workflow.md\n"
            "Read Offset: 0\n"
            "Returned Chars: 86\n"
            "Total Chars: 86\n"
            "Next Offset: \n"
            "Continuation API: fetch_skill_instructions(skill_name='demo-skill', relative_path='<path>')\n\n"
            "=== FILE CONTENT ===\n"
            "# Workflow\n\n"
            "Step 1: read the whole resource document.\n"
            "Step 2: only then execute the method.\n"
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill-relative",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1800},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert visible.startswith("Skill file: references/workflow.md")
    assert "Contract: preserve this file's original order" in visible
    assert "# Workflow" in visible
    assert "Step 2: only then execute the method." in visible
    assert "Use the main SKILL.md instructions below" not in visible
    assert "Continuation manifest" not in visible


def test_fetch_skill_script_result_keeps_actionable_output_and_hides_runtime_shape():
    message = ToolMessage(
        content=(
            "=== SKILL SCRIPT RESULT ===\n"
            "Status: completed\n"
            "Script: scripts/check-quality.py\n"
            "Exit Code: 0\n"
            "Summary: 脚本执行成功。\n\n"
            "Output:\nquality score: 98\n\n"
            "Next Action: 继续按 SKILL.md 验证后续产物。"
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill-script",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1400},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert visible.startswith("=== SKILL SCRIPT RESULT ===")
    assert "quality score: 98" in visible
    assert "Next Action:" in visible
    assert "Skill instructions\n" not in visible
    assert "toolobs://" in visible


def test_fetch_skill_relative_file_truncates_with_same_document_offset():
    body = "Resource contract line.\n" * 80
    message = ToolMessage(
        content=(
            "=== SKILL FILE ===\n"
            "Skill Name: demo-skill\n"
            "Relative Path: references/large.md\n"
            "Read Offset: 1200\n"
            f"Returned Chars: {len(body)}\n"
            "Total Chars: 9000\n"
            "Next Offset: 3200\n"
            "Continuation API: fetch_skill_instructions(skill_name='demo-skill', relative_path='references/large.md', offset=3200)\n\n"
            "=== FILE CONTENT ===\n"
            f"{body}"
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill-relative-large",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1200},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert "Skill file: references/large.md" in visible
    assert "Resource contract line." in visible
    assert "skill relative file truncated at offset" in visible
    assert "relative_path='references/large.md'" in visible
    assert "offset=" in visible
    assert "inspect the raw file in workspace tools" not in visible


def test_skill_relative_file_reader_supports_offset_continuation(tmp_path):
    target = tmp_path / "references.md"
    target.write_text("abcdefg" * 10, encoding="utf-8")

    first, first_offset, total, truncated = _read_skill_text_file(target, max_chars=12, offset=0)
    second, second_offset, second_total, second_truncated = _read_skill_text_file(target, max_chars=12, offset=12)

    assert first == "abcdefgabcde"
    assert first_offset == 0
    assert total == 70
    assert truncated is True
    assert second == "fgabcdefgabc"
    assert second_offset == 12
    assert second_total == 70
    assert second_truncated is True


def test_read_audit_log_summarizes_json_body_without_long_raw_line():
    payload = {
        "skillName": "huashu-nuwa",
        "verdict": "audit",
        "confidence": 0.88,
        "reasons": ["发现 声明式密钥/环境变量依赖（11 个文件）。"],
        "flaggedFiles": [
            {
                "path": f"examples/example-{idx}/references/research/long-file.md",
                "severity": "low",
                "findings": [{"id": "secret_declaration", "reason": "发现 skill 需要 API Key、Token 或环境变量配置。"}],
            }
            for idx in range(20)
        ],
        "scannedFiles": 126,
        "candidateFiles": 126,
        "skillTrustScore": 56,
        "ledgerId": "skillreview_abc",
    }
    message = ToolMessage(
        content=(
            "[2026-06-12 01:52:21] [SAFETY] skill_scan - INFO: "
            + json.dumps(payload, ensure_ascii=False)
        ),
        name="read_audit_log",
        tool_call_id="call-read-audit-log",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 2500},
            tool_name="read_audit_log",
        ).content
    )

    assert visible.startswith("Audit log")
    assert "huashu-nuwa" in visible
    assert "verdict=audit" in visible
    assert "items=20" in visible
    assert "skillreview_abc" in visible
    assert "secret_declaration" not in visible
    assert '"flaggedFiles"' not in visible
    assert max(len(line) for line in visible.splitlines()) < 1200
    _assert_not_json_wrapper(visible)


def test_unknown_json_defaults_to_minimal_summary_with_detail_tool():
    visible = _visible(
        "new_experimental_tool",
        {
            "ok": True,
            "summary": "Created a candidate plan with two sources.",
            "results": [
                {"title": "Primary source", "url": "https://example.com/source", "snippet": "Useful fact."}
            ],
            "internalControl": {"token": "do-not-show"},
        },
    )

    assert visible.startswith("new experimental tool result")
    assert "Created a candidate plan" in visible
    assert "https://example.com/source" in visible
    assert "tool_observation_detail" in visible
    assert "internalControl" not in visible
    _assert_not_json_wrapper(visible)


def test_malformed_structured_output_never_exposes_partial_json():
    message = ToolMessage(
        content='{"ok": true, "summary": "cut off", "internal": {',
        name="new_experimental_tool",
        tool_call_id="call-malformed-json",
    )

    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1200},
            tool_name="new_experimental_tool",
        ).content
    )

    assert "incomplete structured output" in visible
    assert "tool_observation_detail" in visible
    assert not visible.lstrip().startswith("{")
    assert '"internal"' not in visible


def test_bracketed_control_message_is_not_misclassified_as_partial_json():
    message = ToolMessage(
        content="[route required] Engineering runtime must handle this write.",
        name="write_native_file",
        tool_call_id="call-route-required",
    )

    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1200},
            tool_name="write_native_file",
        ).content
    )

    assert "[route required]" in visible
    assert "incomplete structured output" not in visible
