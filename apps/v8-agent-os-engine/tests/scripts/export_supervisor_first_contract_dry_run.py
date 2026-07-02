from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.tools.native.automation import manage_cron, manage_hook, wait  # noqa: E402
from core.tools.native.computer_use import computer_use_execute_task, computer_use_observe  # noqa: E402
from core.tools.native.creative_media import (  # noqa: E402
    creative_media_create_job,
    creative_media_production_pack,
    creative_media_rank_models,
)
from core.tools.native.delegation import delegation_broker  # noqa: E402
from core.tools.native.runtime import runtime_broker  # noqa: E402
from core.tools.native.spec import spec_broker  # noqa: E402
from graph.supervisor_context import build_supervisor_system_content  # noqa: E402


REPORT_ROOT = REPO_ROOT / "docs" / "chatruntime" / "runtime_deep_observation_reports"


class _MemoryRuntimeStub:
    def recall(self, *args, **kwargs):
        return []

    def build_session_context(self, *args, **kwargs):
        return ""


def _tool_description(tool) -> str:
    return str(getattr(tool, "description", "") or getattr(tool, "__doc__", "") or "").strip()


def _contains_all(text: str, needles: list[str]) -> dict[str, bool]:
    return {needle: needle in text for needle in needles}


def main() -> int:
    with patch("graph.supervisor_context._build_workspace_rules_context", return_value=("", [])), patch(
        "graph.supervisor_context._build_artifact_awareness_context",
        return_value=("", []),
    ), patch(
        "graph.supervisor_context._render_engineering_context",
        return_value=("", []),
    ):
        result = build_supervisor_system_content(
            state={},
            config=SimpleNamespace(system_prompt="Custom editable persona prompt."),
            user_query="开启 Spec Mode 做一个复杂项目，并按需要调研、分发子代理和执行。",
            current_scope="global",
            scope_chain=["global"],
            session_id="dry_run_supervisor_first",
            messages=[],
            loaded_agents=[],
            supervisor_tools=[],
            memory_runtime=_MemoryRuntimeStub(),
        )

    system_content = result["system_content"]
    tool_descriptions = {
        "runtime_broker": _tool_description(runtime_broker),
        "delegation_broker": _tool_description(delegation_broker),
        "spec_broker": _tool_description(spec_broker),
        "creative_media_create_job": _tool_description(creative_media_create_job),
        "creative_media_production_pack": _tool_description(creative_media_production_pack),
        "creative_media_rank_models": _tool_description(creative_media_rank_models),
        "computer_use_execute_task": _tool_description(computer_use_execute_task),
        "computer_use_observe": _tool_description(computer_use_observe),
        "wait": _tool_description(wait),
        "manage_cron": _tool_description(manage_cron),
        "manage_hook": _tool_description(manage_hook),
    }

    checks = {
        "system_contract": _contains_all(
            system_content,
            [
                "Supervisor First, Runtime Grounded",
                "supporting signals",
                "Planner output as a proposed episode plan/runtime-needs map",
                "Memory is evidence",
                "Product language:",
                "Use product words with users",
                "编程模式",
                "深度调研",
                "多媒体创作",
                "canonical ids",
                "Specialist mode path",
                "Passive/support systems",
                "`wait` is only for a short local stabilization pause",
                "`manage_cron` creates or changes scheduled tasks only when the user explicitly asks",
            ],
        ),
        "tool_descriptions": {
            "runtime_product_language": "specialist mode" in tool_descriptions["runtime_broker"]
            and "编程模式" in tool_descriptions["runtime_broker"]
            and "Do not tell ordinary users" in tool_descriptions["runtime_broker"],
            "runtime_broker_active_passive_boundary": "Passive" in tool_descriptions["runtime_broker"]
            or "passive" in tool_descriptions["runtime_broker"],
            "delegation_requires_real_tasks": "not a decorative" in tool_descriptions["delegation_broker"],
            "delegation_product_language": "子代理" in tool_descriptions["delegation_broker"]
            and "Do not tell ordinary users" in tool_descriptions["delegation_broker"],
            "spec_engine_creates_spec_id": "Engine creates the canonical `specId`" in tool_descriptions["spec_broker"],
            "spec_product_language": "规格文档" in tool_descriptions["spec_broker"] or "Spec 模式" in tool_descriptions["spec_broker"],
            "creative_create_job_product_language": "多媒体创作" in tool_descriptions["creative_media_create_job"]
            and "music.generate" in tool_descriptions["creative_media_create_job"]
            and "model3d.generate" in tool_descriptions["creative_media_create_job"],
            "creative_rank_clean_markdown": "Markdown" in tool_descriptions["creative_media_rank_models"]
            and "provider JSON" in tool_descriptions["creative_media_rank_models"],
            "creative_pack_delivery": "artifact proof" in tool_descriptions["creative_media_production_pack"]
            and "QA" in tool_descriptions["creative_media_production_pack"],
            "computer_execute_product_language": "桌面操作" in tool_descriptions["computer_use_execute_task"]
            and "goal" in tool_descriptions["computer_use_execute_task"]
            and "successCriteria" in tool_descriptions["computer_use_execute_task"],
            "computer_observe_before_action": "Use before click/type" in tool_descriptions["computer_use_observe"]
            and "桌面操作" in tool_descriptions["computer_use_observe"],
            "wait_short_only": "Scheduled or recurring work" in tool_descriptions["wait"],
            "cron_user_explicit": "user explicitly asks" in tool_descriptions["manage_cron"],
            "hook_user_explicit": "user explicitly asks" in tool_descriptions["manage_hook"],
        },
    }
    passed = all(checks["system_contract"].values()) and all(checks["tool_descriptions"].values())

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    stem = "supervisor_first_contract_dry_run"
    json_path = REPORT_ROOT / f"{stem}.json"
    md_path = REPORT_ROOT / f"{stem}.md"

    payload = {
        "passed": passed,
        "checks": checks,
        "systemContentPreview": system_content[:6000],
        "toolDescriptions": tool_descriptions,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Supervisor First Contract Dry Run",
        "",
        f"Overall: **{'PASS' if passed else 'FAIL'}**",
        "",
        "## System Contract Checks",
    ]
    for key, ok in checks["system_contract"].items():
        lines.append(f"- [{'x' if ok else ' '}] `{key}`")
    lines.extend(["", "## Tool Description Checks"])
    for key, ok in checks["tool_descriptions"].items():
        lines.append(f"- [{'x' if ok else ' '}] `{key}`")
    lines.extend(
        [
            "",
            "## Agent-Visible Excerpt",
            "",
            "```text",
            system_content[:3500],
            "```",
            "",
            "## Tool Descriptions",
        ]
    )
    for name, description in tool_descriptions.items():
        lines.extend(["", f"### {name}", "", "```text", description[:2200], "```"])
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"markdown": str(md_path), "json": str(json_path), "passed": passed}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
