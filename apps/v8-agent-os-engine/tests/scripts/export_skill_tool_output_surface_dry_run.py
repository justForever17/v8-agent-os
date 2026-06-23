from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.tool_surface import apply_tool_surface_budget  # noqa: E402
from erc.runtime_context import bind_runtime_context  # noqa: E402
from runtimes.extensions.skills.loader import SkillLoader, fetch_skill_instructions  # noqa: E402


REPO_ROOT = ENGINE_ROOT.parents[1]
OUTPUT_ROOT = REPO_ROOT / "docs" / "chatruntime" / "runtime_deep_observation_reports"


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _contains_none(text: str, needles: list[str]) -> bool:
    return all(needle not in text for needle in needles)


def _write_demo_skill(workspace: Path) -> str:
    skill_name = "surface-demo-skill"
    root = workspace / ".agents" / "skills" / skill_name
    (root / "references" / "research").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        """---
name: surface-demo-skill
description: Demo skill for fetch_skill_instructions output surface.
---

# Surface Demo Skill

Use this skill to verify that fetch_skill_instructions exposes the full SKILL.md method contract while keeping loader paths out of the agent-visible surface.

## Workflow

1. Read this SKILL.md from top to bottom before starting work.
2. When the workflow mentions references/research/01-writings.md, continue through fetch_skill_instructions(relative_path=...).
3. Run scripts/check-quality.py when quality validation is required. Large script source does not need to be loaded into model context first.

## Output

Return a concise result, evidence used, risks, and next action.
""",
        encoding="utf-8",
    )
    (root / "references" / "research" / "01-writings.md").write_text(
        "# Research Notes\n\nKeep this document intact when reading it.",
        encoding="utf-8",
    )
    (root / "scripts" / "check-quality.py").write_text(
        "import sys\nprint('quality ok: ' + '|'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    return skill_name


def build_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="v8-skill-surface-") as temp_dir:
        workspace = Path(temp_dir) / "workspace"
        workspace.mkdir(parents=True)
        skill_name = _write_demo_skill(workspace)
        SkillLoader.resolve_skill_matches(
            skill_name,
            force_refresh=True,
            explicit_workspace_path=str(workspace),
        )
        with bind_runtime_context(workspace_path=str(workspace), runtime_kind="chat"):
            raw_output = fetch_skill_instructions.invoke(
                {
                    "skill_name": skill_name,
                    "detail_level": "full",
                }
            )
        with bind_runtime_context(workspace_path=str(workspace), runtime_kind="chat"):
            raw_script_output = fetch_skill_instructions.invoke(
                {
                    "skill_name": skill_name,
                    "mode": "run_script",
                    "relative_path": "scripts/check-quality.py",
                    "script_args": ["artifact.md"],
                }
            )
        agent_message = apply_tool_surface_budget(
            ToolMessage(
                content=str(raw_output),
                name="fetch_skill_instructions",
                tool_call_id="call_skill_surface_dry_run",
            ),
            {"agentVisibleBudget": 5000},
            tool_name="fetch_skill_instructions",
        )
        agent_visible_output = str(agent_message.content)
        agent_script_message = apply_tool_surface_budget(
            ToolMessage(
                content=str(raw_script_output),
                name="fetch_skill_instructions",
                tool_call_id="call_skill_script_surface_dry_run",
            ),
            {"agentVisibleBudget": 3000},
            tool_name="fetch_skill_instructions",
        )
        agent_script_output = str(agent_script_message.content)
        manifest_text = str(raw_output).split("=== CONTINUATION MANIFEST ===", 1)[1].split("=== INSTRUCTIONS", 1)[0]

    forbidden = [
        "Visibility:",
        "Workspace ID:",
        "Project ID:",
        "Skill Name:",
        "Workspace Path:",
        "Instruction Path:",
        "References Dir:",
        "Scripts Dir:",
        "Assets Dir:",
        "Templates Dir:",
        "Examples Dir:",
        '"skillRoot"',
        "按当前 skill 的要求去做。",
        "Relative Resources:",
        "Verdict:",
        "Governance Target:",
        "Posture:",
        "Audit ID:",
    ]
    validations = {
        "raw_output_keeps_full_skill_md": all(
            item in raw_output
            for item in (
                "=== INSTRUCTIONS (FULL) ===",
                "Read this SKILL.md from top to bottom",
                "references/research/01-writings.md",
                "scripts/check-quality.py",
            )
        ),
        "raw_output_hides_loader_noise": _contains_none(str(raw_output), forbidden),
        "raw_output_keeps_skill_root": "Skill Root:" in raw_output,
        "raw_output_omits_redundant_skill_name": "Skill Name:" not in raw_output
        and "Skill instructions: surface-demo-skill" not in agent_visible_output,
        "raw_output_omits_redundant_resource_tree": "Relative Resources:" not in raw_output
        and "\n- references/\n" not in raw_output
        and "\n- scripts/\n" not in raw_output,
        "manifest_is_markdown_not_raw_json": "Continue reading skill-relative files with:" in raw_output
        and '"readContract"' not in raw_output
        and '"references"' not in raw_output
        and "Required reads for artifact work:" not in raw_output
        and "Recommended next reads:" not in raw_output
        and "Examples:" not in raw_output
        and "\n- references/\n" not in manifest_text
        and "\n- scripts/ —" not in manifest_text,
        "agent_surface_keeps_method_contract": all(
            item in agent_visible_output
            for item in (
                "Skill instructions",
                "Use the main SKILL.md instructions below as the method contract",
                "Read this SKILL.md from top to bottom",
                "Relative path continuation:",
            )
        ),
        "agent_surface_hides_loader_noise": _contains_none(agent_visible_output, forbidden),
        "script_runs_without_source_read": "Status: completed" in raw_script_output
        and "quality ok: artifact.md" in raw_script_output,
        "script_agent_surface_is_actionable": all(
            item in agent_script_output
            for item in (
                "=== SKILL SCRIPT RESULT ===",
                "Script: scripts/check-quality.py",
                "quality ok: artifact.md",
                "Next Action:",
                "toolobs://",
            )
        ),
        "script_surface_hides_absolute_paths": str(workspace) not in raw_script_output
        and str(workspace) not in agent_script_output,
    }
    return {
        "description": "Dry-run export for fetch_skill_instructions read and governed script surfaces. No model/network call or repository mutation; script execution is limited to a temporary workspace.",
        "passed": all(validations.values()),
        "validations": validations,
        "rawOutput": raw_output,
        "agentVisibleOutput": agent_visible_output,
        "rawScriptOutput": raw_script_output,
        "agentScriptOutput": agent_script_output,
    }


def write_report(matrix: dict[str, Any]) -> dict[str, str]:
    stamp = _now_stamp()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_ROOT / f"{stamp}_skill_tool_output_surface_dry_run.json"
    md_path = OUTPUT_ROOT / f"{stamp}_skill_tool_output_surface_dry_run.md"
    json_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_lines = "\n".join(
        f"- {'PASS' if ok else 'FAIL'} `{name}`"
        for name, ok in matrix["validations"].items()
    )
    md = f"""# Skill Tool Output Surface Dry Run

No model/network call or repository mutation. The script runs only inside a temporary workspace.

## Result

Overall: **{'PASS' if matrix['passed'] else 'FAIL'}**

{validation_lines}

## Raw Tool Output

```text
{matrix['rawOutput']}
```

## Agent Visible Output

```text
{matrix['agentVisibleOutput']}
```

## Raw Script Output

```text
{matrix['rawScriptOutput']}
```

## Agent Script Output

```text
{matrix['agentScriptOutput']}
```
"""
    md_path.write_text(md, encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path)}


def main() -> int:
    matrix = build_matrix()
    paths = write_report(matrix)
    print(json.dumps({**paths, "passed": matrix["passed"]}, ensure_ascii=False))
    return 0 if matrix["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
