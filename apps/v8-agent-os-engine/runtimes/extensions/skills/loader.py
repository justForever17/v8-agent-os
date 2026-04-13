import asyncio
import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import tool

from core.v8_agent_os_paths import V8_AGENT_OS_HOME
from core.workspace_resolution import workspace_resolution_service
from runtimes.memory.project_registry import project_registry_service


class SkillLoader:
    _skills_registry: dict[str, dict] = {}
    _skills_fingerprint: str = ""
    _skills_roots: list[Path] = []
    _skills_root_descriptors: list[dict[str, Any]] = []
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
        current = Path(__file__).resolve()
        for ancestor in current.parents:
            if (ancestor / ".agents").exists() and (ancestor / "apps").exists():
                return ancestor
        return current.parents[5]

    @classmethod
    def _normalize_path(cls, value: str | Path | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        return str(Path(raw).expanduser().resolve(strict=False))

    @classmethod
    def _build_root_descriptor(
        cls,
        *,
        root_path: Path,
        source_type: str,
        visibility: str,
        workspace_path: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_root = cls._normalize_path(root_path)
        return {
            "rootPath": normalized_root,
            "sourceType": source_type,
            "workspacePath": cls._normalize_path(workspace_path),
            "workspaceId": str(workspace_id or "").strip() or None,
            "projectId": str(project_id or "").strip() or None,
            "visibility": visibility,
        }

    @classmethod
    def _dedupe_root_descriptors(cls, descriptors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for descriptor in descriptors:
            root_path = cls._normalize_path(descriptor.get("rootPath"))
            if not root_path or root_path in seen:
                continue
            seen.add(root_path)
            normalized = dict(descriptor)
            normalized["rootPath"] = root_path
            normalized["workspacePath"] = cls._normalize_path(normalized.get("workspacePath"))
            normalized["workspaceId"] = str(normalized.get("workspaceId") or "").strip() or None
            normalized["projectId"] = str(normalized.get("projectId") or "").strip() or None
            normalized["sourceType"] = str(normalized.get("sourceType") or "global").strip() or "global"
            normalized["visibility"] = str(normalized.get("visibility") or "global").strip() or "global"
            deduped.append(normalized)
        return deduped

    @classmethod
    def _persist_cache(cls) -> None:
        cache_file = cls._cache_file()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "updatedAt": cls._now_iso(),
            "fingerprint": cls._skills_fingerprint,
            "roots": [str(root.resolve(strict=False)) for root in cls._skills_roots],
            "rootDescriptors": list(cls._skills_root_descriptors),
            "items": list(cls._skills_registry.values()),
        }
        cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def _stable_skill_id(cls, *, source_type: str, root_path: str, instruction_path: str) -> str:
        digest = hashlib.sha1(
            "|".join([source_type, cls._normalize_path(root_path), cls._normalize_path(instruction_path)]).encode("utf-8")
        ).hexdigest()
        return f"{source_type}:{digest[:16]}"

    @classmethod
    def _normalize_cached_item(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        skill_name = str(item.get("skillName") or item.get("name") or item.get("folder") or "").strip()
        skill_root = cls._normalize_path(item.get("skillRoot") or item.get("path"))
        instruction_path = cls._normalize_path(item.get("instructionPath"))
        source_type = str(item.get("sourceType") or "global").strip() or "global"
        skill_id = str(item.get("skillId") or "").strip() or cls._stable_skill_id(
            source_type=source_type,
            root_path=skill_root,
            instruction_path=instruction_path or skill_root,
        )
        if not skill_id or not skill_name:
            return None
        return {
            "skillId": skill_id,
            "name": skill_name,
            "description": str(item.get("description") or "No description provided."),
            "instructions": str(item.get("instructions") or ""),
            "folder": str(item.get("folder") or Path(skill_root).name or skill_name),
            "path": skill_root,
            "skillName": skill_name,
            "skillRoot": skill_root,
            "instructionPath": instruction_path,
            "referencesDir": cls._normalize_path(item.get("referencesDir")),
            "scriptsDir": cls._normalize_path(item.get("scriptsDir")),
            "assetsDir": cls._normalize_path(item.get("assetsDir")),
            "templatesDir": cls._normalize_path(item.get("templatesDir")),
            "availableFiles": list(item.get("availableFiles") or []),
            "sourceType": source_type,
            "visibility": str(item.get("visibility") or "global").strip() or "global",
            "workspacePath": cls._normalize_path(item.get("workspacePath")),
            "workspaceId": str(item.get("workspaceId") or "").strip() or None,
            "projectId": str(item.get("projectId") or "").strip() or None,
            "rootPath": cls._normalize_path(item.get("rootPath") or skill_root),
        }

    @classmethod
    def _load_cached_registry(cls) -> bool:
        cache_file = cls._cache_file()
        if not cache_file.exists():
            return False
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return False

        registry: dict[str, dict] = {}
        for item in list(payload.get("items") or []):
            if not isinstance(item, dict):
                continue
            normalized = cls._normalize_cached_item(item)
            if normalized is None:
                continue
            registry[str(normalized.get("skillId"))] = normalized
        if not registry:
            return False

        cls._skills_registry = registry
        cls._skills_fingerprint = str(payload.get("fingerprint") or "").strip()
        cls._skills_roots = [
            Path(candidate)
            for candidate in [str(root or "").strip() for root in list(payload.get("roots") or [])]
            if candidate
        ]
        root_descriptors = cls._dedupe_root_descriptors(list(payload.get("rootDescriptors") or []))
        if root_descriptors:
            cls._skills_root_descriptors = root_descriptors
        else:
            cls._skills_root_descriptors = [
                cls._build_root_descriptor(root_path=root, source_type="global", visibility="global")
                for root in cls._skills_roots
            ]
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
    def _lookup_project_binding_for_workspace(cls, workspace_path: str | None) -> tuple[str | None, str | None]:
        normalized = cls._normalize_path(workspace_path)
        if not normalized:
            return None, None
        project = project_registry_service.find_project_for_workspace(workspace_path=normalized)
        if project is None:
            return None, None
        return (
            str(project.workspace_id or "").strip() or None,
            str(project.project_id or "").strip() or None,
        )

    @classmethod
    def _global_root_descriptor(cls) -> dict[str, Any]:
        global_agents_path = Path.home() / ".agents" / "skills"
        global_agents_path.mkdir(parents=True, exist_ok=True)
        cls._ensure_seeded_global_skills(global_agents_path)
        return cls._build_root_descriptor(
            root_path=global_agents_path,
            source_type="global",
            visibility="global",
        )

    @classmethod
    def _main_workspace_root_descriptor(cls) -> dict[str, Any] | None:
        workspace_path = workspace_resolution_service.get_main_workspace_path()
        normalized_workspace = cls._normalize_path(workspace_path)
        if not normalized_workspace:
            return None
        workspace_id, project_id = cls._lookup_project_binding_for_workspace(normalized_workspace)
        return cls._build_root_descriptor(
            root_path=Path(normalized_workspace) / ".agents" / "skills",
            source_type="main_workspace",
            visibility="global",
            workspace_path=normalized_workspace,
            workspace_id=workspace_id,
            project_id=project_id,
        )

    @classmethod
    def _scoped_workspace_root_descriptor(
        cls,
        *,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> dict[str, Any] | None:
        descriptor = workspace_resolution_service.resolve_workspace_descriptor(
            runtime_kind=runtime_kind or "chat",
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        workspace_root = cls._normalize_path(descriptor.get("workspaceRoot"))
        main_workspace_root = cls._normalize_path(descriptor.get("mainWorkspacePath"))
        if not workspace_root or workspace_root == main_workspace_root:
            return None
        return cls._build_root_descriptor(
            root_path=Path(workspace_root) / ".agents" / "skills",
            source_type="scoped_workspace",
            visibility="scoped",
            workspace_path=workspace_root,
            workspace_id=str(descriptor.get("workspaceId") or "").strip() or None,
            project_id=str(descriptor.get("projectId") or "").strip() or None,
        )

    @classmethod
    def _resolve_root_descriptors(
        cls,
        *,
        include_scoped: bool = False,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = [cls._global_root_descriptor()]
        main_descriptor = cls._main_workspace_root_descriptor()
        if main_descriptor is not None:
            descriptors.append(main_descriptor)
        if include_scoped:
            scoped_descriptor = cls._scoped_workspace_root_descriptor(
                runtime_kind=runtime_kind,
                session_id=session_id,
                explicit_workspace_id=explicit_workspace_id,
                explicit_workspace_path=explicit_workspace_path,
                explicit_project_id=explicit_project_id,
            )
            if scoped_descriptor is not None:
                descriptors.append(scoped_descriptor)
        return cls._dedupe_root_descriptors(descriptors)

    @classmethod
    def _compute_fingerprint(cls, descriptors: list[dict[str, Any]]) -> str:
        digest = hashlib.sha1()
        for descriptor in descriptors:
            root_path = cls._normalize_path(descriptor.get("rootPath"))
            digest.update(root_path.encode("utf-8"))
            digest.update(str(descriptor.get("sourceType") or "").encode("utf-8"))
            digest.update(str(descriptor.get("visibility") or "").encode("utf-8"))
            digest.update(str(descriptor.get("workspacePath") or "").encode("utf-8"))
            digest.update(str(descriptor.get("workspaceId") or "").encode("utf-8"))
            digest.update(str(descriptor.get("projectId") or "").encode("utf-8"))
            root = Path(root_path)
            if not root.exists() or not root.is_dir():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                stat = skill_file.stat()
                digest.update(str(skill_file.resolve()).encode("utf-8"))
                digest.update(str(stat.st_mtime_ns).encode("utf-8"))
                digest.update(str(stat.st_size).encode("utf-8"))
        return digest.hexdigest()

    @classmethod
    def _inventory_snapshot(
        cls,
        *,
        registry: dict[str, dict],
        descriptors: list[dict[str, Any]],
        fingerprint: str,
    ) -> dict[str, Any]:
        items = sorted(
            list(registry.values()),
            key=lambda item: (
                str(item.get("skillName") or item.get("name") or "").lower(),
                str(item.get("sourceType") or ""),
                str(item.get("skillRoot") or item.get("path") or ""),
            ),
        )
        return {
            "registry": dict(registry),
            "items": items,
            "rootDescriptors": list(descriptors),
            "roots": [str(item.get("rootPath") or "") for item in descriptors],
            "fingerprint": fingerprint,
        }

    @classmethod
    def _build_skill_entry(
        cls,
        *,
        folder_name: str,
        file_path: Path,
        descriptor: dict[str, Any],
        content: str,
    ) -> dict[str, Any] | None:
        if not content.startswith("---"):
            return None
        try:
            parts = content.split("---", 2)
            if len(parts) < 3:
                return None
            frontmatter = yaml.safe_load(parts[1]) or {}
        except Exception as exc:
            print(f"[SkillLoader] Error parsing frontmatter in {file_path}: {exc}")
            return None

        body = parts[2].strip()
        name = str(frontmatter.get("name") or folder_name).strip() or folder_name
        description = str(frontmatter.get("description") or "No description provided.").strip() or "No description provided."
        skill_root = file_path.parent
        source_type = str(descriptor.get("sourceType") or "global").strip() or "global"
        normalized_skill_root = cls._normalize_path(skill_root)
        normalized_instruction_path = cls._normalize_path(file_path)
        return {
            "skillId": cls._stable_skill_id(
                source_type=source_type,
                root_path=normalized_skill_root,
                instruction_path=normalized_instruction_path,
            ),
            "name": name,
            "description": description,
            "instructions": body,
            "folder": folder_name,
            "path": normalized_skill_root,
            "skillName": name,
            "skillRoot": normalized_skill_root,
            "instructionPath": normalized_instruction_path,
            "referencesDir": cls._normalize_path(skill_root / "references") if (skill_root / "references").exists() else "",
            "scriptsDir": cls._normalize_path(skill_root / "scripts") if (skill_root / "scripts").exists() else "",
            "assetsDir": cls._normalize_path(skill_root / "assets") if (skill_root / "assets").exists() else "",
            "templatesDir": cls._normalize_path(skill_root / "templates") if (skill_root / "templates").exists() else "",
            "availableFiles": cls._summarize_skill_structure(skill_root),
            "sourceType": source_type,
            "visibility": str(descriptor.get("visibility") or "global").strip() or "global",
            "workspacePath": cls._normalize_path(descriptor.get("workspacePath")),
            "workspaceId": str(descriptor.get("workspaceId") or "").strip() or None,
            "projectId": str(descriptor.get("projectId") or "").strip() or None,
            "rootPath": cls._normalize_path(descriptor.get("rootPath") or normalized_skill_root),
        }

    @classmethod
    def _scan_root_descriptors(cls, descriptors: list[dict[str, Any]]) -> dict[str, dict]:
        registry: dict[str, dict] = {}
        for descriptor in descriptors:
            base_path = Path(str(descriptor.get("rootPath") or ""))
            if not base_path.exists() or not base_path.is_dir():
                continue
            print(f"[SkillLoader] Scanning skills in {base_path} ...")
            for item in sorted(base_path.iterdir()):
                if not item.is_dir():
                    continue
                skill_file = item / "SKILL.md"
                if not skill_file.exists():
                    continue
                try:
                    content = skill_file.read_text(encoding="utf-8")
                except Exception as exc:
                    print(f"[SkillLoader] Error reading {skill_file}: {exc}")
                    continue
                entry = cls._build_skill_entry(
                    folder_name=item.name,
                    file_path=skill_file,
                    descriptor=descriptor,
                    content=content,
                )
                if entry is None:
                    continue
                registry[str(entry.get("skillId"))] = entry
                print(
                    f"[SkillLoader] Successfully loaded Skill: {entry.get('skillName')} "
                    f"({entry.get('sourceType')})"
                )
        return registry

    @classmethod
    def ensure_fresh(cls, force: bool = False) -> None:
        if not force and cls._background_refresh_in_progress and cls._skills_registry:
            return
        now = time.monotonic()
        if not force and (now - cls._last_check_at) < cls._check_interval_seconds and cls._skills_registry:
            return
        cls._last_check_at = now
        descriptors = cls._resolve_root_descriptors(include_scoped=False)
        fingerprint = cls._compute_fingerprint(descriptors)
        if not force and fingerprint == cls._skills_fingerprint and cls._skills_registry:
            cls._skills_root_descriptors = descriptors
            cls._skills_roots = [Path(item["rootPath"]) for item in descriptors]
            return
        cls.reload_skills(root_descriptors=descriptors, fingerprint=fingerprint)

    @classmethod
    def discover_skills(
        cls,
        skills_dir: str = "skills",
        *,
        skill_roots: list[Path] | None = None,
        root_descriptors: list[dict[str, Any]] | None = None,
        fingerprint: str | None = None,
    ) -> None:
        del skills_dir
        descriptors = root_descriptors or [
            cls._build_root_descriptor(root_path=root, source_type="global", visibility="global")
            for root in list(skill_roots or [])
        ] or cls._resolve_root_descriptors(include_scoped=False)
        descriptors = cls._dedupe_root_descriptors(descriptors)
        registry = cls._scan_root_descriptors(descriptors)
        cls._skills_registry = registry
        cls._skills_root_descriptors = descriptors
        cls._skills_roots = [Path(item["rootPath"]) for item in descriptors]
        cls._skills_fingerprint = fingerprint or cls._compute_fingerprint(descriptors)
        cls._last_check_at = time.monotonic()

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
    def get_inventory(
        cls,
        *,
        force_refresh: bool = True,
        include_scoped: bool = True,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> dict[str, Any]:
        if force_refresh:
            cls.ensure_fresh()
        elif not cls._skills_registry:
            cls.prime_startup_cache()
            if not cls._skills_registry:
                cls.ensure_fresh()

        base_descriptors = cls._skills_root_descriptors or cls._resolve_root_descriptors(include_scoped=False)
        base_registry = dict(cls._skills_registry)
        base_fingerprint = cls._skills_fingerprint or cls._compute_fingerprint(base_descriptors)
        if not include_scoped:
            return cls._inventory_snapshot(
                registry=base_registry,
                descriptors=base_descriptors,
                fingerprint=base_fingerprint,
            )

        visible_descriptors = cls._resolve_root_descriptors(
            include_scoped=True,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        base_paths = {cls._normalize_path(item.get("rootPath")) for item in base_descriptors}
        scoped_descriptors = [
            item for item in visible_descriptors if cls._normalize_path(item.get("rootPath")) not in base_paths
        ]
        if not scoped_descriptors:
            return cls._inventory_snapshot(
                registry=base_registry,
                descriptors=visible_descriptors,
                fingerprint=base_fingerprint,
            )
        scoped_registry = cls._scan_root_descriptors(scoped_descriptors)
        merged_registry = dict(base_registry)
        merged_registry.update(scoped_registry)
        visible_fingerprint = cls._compute_fingerprint(visible_descriptors)
        return cls._inventory_snapshot(
            registry=merged_registry,
            descriptors=visible_descriptors,
            fingerprint=visible_fingerprint,
        )

    @classmethod
    def get_all_skills(
        cls,
        *,
        force_refresh: bool = True,
        include_scoped: bool = True,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> dict[str, dict]:
        inventory = cls.get_inventory(
            force_refresh=force_refresh,
            include_scoped=include_scoped,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        return dict(inventory.get("registry") or {})

    @classmethod
    def get_cached_skills(cls) -> dict[str, dict]:
        return dict(cls._skills_registry)

    @classmethod
    def _skill_registry_signature(cls, item: dict[str, Any]) -> str:
        instruction_path = Path(str(item.get("instructionPath") or item.get("path") or "").strip())
        parts = [
            str(item.get("skillId") or ""),
            str(item.get("name") or ""),
            str(item.get("description") or ""),
            str(instruction_path),
            str(item.get("sourceType") or ""),
            str(item.get("rootPath") or ""),
        ]
        if instruction_path.exists() and instruction_path.is_file():
            try:
                stat = instruction_path.stat()
                parts.extend([str(stat.st_mtime_ns), str(stat.st_size)])
            except OSError:
                pass
        return "|".join(parts)

    @classmethod
    def reload_if_changed(cls) -> dict[str, Any]:
        descriptors = cls._resolve_root_descriptors(include_scoped=False)
        fingerprint = cls._compute_fingerprint(descriptors)
        if fingerprint == cls._skills_fingerprint and cls._skills_registry:
            cls._skills_root_descriptors = descriptors
            cls._skills_roots = [Path(item["rootPath"]) for item in descriptors]
            cls._last_check_at = time.monotonic()
            return {
                "changed": False,
                "fingerprint": fingerprint,
                "roots": [str(item.get("rootPath") or "") for item in descriptors],
                "rootDescriptors": list(descriptors),
                "addedSkills": [],
                "removedSkills": [],
                "updatedSkills": [],
            }

        before = {
            skill_id: cls._skill_registry_signature(item)
            for skill_id, item in cls._skills_registry.items()
        }
        cls.reload_skills(root_descriptors=descriptors, fingerprint=fingerprint)
        after = {
            skill_id: cls._skill_registry_signature(item)
            for skill_id, item in cls._skills_registry.items()
        }
        before_ids = set(before)
        after_ids = set(after)
        shared_ids = before_ids & after_ids
        return {
            "changed": True,
            "fingerprint": fingerprint,
            "roots": [str(item.get("rootPath") or "") for item in descriptors],
            "rootDescriptors": list(descriptors),
            "addedSkills": sorted(after_ids - before_ids),
            "removedSkills": sorted(before_ids - after_ids),
            "updatedSkills": sorted(skill_id for skill_id in shared_ids if before.get(skill_id) != after.get(skill_id)),
        }

    @classmethod
    def reload_skills(
        cls,
        skills_dir: str = "skills",
        *,
        skill_roots: list[Path] | None = None,
        root_descriptors: list[dict[str, Any]] | None = None,
        fingerprint: str | None = None,
    ) -> None:
        del skills_dir
        print("[SkillLoader] Reloading skills registry...")
        descriptors = root_descriptors or [
            cls._build_root_descriptor(root_path=root, source_type="global", visibility="global")
            for root in list(skill_roots or [])
        ] or cls._resolve_root_descriptors(include_scoped=False)
        cls.discover_skills(root_descriptors=descriptors, fingerprint=fingerprint)
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
                if force:
                    await asyncio.to_thread(cls.reload_skills)
                else:
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
        descriptors = cls._skills_root_descriptors or cls._resolve_root_descriptors(include_scoped=False)
        roots = [str(item.get("rootPath") or "") for item in descriptors]
        return {
            "startupState": cls._startup_state,
            "snapshotFreshness": cls._snapshot_freshness,
            "lastRefreshAt": cls._last_refresh_at,
            "lastRefreshError": cls._last_refresh_error,
            "skillCount": len(cls._skills_registry),
            "fingerprint": cls._skills_fingerprint,
            "root": roots[0] if roots else "",
            "roots": roots,
            "rootDescriptors": list(descriptors),
            "cacheFile": str(cls._cache_file()),
        }

    @classmethod
    def get_system_prompt_addition(cls) -> str:
        inventory = cls.get_inventory(force_refresh=True, include_scoped=False)
        registry_items = list(inventory.get("items") or [])
        if not registry_items:
            return "No persistent skills available at the moment."

        root_descriptors = list(inventory.get("rootDescriptors") or [])
        lines = [
            "\n# Available Custom Skills",
            f"You have access to {len(registry_items)} reusable workflow skills from {len(root_descriptors)} configured roots.",
            "Use `fetch_skill_instructions` only when a task clearly matches one of these workflow areas:",
        ]
        for meta in registry_items[:8]:
            lines.append(f"- **{meta['skillName']}**: {meta['description']}")
        if len(registry_items) > 8:
            lines.append(f"- 还有 {len(registry_items) - 8} 个技能未在此处展开，请按任务领域选择最相关技能。")
        if root_descriptors:
            lines.append("当前默认扫描的 skills roots：")
            for descriptor in root_descriptors:
                lines.append(f"- {descriptor.get('sourceType')}: {descriptor.get('rootPath')}")
        lines.append("\nCRITICAL: Always read a skill's instructions before attempting a complex task relating to it!")
        return "\n".join(lines)

    @classmethod
    def resolve_skill_matches(
        cls,
        identifier: str,
        *,
        force_refresh: bool = False,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        needle = str(identifier or "").strip()
        if not needle:
            return []
        inventory = cls.get_inventory(
            force_refresh=force_refresh,
            include_scoped=True,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        entries = list(inventory.get("items") or [])
        normalized_needle = needle.lower()
        matches: list[dict[str, Any]] = []
        for entry in entries:
            skill_id = str(entry.get("skillId") or "").strip()
            skill_name = str(entry.get("skillName") or entry.get("name") or entry.get("folder") or "").strip()
            folder_name = str(entry.get("folder") or "").strip()
            skill_root = cls._normalize_path(entry.get("skillRoot") or entry.get("path"))
            instruction_path = cls._normalize_path(entry.get("instructionPath"))
            candidates = {
                skill_id.lower(),
                skill_name.lower(),
                folder_name.lower(),
                skill_root.lower(),
                instruction_path.lower(),
            }
            if normalized_needle in {candidate for candidate in candidates if candidate}:
                matches.append(entry)
        return matches


@tool
def fetch_skill_instructions(skill_name: str) -> str:
    """Fetches the detailed markdown workflow instructions for a specific given skill name.
    Use this tool whenever you want to learn HOW to perform a specific workflow that is listed in your Available Custom Skills.
    """

    runtime_kind = "chat"
    session_id = None
    explicit_workspace_id = None
    explicit_workspace_path = None
    explicit_project_id = None
    try:
        from erc.runtime_context import get_runtime_context

        runtime_context = get_runtime_context()
        runtime_kind = str(runtime_context.get("runtime_kind") or "chat")
        session_id = str(runtime_context.get("session_id") or "").strip() or None
        explicit_workspace_id = str(runtime_context.get("workspace_id") or "").strip() or None
        explicit_workspace_path = str(runtime_context.get("workspace_path") or "").strip() or None
        explicit_project_id = str(runtime_context.get("project_id") or "").strip() or None
    except Exception:
        pass

    matches = SkillLoader.resolve_skill_matches(
        skill_name,
        force_refresh=False,
        runtime_kind=runtime_kind,
        session_id=session_id,
        explicit_workspace_id=explicit_workspace_id,
        explicit_workspace_path=explicit_workspace_path,
        explicit_project_id=explicit_project_id,
    )
    if len(matches) > 1:
        lines = [
            "Error: 找到了多个同名或同引用的 skill，请改用 skillId 或绝对路径精确指定：",
        ]
        for skill in matches[:12]:
            lines.append(
                f"- {skill.get('skillName')} | id={skill.get('skillId')} | "
                f"source={skill.get('sourceType')} | root={skill.get('skillRoot')}"
            )
        return "\n".join(lines)
    if not matches:
        return f"Error: The requested skill '{skill_name}' was not found in the registry."

    skill = matches[0]
    scan_payload: dict[str, Any] | None = None
    review_payload: dict[str, Any] | None = None
    try:
        from core.audit_logger import audit_logger
        from erc.safety_guardian import safety_guardian

        scan_payload = safety_guardian.assess_skill_directory(
            skill_name=skill.get("name") or skill_name,
            skill_root=skill.get("path") or "",
            instruction_path=skill.get("instructionPath") or "",
        )
        static_verdict = str(scan_payload.get("verdict") or "").strip().lower()
        if static_verdict == "review" and bool(scan_payload.get("llmReviewRecommended")):
            review_payload = safety_guardian.review_skill_scan_with_llm(
                skill_name=skill.get("name") or skill_name,
                skill_root=skill.get("path") or "",
                scan_payload=scan_payload,
            )
            if review_payload:
                scan_payload["llmReview"] = review_payload
                scan_payload["reviewMode"] = "llm_assisted"
                if review_payload.get("status") == "completed":
                    review_summary = str(review_payload.get("summary") or "").strip()
                    if review_payload.get("decision") == "allow":
                        scan_payload["staticVerdict"] = static_verdict
                        scan_payload["verdict"] = "audit"
                        reasons = list(scan_payload.get("reasons") or [])
                        reasons.append(
                            f"安全复审模型认为该 skill 可放行：{review_summary or '证据不足以支持阻断。'}"
                        )
                        scan_payload["reasons"] = reasons[:10]
                    elif review_payload.get("decision") == "block":
                        scan_payload["staticVerdict"] = static_verdict
                        scan_payload["verdict"] = "block"
                        reasons = list(scan_payload.get("reasons") or [])
                        reasons.append(
                            f"安全复审模型维持阻断：{review_summary or '疑点仍然足够高风险。'}"
                        )
                        scan_payload["reasons"] = reasons[:10]
                else:
                    scan_payload["reviewMode"] = "rules_only_fallback"
        else:
            scan_payload["reviewMode"] = "rules_only"
        audit_logger.log(
            source_type="SAFETY",
            action="skill_scan",
            status=(
                "ERROR"
                if scan_payload.get("verdict") == "block"
                else "WARNING"
                if scan_payload.get("verdict") == "review"
                else "INFO"
            ),
            details=json.dumps(
                {
                    "skillId": skill.get("skillId") or "",
                    "skillName": skill.get("name") or skill_name,
                    "skillPath": skill.get("path") or "",
                    "instructionPath": skill.get("instructionPath") or "",
                    **scan_payload,
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        scan_payload = None

    if scan_payload and scan_payload.get("verdict") == "block":
        try:
            from core.extensions_runtime import extensions_runtime_service

            extensions_runtime_service.emit_skill_blocked(
                skill_id=str(skill.get("skillId") or ""),
                skill_name=skill.get("name") or skill_name,
                skill_path=skill.get("path") or "",
                root_path=skill.get("rootPath") or skill.get("path") or "",
                source_type=str(skill.get("sourceType") or ""),
                verdict=str(scan_payload.get("verdict") or "block"),
                confidence=float(scan_payload.get("confidence") or 0.0),
                skill_trust_score=int(scan_payload.get("skillTrustScore") or 0),
                audit_id=str(scan_payload.get("auditId") or ""),
                reasons=list(scan_payload.get("reasons") or []),
                flagged_files=list(scan_payload.get("flaggedFiles") or []),
            )
        except Exception:
            pass
        reasons = "\n".join(f"- {item}" for item in list(scan_payload.get("reasons") or [])[:8]) or "- Safety Guardian 未提供具体原因。"
        flagged_files = "\n".join(
            f"- {item.get('path')}: {', '.join(str(entry.get('label') or '') for entry in list(item.get('findings') or [])[:4] if str(entry.get('label') or '').strip()) or '高风险特征'}"
            for item in list(scan_payload.get("flaggedFiles") or [])[:12]
        ) or "- 未返回命中文件详情。"
        return (
            f"=== SKILL BLOCKED BY SAFETY GUARDIAN ===\n"
            f"Skill ID: {skill.get('skillId') or ''}\n"
            f"Skill Name: {skill.get('skillName') or skill.get('name') or skill_name}\n"
            f"Skill Root: {skill.get('skillRoot') or skill.get('path') or ''}\n"
            f"Source Type: {skill.get('sourceType') or ''}\n"
            f"Verdict: {scan_payload.get('verdict')}\n"
            f"Confidence: {scan_payload.get('confidence')}\n"
            f"Skill Trust Score: {scan_payload.get('skillTrustScore')}\n"
            f"Audit ID: {scan_payload.get('auditId')}\n"
            f"Reasons:\n{reasons}\n"
            f"Flagged Files:\n{flagged_files}\n\n"
            f"Safety Guardian 已阻断该 skill 的说明读取。不要继续使用这个 skill，"
            f"请改用其他 skill、MCP、插件工具或系统工具继续完成当前任务。"
        )

    safety_banner = ""
    if scan_payload and scan_payload.get("verdict") in {"audit", "review"}:
        verdict = str(scan_payload.get("verdict") or "").strip().lower()
        reasons = "\n".join(f"- {item}" for item in list(scan_payload.get("reasons") or [])[:6]) or "- Safety Guardian 未返回额外说明。"
        banner_title = "=== SKILL SAFETY REVIEW ==="
        banner_mode = "审计放行" if verdict == "audit" else "允许读取，但建议复核"
        safety_banner = (
            f"{banner_title}\n"
            f"Skill ID: {skill.get('skillId') or ''}\n"
            f"Skill Name: {skill.get('skillName') or skill.get('name') or skill_name}\n"
            f"Verdict: {verdict}\n"
            f"Mode: {banner_mode}\n"
            f"Governance Target: {scan_payload.get('governanceTarget') or 'skill_supply_chain'}\n"
            f"Posture: {scan_payload.get('posture') or ''}\n"
            f"Audit ID: {scan_payload.get('auditId')}\n"
            f"Reasons:\n{reasons}\n\n"
        )

    try:
        from core.extensions_runtime import extensions_runtime_service

        extensions_runtime_service.emit_skill_loaded(
            skill_id=str(skill.get("skillId") or ""),
            skill_name=skill["name"],
            skill_path=skill["path"],
        )
    except Exception:
        pass

    available_files = list(skill.get("availableFiles") or [])
    structure = "\n".join(f"- {item}" for item in available_files[:64]) if available_files else "- (no extra references/scripts/assets/templates found)"
    return (
        f"{safety_banner}"
        f"=== SKILL ENTRYPOINTS ===\n"
        f"Skill ID: {skill.get('skillId') or ''}\n"
        f"Skill Name: {skill.get('skillName') or skill.get('name') or skill_name}\n"
        f"Source Type: {skill.get('sourceType') or ''}\n"
        f"Visibility: {skill.get('visibility') or ''}\n"
        f"Workspace Path: {skill.get('workspacePath') or ''}\n"
        f"Workspace ID: {skill.get('workspaceId') or ''}\n"
        f"Project ID: {skill.get('projectId') or ''}\n"
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
