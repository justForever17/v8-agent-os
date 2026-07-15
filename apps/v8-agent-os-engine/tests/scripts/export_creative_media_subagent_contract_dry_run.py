from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

if "chromadb" not in sys.modules:
    class _FakeChromaCollection:
        def upsert(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {}

    class _FakeChromaClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def get_or_create_collection(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeChromaCollection()

    sys.modules["chromadb"] = type("chromadb", (), {"PersistentClient": _FakeChromaClient})()


from core.agents import default_subagent_configs  # noqa: E402
from core.tools.native.creative_media_facade import (  # noqa: E402
    creative_media_action_contract,
    creative_media_assets,
    creative_media_capabilities,
    creative_media_edit,
    creative_media_jobs,
    creative_media_plan,
    creative_media_quality,
)
from graph.agent_factories import _build_agent_system_content, _format_delegated_task_contract  # noqa: E402


REPO_ROOT = ENGINE_ROOT.parents[1]
OUTPUT_ROOT = REPO_ROOT / "docs" / "chatruntime" / "runtime_deep_observation_reports"


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _contains_all(text: str, terms: list[str]) -> bool:
    return all(term in text for term in terms)


def _creative_agent_prompt() -> str:
    agents = {agent.id: agent for agent in default_subagent_configs()}
    agent = agents["audio-post-producer"]
    task_brief = {
        "taskBriefId": "TASK-CREATIVE-MEDIA-MUSIC-3D",
        "goal": "Generate a short game background music loop and a small 3D prop artifact for a web game prototype.",
        "expectedOutputs": [
            "music artifact id and file type",
            "3D artifact id and file type",
            "limitations and acceptance notes",
        ],
        "requiredCapabilities": ["creative_media", "music_generation", "model3d_generation", "artifact_handoff"],
        "runtimeAccess": ["creative_media.core"],
        "acceptanceContract": "Do not return provider raw JSON. Return artifact refs, status, limitations, and what was verified.",
        "context": {
            "workspacePath": "E:/Projects/test3",
            "taskId": "TASK-CREATIVE-MEDIA-MUSIC-3D",
            "expectedOutput": "Two Creative Media jobs: music.generate and model3d.generate, each with artifact refs.",
            "detailRefs": [
                "creative-media://model-preferences/music.generate",
                "creative-media://model-preferences/model3d.generate",
            ],
        },
    }
    delegated_plan_context = _format_delegated_task_contract(task_brief)
    return _build_agent_system_content(
        agent_name=agent.name,
        agent_system_prompt=agent.system_prompt,
        env_context=(
            "<environment>\n"
            "OS: Windows\n"
            "Active Workspace Root: E:/Projects/test3\n"
            "</environment>\n"
        ),
        delegated_plan_context=delegated_plan_context,
    )


def build_matrix() -> dict[str, Any]:
    prompt = _creative_agent_prompt()
    tool_descriptions = {
        "creative_media_capabilities": creative_media_capabilities.description,
        "creative_media_plan": creative_media_plan.description,
        "creative_media_assets": creative_media_assets.description,
        "creative_media_jobs": creative_media_jobs.description,
        "creative_media_edit": creative_media_edit.description,
        "creative_media_quality": creative_media_quality.description,
    }
    action_contract = creative_media_action_contract()
    joined_tool_descriptions = "\n".join(str(value or "") for value in tool_descriptions.values())
    validations = {
        "prompt_has_stable_worker_identity": _contains_all(
            prompt,
            ["You are a delegated V8OS worker", "Assigned Task Brief", "Acceptance Contract"],
        ),
        "prompt_teaches_creative_media_job_flow": _contains_all(
            prompt,
            ["creative_media_jobs", "action='create'", "action='get'", "action='artifacts'"],
        ),
        "prompt_preserves_music_and_3d_operations": _contains_all(
            prompt,
            ["music.generate", "model3d.generate", "Creative Media jobs"],
        ),
        "prompt_requires_artifact_handoff_not_raw_json": _contains_all(
            prompt,
            ["artifact IDs", "provider raw JSON", "acceptance status"],
        ),
        "prompt_has_production_charter": _contains_all(
            prompt,
            ["CreativeMediaProductionPack", "sample before batch", "provider/model", "Artifact proof"],
        ),
        "prompt_has_hard_production_gates": _contains_all(
            prompt,
            [
                "Reference media is a gate",
                "Sample approval is a gate",
                "Complex final delivery must pass QA first",
                "providerLock",
            ],
        ),
        "prompt_explains_sample_approval_and_qa": _contains_all(
            prompt,
            ["action='sample_approval'", "ask_user", "action='qa_check'"],
        ),
        "prompt_explains_reference_preflight_and_selector": _contains_all(
            prompt,
            ["action='reference_brief'", "action='rank_models'", "clean Markdown"],
        ),
        "facade_registry_declares_operation_parameters": _contains_all(
            json.dumps(action_contract, ensure_ascii=False),
            ["modality", "operationKind", "providerAdapterId", "plugin_grant_required"],
        ),
        "tool_descriptions_explain_facade_discovery_and_jobs": _contains_all(
            joined_tool_descriptions,
            ["action='describe'", "provider-backed", "six-facade"],
        ),
        "action_registry_exposes_pack_approval_and_qa": all(
            action in action_contract[facade]
            for facade, action in (
                ("plan", "production_pack"),
                ("plan", "sample_approval"),
                ("quality", "qa_check"),
            )
        ),
    }
    return {
        "description": "Dry-run check for Creative Media subagent prompt and tool-description usability. No model call, no DB write.",
        "validations": validations,
        "passed": all(validations.values()),
        "agentPrompt": prompt,
        "toolDescriptions": tool_descriptions,
        "actionContract": action_contract,
    }


def write_report(matrix: dict[str, Any]) -> dict[str, str]:
    stamp = _now_stamp()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_ROOT / f"{stamp}_creative_media_subagent_contract_dry_run.json"
    md_path = OUTPUT_ROOT / f"{stamp}_creative_media_subagent_contract_dry_run.md"
    json_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_lines = "\n".join(
        f"- {'PASS' if ok else 'FAIL'} `{name}`"
        for name, ok in matrix["validations"].items()
    )
    md = f"""# Creative Media Subagent Contract Dry Run

No model call, no database write, no workspace mutation.

## Result

Overall: **{'PASS' if matrix['passed'] else 'FAIL'}**

{validation_lines}

## Tool Descriptions

```json
{json.dumps(matrix['toolDescriptions'], ensure_ascii=False, indent=2)}
```

## Agent Prompt

```text
{matrix['agentPrompt']}
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
