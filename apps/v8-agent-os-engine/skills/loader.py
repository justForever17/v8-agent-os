import asyncio
import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import tool

from core.v8_agent_os_paths import V8_AGENT_OS_HOME

class SkillLoader:
    _skills_registry: dict[str, dict] = {}
    _skills_fingerprint: str = ""
    _skills_roots: list[Path] = []
    _last_check_at: float = 0.0
    _check_interval_seconds: float = 0.75
    _startup_state: str = "cold"
    _snapshot_freshness: str = "cold"
    _last_refresh_at: str | None = None
    _last_refresh_error: str | None = None
    _background_refresh_task: asyncio.Task | None = None
    _background_refresh_in_progress: bool = False

    @classmethod
    def _now_iso(cls) -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _cache_file(cls) -> Path:
        return V8_AGENT_OS_HOME / "skills_inventory_cache.json"

    @classmethod
    def _resolve_repo_root(cls) -> Path:
        return Path(__file__).resolve().parents[3]

    @classmethod
    def _persist_cache(cls) -> None:
        cache_file = cls._cache_file()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updatedAt": cls._now_iso(),
            "fingerprint": cls._skills_fingerprint,
            "roots": [str(root.resolve(strict=False)) for root in cls._skills_roots],
            "items": list(cls._skills_registry.values()),
        }
        cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def _load_cached_registry(cls) -> bool:
        cache_file = cls._cache_file()
        if not cache_file.exists():
            return False
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return False
        items = list(payload.get("items") or [])
        registry: dict[str, dict] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("folder") or "").strip()
            if not name:
                continue
            registry[name] = {
                "name": name,
                "description": str(item.get("description") or "No description provided."),
                "instructions": str(item.get("instructions") or ""),
                "folder": str(item.get("folder") or name),
                "path": str(item.get("path") or ""),
                "skillName": str(item.get("skillName") or name),
                "skillRoot": str(item.get("skillRoot") or item.get("path") or ""),
                "instructionPath": str(item.get("instructionPath") or ""),
                "referencesDir": str(item.get("referencesDir") or ""),
                "scriptsDir": str(item.get("scriptsDir") or ""),
                "assetsDir": str(item.get("assetsDir") or ""),
                "templatesDir": str(item.get("templatesDir") or ""),
                "availableFiles": list(item.get("availableFiles") or []),
            }
        if not registry:
            return False
        cls._skills_registry = registry
        cls._skills_fingerprint = str(payload.get("fingerprint") or "").strip()
        roots = []
        for root in list(payload.get("roots") or []):
            candidate = str(root or "").strip()
            if candidate:
                roots.append(Path(candidate))
        cls._skills_roots = roots
        cls._startup_state = "ready"
        cls._snapshot_freshness = "cached"
        cls._last_refresh_at = str(payload.get("updatedAt") or "").strip() or None
        cls._last_refresh_error = None
        cls._last_check_at = time.monotonic()
        return True

    @classmethod
    def _ensure_seeded_global_skills(cls, global_agents_path: Path) -> None:
        repo_skill_root = cls._resolve_repo_root() / ".agents" / "skills"
        if not repo_skill_root.exists():
            return

        for skill_name in ("code-reviewer",):
            source_dir = repo_skill_root / skill_name
            target_dir = global_agents_path / skill_name
            if not source_dir.exists() or target_dir.exists():
                continue
            try:
                shutil.copytree(source_dir, target_dir)
                print(f"[SkillLoader] Seeded workspace skill '{skill_name}' into {target_dir}")
            except Exception as exc:
                print(f"[SkillLoader] Failed to seed skill '{skill_name}': {exc}")

    @classmethod
    def _resolve_skill_roots(cls) -> list[Path]:
        global_agents_path = Path.home() / ".agents" / "skills"
        global_agents_path.mkdir(parents=True, exist_ok=True)
        cls._ensure_seeded_global_skills(global_agents_path)
        roots: list[Path] = [global_agents_path]

        deduped: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            normalized = str(root.resolve(strict=False))
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(root)
        return deduped

    @classmethod
    def _compute_fingerprint(cls, roots: list[Path]) -> str:
        digest = hashlib.sha1()
        for root in roots:
            digest.update(str(root.resolve(strict=False)).encode("utf-8"))
            if not root.exists() or not root.is_dir():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                stat = skill_file.stat()
                digest.update(str(skill_file.resolve()).encode("utf-8"))
                digest.update(str(stat.st_mtime_ns).encode("utf-8"))
                digest.update(str(stat.st_size).encode("utf-8"))
        return digest.hexdigest()

    @classmethod
    def ensure_fresh(cls, force: bool = False):
        if not force and cls._background_refresh_in_progress and cls._skills_registry:
            return
        now = time.monotonic()
        if not force and (now - cls._last_check_at) < cls._check_interval_seconds and cls._skills_registry:
            return
        cls._last_check_at = now
        roots = cls._resolve_skill_roots()
        fingerprint = cls._compute_fingerprint(roots)
        if not force and fingerprint == cls._skills_fingerprint and cls._skills_registry:
            cls._skills_roots = roots
            return
        cls.reload_skills(skill_roots=roots, fingerprint=fingerprint)

    @classmethod
    def discover_skills(
        cls,
        skills_dir: str = "skills",
        *,
        skill_roots: list[Path] | None = None,
        fingerprint: str | None = None,
    ):
        """Scans the designated skills directories for SKILL.md files and registers them."""
        roots = skill_roots or cls._resolve_skill_roots()
        cls._skills_roots = roots
        for base_path in roots:
            if base_path.exists() and base_path.is_dir():
                print(f"[SkillLoader] Scanning skills in {base_path} ...")
                for item in sorted(base_path.iterdir()):
                    if item.is_dir():
                        skill_file = item / "SKILL.md"
                        if skill_file.exists():
                            cls._load_skill_file(item.name, skill_file)
        cls._skills_fingerprint = fingerprint or cls._compute_fingerprint(roots)
        cls._last_check_at = time.monotonic()

    @classmethod
    def _load_skill_file(cls, folder_name: str, file_path: Path):
        content = file_path.read_text(encoding="utf-8")
        # Fast parse of YAML frontmatter
        if content.startswith("---"):
            try:
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                    body = parts[2].strip()
                    
                    # Ensure name defaults to folder_name if missing
                    name = frontmatter.get("name", folder_name)
                    description = frontmatter.get("description", "No description provided.")
                    skill_root = file_path.parent
                    
                    cls._skills_registry[name] = {
                        "name": name,
                        "description": description,
                        "instructions": body,
                        "folder": folder_name,
                        "path": str(skill_root.absolute()),
                        "skillName": name,
                        "skillRoot": str(skill_root.absolute()),
                        "instructionPath": str(file_path.absolute()),
                        "referencesDir": str((skill_root / "references").absolute()) if (skill_root / "references").exists() else "",
                        "scriptsDir": str((skill_root / "scripts").absolute()) if (skill_root / "scripts").exists() else "",
                        "assetsDir": str((skill_root / "assets").absolute()) if (skill_root / "assets").exists() else "",
                        "templatesDir": str((skill_root / "templates").absolute()) if (skill_root / "templates").exists() else "",
                        "availableFiles": cls._summarize_skill_structure(skill_root),
                    }
                    print(f"[SkillLoader] Successfully loaded Skill: {name}")
            except Exception as e:
                print(f"[SkillLoader] Error parsing frontmatter in {file_path}: {e}")

    @classmethod
    def _summarize_skill_structure(cls, skill_root: Path) -> list[str]:
        allowed_roots = ("references", "scripts", "assets", "templates")
        items: list[str] = []
        for subdir_name in allowed_roots:
            subdir = skill_root / subdir_name
            if not subdir.exists() or not subdir.is_dir():
                continue
            items.append(f"{subdir_name}/")
            for path in sorted(subdir.rglob("*")):
                if path.is_dir():
                    continue
                try:
                    relative = path.relative_to(skill_root).as_posix()
                except ValueError:
                    continue
                items.append(relative)
        return items

    @classmethod
    def get_all_skills(cls, *, force_refresh: bool = True):
        """Returns the fully loaded skills registry."""
        if force_refresh:
            cls.ensure_fresh()
        return cls._skills_registry

    @classmethod
    def get_cached_skills(cls) -> dict[str, dict]:
        return dict(cls._skills_registry)

    @classmethod
    def reload_skills(
        cls,
        skills_dir: str = "skills",
        *,
        skill_roots: list[Path] | None = None,
        fingerprint: str | None = None,
    ):
        """Clears the registry and re-discovers skills."""
        print("[SkillLoader] Reloading skills registry...")
        cls._skills_registry.clear()
        cls.discover_skills(skills_dir, skill_roots=skill_roots, fingerprint=fingerprint)
        cls._persist_cache()
        cls._startup_state = "ready"
        cls._snapshot_freshness = "live"
        cls._last_refresh_at = cls._now_iso()
        cls._last_refresh_error = None
        print(f"[SkillLoader] Reloaded {len(cls._skills_registry)} skills.")

    @classmethod
    def prime_startup_cache(cls) -> bool:
        loaded = cls._load_cached_registry()
        if not loaded:
            cls._startup_state = "cold"
            cls._snapshot_freshness = "cold"
        return loaded

    @classmethod
    def schedule_background_refresh(cls, *, force: bool = False) -> asyncio.Task:
        current = cls._background_refresh_task
        if current and not current.done():
            return current

        cls._startup_state = "refreshing"
        cls._snapshot_freshness = "cached" if cls._skills_registry else "cold"
        cls._last_refresh_error = None

        async def _runner() -> None:
            cls._background_refresh_in_progress = True
            try:
                await asyncio.to_thread(cls.reload_skills)
            except Exception as exc:
                cls._startup_state = "error"
                cls._last_refresh_error = str(exc).strip() or exc.__class__.__name__
                raise
            finally:
                cls._background_refresh_in_progress = False

        task = asyncio.create_task(_runner(), name="skills:background_refresh")
        cls._background_refresh_task = task
        return task

    @classmethod
    async def wait_for_background_refresh(cls, timeout: float | None = None) -> None:
        task = cls._background_refresh_task
        if not task:
            return
        if timeout is None:
            await asyncio.shield(task)
            return
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    @classmethod
    def get_startup_status(cls) -> dict[str, object]:
        roots = cls._skills_roots or cls._resolve_skill_roots()
        return {
            "startupState": cls._startup_state,
            "snapshotFreshness": cls._snapshot_freshness,
            "lastRefreshAt": cls._last_refresh_at,
            "lastRefreshError": cls._last_refresh_error,
            "skillCount": len(cls._skills_registry),
            "root": str(roots[0]) if roots else "",
            "cacheFile": str(cls._cache_file()),
        }

    @classmethod
    def get_system_prompt_addition(cls) -> str:
        """Generates the text block to append to the Agent's system prompt to make it aware of SKILLs."""
        cls.ensure_fresh()
        if not cls._skills_registry:
            return "No persistent skills available at the moment."

        registry_items = list(cls._skills_registry.items())
        lines = [
            "\n# Available Custom Skills",
            f"You have access to {len(registry_items)} reusable workflow skills from ~/.agents/skills.",
            "Use `fetch_skill_instructions` only when a task clearly matches one of these workflow areas:",
        ]
        for name, meta in registry_items[:8]:
            lines.append(f"- **{name}**: {meta['description']}")
        if len(registry_items) > 8:
            lines.append(f"- 还有 {len(registry_items) - 8} 个技能未在此处展开，请按任务领域选择最相关技能。")

        lines.append("\nCRITICAL: Always read a skill's instructions before attempting a complex task relating to it!")
        return "\n".join(lines)


