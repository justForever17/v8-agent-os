"""Anonymous catalog + complete ZIP installation into an isolated Skill root."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
with tempfile.TemporaryDirectory(prefix="v8-modelscope-live-") as temp:
    state = Path(temp)
    os.environ["V8_AGENT_OS_HOME"] = str(state / "state")
    os.environ["V8_AGENT_OS_EXTENSIONS_STORE_CACHE_DIR"] = str(state / "cache")
    from core import extensions_modelscope as source
    from core import skills_install_service as installer
    from runtimes.extensions.skills.loader import SkillLoader
    root = state / "skills"
    root.mkdir()
    installer._target_root = lambda: root
    SkillLoader._discovery_root_descriptors = classmethod(lambda cls: [cls._build_root_descriptor(
        root_path=root, source_type="global", visibility="global")])
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        listing = source.list_items("skills", query="modelscope-studio", limit=3, page=1, refresh=False)
        timings.append(round((time.perf_counter() - started) * 1000, 2))
    detail = source.skill_detail("modelscope/modelscope-studio", refresh=True)
    result = source.install_skill({"skillId": "modelscope/modelscope-studio", "revision": detail["revision"]})
    installed = Path(result["installed"][0]["path"])
    files = sorted(path.relative_to(installed).as_posix() for path in installed.rglob("*") if path.is_file())
    assert "SKILL.md" in files and any(file.startswith("references/") for file in files)
    registry = SkillLoader.get_cached_skills()
    assert any(Path(value["path"]).resolve() == installed.resolve() for value in registry.values())
    second = source.install_skill({"skillId": "modelscope/modelscope-studio", "revision": detail["revision"]})
    assert second["skipped"] and not second["installed"]
    receipt = installer.get_skill_receipt("modelscope", "modelscope/modelscope-studio")
    report = {"scope": "anonymous public API, complete ZIP, real installer and isolated SkillLoader; no scripts executed",
        "item": "modelscope/modelscope-studio", "revision": detail["revision"], "archiveSha256": receipt["archiveSha256"],
        "files": files, "registryCount": len(registry), "repeat": "already_installed", "listSamplesMs": timings,
        "limitations": ["Single machine network", "No hosted deployment, user login, or business API call"]}
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
