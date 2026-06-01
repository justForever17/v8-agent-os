from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_SOURCE_MARKER_RE = re.compile(r"(https?://|来源|source|reference|citation|可信|confidence)", re.IGNORECASE)


@dataclass
class SkillArtifactValidationResult:
    ok: bool
    status: str
    skill_root: str
    findings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "skillRoot": self.skill_root,
            "findings": list(self.findings),
            "metadata": dict(self.metadata),
        }


class SkillArtifactValidator:
    """Validate generated Skill artifacts before an Engineering episode claims completion."""

    MIN_GENERIC_SKILL_MD_CHARS = 1000
    MIN_HUASHU_SKILL_MD_CHARS = 4000
    REQUIRED_RESEARCH_FILES = (
        "01-writings.md",
        "02-conversations.md",
        "03-expression-dna.md",
        "04-external-views.md",
        "05-decisions.md",
        "06-timeline.md",
    )

    @classmethod
    def validate(
        cls,
        skill_root: str | Path,
        *,
        require_huashu_research: bool = False,
        require_source_markers: bool = True,
    ) -> SkillArtifactValidationResult:
        root = Path(skill_root)
        findings: list[str] = []
        metadata: dict[str, Any] = {}
        skill_file = root / "SKILL.md"
        if not root.exists() or not root.is_dir():
            return SkillArtifactValidationResult(False, "skill_root_missing", str(root), [f"skill root 不存在：{root}"])
        if not skill_file.exists() or not skill_file.is_file():
            return SkillArtifactValidationResult(False, "skill_md_missing", str(root), [f"缺少 SKILL.md：{skill_file}"])

        text = skill_file.read_text(encoding="utf-8", errors="replace")
        metadata["skillMdChars"] = len(text)
        if not text.strip():
            findings.append("SKILL.md 为空。")
        min_skill_chars = cls.MIN_HUASHU_SKILL_MD_CHARS if require_huashu_research else cls.MIN_GENERIC_SKILL_MD_CHARS
        if len(text.strip()) < min_skill_chars:
            findings.append(f"SKILL.md 内容过短：{len(text.strip())}<{min_skill_chars}。")

        frontmatter, body = cls._split_frontmatter(text)
        if not frontmatter:
            findings.append("缺少 YAML frontmatter。")
        else:
            try:
                parsed = yaml.safe_load(frontmatter) or {}
                if not isinstance(parsed, dict):
                    parsed = {}
            except Exception as exc:  # noqa: BLE001 - validator reports schema facts, not exceptions.
                parsed = {}
                findings.append(f"YAML frontmatter 解析失败：{type(exc).__name__}: {exc}")
            metadata["frontmatter"] = {key: parsed.get(key) for key in ("name", "description") if key in parsed}
            if not str(parsed.get("name") or "").strip():
                findings.append("frontmatter 缺少 name。")
            if not str(parsed.get("description") or "").strip():
                findings.append("frontmatter 缺少 description。")

        lower_body = body.lower()
        if not any(marker in text for marker in ("触发", "使用说明", "Use when", "When to use", "激活")):
            findings.append("缺少触发/使用说明。")
        if not any(marker in text for marker in ("诚实边界", "边界", "Honesty", "Limitations", "Assumptions")):
            findings.append("缺少诚实边界或限制说明。")
        if not any(marker in text for marker in ("调研来源", "来源", "References", "Sources", "source")):
            findings.append("缺少调研来源说明。")
        if "description" not in lower_body and not frontmatter:
            findings.append("缺少可加载 skill 描述信息。")

        references_root = root / "references" / "research"
        metadata["referencesResearchPath"] = str(references_root)
        if require_huashu_research:
            if not references_root.exists() or not references_root.is_dir():
                findings.append("缺少 references/research 目录。")
            else:
                missing_files: list[str] = []
                weak_files: list[str] = []
                for filename in cls.REQUIRED_RESEARCH_FILES:
                    path = references_root / filename
                    if not path.exists():
                        missing_files.append(filename)
                        continue
                    content = path.read_text(encoding="utf-8", errors="replace")
                    if len(content.strip()) < 120:
                        weak_files.append(f"{filename}: 内容过短")
                    elif require_source_markers and not _SOURCE_MARKER_RE.search(content):
                        weak_files.append(f"{filename}: 缺少来源/可信度标记")
                if missing_files:
                    findings.append("缺少调研文件：" + ", ".join(missing_files))
                if weak_files:
                    findings.append("调研文件质量不足：" + "; ".join(weak_files[:6]))

        status = "valid" if not findings else "invalid"
        return SkillArtifactValidationResult(not findings, status, str(root), findings, metadata)

    @staticmethod
    def _split_frontmatter(text: str) -> tuple[str, str]:
        normalized = str(text or "").replace("\r\n", "\n")
        if not normalized.startswith("---\n"):
            return "", normalized
        end = normalized.find("\n---", 4)
        if end < 0:
            return "", normalized
        frontmatter = normalized[4:end].strip()
        body = normalized[end + len("\n---") :].lstrip("\n")
        return frontmatter, body