# --- Native LangChain Tool ---

@tool
def fetch_skill_instructions(skill_name: str) -> str:
    """Fetches the detailed markdown workflow instructions for a specific given skill name.
    Use this tool whenever you want to learn HOW to perform a specific workflow that is listed in your Available Custom Skills.
    """
    SkillLoader.ensure_fresh()
    registry = SkillLoader.get_all_skills()
    if skill_name in registry:
        # Returning the path and the raw markdown. LangGraph will place this directly into the ToolMessage for the context.
        skill = registry[skill_name]
        try:
            from core.extensions_runtime import extensions_runtime_service

            extensions_runtime_service.emit_skill_loaded(
                skill_name=skill["name"],
                skill_path=skill["path"],
            )
        except Exception:
            pass
        available_files = list(skill.get("availableFiles") or [])
        structure = "\n".join(f"- {item}" for item in available_files[:64]) if available_files else "- (no extra references/scripts/assets/templates found)"
        return (
            f"=== SKILL ENTRYPOINTS ===\n"
            f"Skill Name: {skill.get('skillName') or skill.get('name') or skill_name}\n"
            f"Skill Root: {skill.get('skillRoot') or skill.get('path') or ''}\n"
            f"Instruction Path: {skill.get('instructionPath') or ''}\n"
            f"References Dir: {skill.get('referencesDir') or ''}\n"
            f"Scripts Dir: {skill.get('scriptsDir') or ''}\n"
            f"Assets Dir: {skill.get('assetsDir') or ''}\n"
            f"Templates Dir: {skill.get('templatesDir') or ''}\n"
            f"Directory Structure:\n{structure}\n\n"
            f"按当前 skill 的要求去做。\n\n"
            f"=== INSTRUCTIONS ===\n{skill['instructions']}"
        )
    return f"Error: The requested skill '{skill_name}' was not found in the registry."
