from __future__ import annotations

from runtimes.extensions.skills.artifact_validator import SkillArtifactValidator


def test_skill_artifact_validator_rejects_missing_frontmatter(tmp_path):
    skill_root = tmp_path / ".agents" / "skills" / "bad-skill"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("# Bad Skill\n\n只有正文，没有 schema。\n", encoding="utf-8")

    result = SkillArtifactValidator.validate(skill_root)

    assert not result.ok
    assert any("YAML frontmatter" in item for item in result.findings)


def test_skill_artifact_validator_accepts_huashu_research_pack(tmp_path):
    skill_root = tmp_path / ".agents" / "skills" / "sanyueqi-perspective"
    research_root = skill_root / "references" / "research"
    research_root.mkdir(parents=True)
    rich_skill_body = (
        """---
name: sanyueqi-perspective
description: 用三月七视角分析问题和表达建议。
---

# 三月七视角

## 触发说明
当用户要求用三月七视角分析、安慰、吐槽或做角色化表达时使用。

## 心智模型
保留角色语气，但不要编造官方剧情。

## 决策启发式
优先保护同伴，先确认风险，再用轻快直接的语言鼓励对方。

## 表达DNA
轻快、真诚、带一点拍照记录感。

## 时间线
按公开剧情节点维护，不覆盖未验证版本。

## 诚实边界
未知设定必须标注假设，不把玩家二创当官方事实。

## 调研来源
见 references/research 下的来源记录。
"""
        + "\n".join(
            f"- 细化规则 {idx}: 输出时必须保留三月七的乐观、朋友优先、拍照记录感和对未知身世的诚实边界。"
            for idx in range(90)
        )
    )
    (skill_root / "SKILL.md").write_text(
        rich_skill_body,
        encoding="utf-8",
    )
    for name in SkillArtifactValidator.REQUIRED_RESEARCH_FILES:
        (research_root / name).write_text(
            (
                f"# {name}\n\n"
                "结论：这是经过来源约束的调研条目，包含角色设定、剧情表达、玩家解读和时间线证据。\n\n"
                "来源：https://example.com/honkai-star-rail/march-7th\n"
                "可信度：medium。该条目用于测试 validator 对来源标记和正文长度的校验。\n"
            ),
            encoding="utf-8",
        )

    result = SkillArtifactValidator.validate(skill_root, require_huashu_research=True)

    assert result.ok
    assert result.status == "valid"


def test_skill_artifact_validator_rejects_short_huashu_skill_md(tmp_path):
    skill_root = tmp_path / ".agents" / "skills" / "sanyueqi-perspective"
    research_root = skill_root / "references" / "research"
    research_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        """---
name: sanyueqi-perspective
description: 用三月七视角分析问题和表达建议。
---

# 三月七视角

## 触发说明
当用户要求用三月七视角分析时使用。

## 心智模型
朋友优先。

## 决策启发式
保护同伴。

## 表达DNA
轻快真诚。

## 时间线
信息截止到测试版本。

## 诚实边界
未知设定标注假设。

## 调研来源
见 references/research。
""",
        encoding="utf-8",
    )
    for name in SkillArtifactValidator.REQUIRED_RESEARCH_FILES:
        (research_root / name).write_text(
            (
                f"# {name}\n\n"
                "结论：这是经过来源约束的调研条目，包含角色设定、剧情表达、玩家解读和时间线证据。\n\n"
                "来源：https://example.com/honkai-star-rail/march-7th\n"
                "可信度：medium。该条目用于测试 validator 对来源标记和正文长度的校验。\n"
            ),
            encoding="utf-8",
        )

    result = SkillArtifactValidator.validate(skill_root, require_huashu_research=True)

    assert not result.ok
    assert any("内容过短" in item for item in result.findings)
