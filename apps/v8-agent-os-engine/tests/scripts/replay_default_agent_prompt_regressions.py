"""Compare historical seed overwrite and removed-charter mutants in isolated fixtures."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline", default="f46631d1")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["V8_AGENT_OS_HOME"] = str(output / "isolated-home")
    engine = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(engine))
    from core.storage import StorageManager
    from core.agents import default_subagent_configs
    from tests.scripts.export_default_agent_prompt_contract import capture_agent_contract
    import graph.agent_factories as factories

    source = subprocess.run(["git", "show", f"{args.baseline}:apps/v8-agent-os-engine/core/storage.py"],
                            cwd=engine, check=True, capture_output=True, encoding="utf-8").stdout
    owner = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "StorageManager")
    method = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == "_ensure_default_subagents")
    namespace = {"Path": Path, "datetime": datetime, "timezone": timezone, "shutil": shutil}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "historical_seed_migration", "exec"), namespace)
    old_method = namespace["_ensure_default_subagents"]
    original = (engine / "tests/fixtures/agents/creative-media-director-9.16.4.md").read_text(encoding="utf-8")
    edited = original + "\nUser edit: retain the robot's square eyes and brass finish.\n"
    outcomes = {}
    for label, initialize in (("baseline", old_method), ("candidate", StorageManager._ensure_default_subagents)):
        manager = StorageManager.__new__(StorageManager)
        manager.base_dir = output / label
        target = manager.base_dir / "agents/creative-media-director.md"
        target.parent.mkdir(parents=True)
        target.write_text(edited, encoding="utf-8", newline="\n")
        initialize(manager)
        outcomes[label] = {"editedSeedPreserved": target.read_text(encoding="utf-8") == edited}

    agent = next(a for a in default_subagent_configs() if a.id == "motion-shot-director")
    capture = capture_agent_contract(agent, persona_override="")
    original_charter = factories.delegated_agent_operating_charter
    try:
        factories.delegated_agent_operating_charter = lambda _names: ""
        mutant = capture_agent_contract(agent, persona_override="")
    finally:
        factories.delegated_agent_operating_charter = original_charter
    outcomes["charter"] = {
        "candidateHasControlledContract": "<delegated_agent_operating_charter>" in capture["systemPrompt"],
        "removedOwnerMutantHasContract": "<delegated_agent_operating_charter>" in mutant["systemPrompt"],
        "toolAuthorityUnchangedByRemoval": capture["tools"] == mutant["tools"],
    }
    passed = (not outcomes["baseline"]["editedSeedPreserved"] and outcomes["candidate"]["editedSeedPreserved"]
              and outcomes["charter"]["candidateHasControlledContract"]
              and not outcomes["charter"]["removedOwnerMutantHasContract"]
              and outcomes["charter"]["toolAuthorityUnchangedByRemoval"])
    result = {"passed": passed, "baseline": args.baseline, "evidenceLevel": "isolated_historical_and_mutant_replay",
              "realProviderVerified": False, "outcomes": outcomes}
    (output / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
