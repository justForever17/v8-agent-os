from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ACTIVE_TARGETS = (
    "README.md",
    "README-ZH.md",
    "docs/V8_AGENT_OS_API_REFERENCE_ZH.md",
    "docs/V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md",
    "docs/V8_AGENT_OS_QUICK_START_ZH.md",
    "docs/V8OS_AGENT_OS_GLOBAL_OVERVIEW_ZH.md",
    "docs/extensions/V8OS_EXTENSIONS_RUNTIME_PUBLIC_OVERVIEW_ZH.md",
    "docs/PLUGIN_HOST",
    "apps/v8-agent-os-engine/main.py",
    "apps/v8-agent-os-engine/api",
    "apps/v8-agent-os-engine/agents",
    "apps/v8-agent-os-engine/core",
    "apps/v8-agent-os-engine/erc",
    "apps/v8-agent-os-engine/graph",
    "apps/v8-agent-os-engine/runtimes",
    "apps/v8-agent-os-admin/src",
    "apps/v8-agent-os-web/src",
    "apps/v8-agent-os-phone/src",
    "packages/session-realtime/src",
)

TEXT_SUFFIXES = frozenset({".cjs", ".js", ".json", ".md", ".mjs", ".py", ".ts", ".tsx", ".yaml", ".yml"})
FORBIDDEN_PATTERNS = (
    re.compile(r"openclaw", re.IGNORECASE),
    re.compile(r"plugin[_-]?host", re.IGNORECASE),
    re.compile(r"插件桥接"),
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    excerpt: str


def _iter_files(repo_root: Path) -> Iterable[Path]:
    for relative in ACTIVE_TARGETS:
        target = repo_root / relative
        if target.is_file():
            yield target
            continue
        if not target.is_dir():
            continue
        for path in target.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if "__pycache__" in path.parts:
                continue
            yield path


def scan_removed_openclaw_residue(repo_root: Path) -> list[Violation]:
    root = repo_root.resolve()
    violations: list[Violation] = []
    for path in _iter_files(root):
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            if not any(pattern.search(line) for pattern in FORBIDDEN_PATTERNS):
                continue
            violations.append(
                Violation(
                    path=path.relative_to(root).as_posix(),
                    line=line_number,
                    excerpt=line.strip()[:240],
                )
            )
    return violations


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when removed OpenClaw/plugin-host product logic remains on active V8OS surfaces."
    )
    parser.add_argument("--root", type=Path, default=_default_repo_root())
    args = parser.parse_args()
    violations = scan_removed_openclaw_residue(args.root)
    if not violations:
        print("Removed OpenClaw/plugin-host residue audit: clean")
        return 0
    print(f"Removed OpenClaw/plugin-host residue audit: {len(violations)} violation(s)")
    for item in violations:
        print(f"{item.path}:{item.line}: {item.excerpt}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
