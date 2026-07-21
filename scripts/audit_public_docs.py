from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "README.md",
    ROOT / "README-ZH.md",
    ROOT / "docs" / "V8_AGENT_OS_QUICK_START_ZH.md",
    ROOT / "docs" / "V8_AGENT_OS_API_REFERENCE_ZH.md",
    ROOT / "docs" / "V8_AGENT_OS_CONFIG_GUIDE_ZH.md",
    ROOT / "docs" / "V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md",
)

FORBIDDEN = {
    "phone_first": re.compile(
        r"phone[- ]first|os-phone.{0,16}主验收|phone.{0,24}primary surface|"
        r"os-web.{0,16}备用|web.{0,24}backup surface",
        re.IGNORECASE,
    ),
    "removed_plugin_host": re.compile(r"openclaw|clawhub|/admin/plugin-host", re.IGNORECASE),
    "desktop_bootstrap_confusion": re.compile(
        r"raw\.githubusercontent\.com/.+?/bootstrap\.(?:ps1|sh)",
        re.IGNORECASE,
    ),
    "grandchild_grant_regression": re.compile(
        r"不能.{0,20}向孙 Agent 传播|cannot.{0,32}(?:pass|propagate).{0,24}grandchild",
        re.IGNORECASE,
    ),
}

REQUIRED = {
    "README.md": ("desktop app is the main product line", "governed project execution"),
    "README-ZH.md": ("桌面版是当前主线", "受治理的项目执行"),
    "V8_AGENT_OS_QUICK_START_ZH.md": ("v8os.cmd preview --rebuild", "不等于完整桌面 Shell"),
    "V8_AGENT_OS_API_REFERENCE_ZH.md": ("Engine 是", "packages/session-realtime"),
    "V8_AGENT_OS_CONFIG_GUIDE_ZH.md": ("config-registry", "engineering-lane"),
    "V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md": ("managed worktree", "聊天 Planner 已物理删除"),
}


def _planner_line_is_historical(line: str) -> bool:
    lowered = line.lower()
    if "planner" not in lowered and "规划器" not in line:
        return True
    return any(marker in line for marker in ("删除", "移除", "历史")) or any(
        marker in lowered for marker in ("removed", "historical")
    )


def _plugin_json_line_is_negative(line: str) -> bool:
    if "plugin.json" not in line.lower():
        return True
    lowered = line.lower()
    return "不存在" in line or "不要寻找" in line or "does not exist" in lowered


def main() -> int:
    violations: list[str] = []
    for path in TARGETS:
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        for name, pattern in FORBIDDEN.items():
            match = pattern.search(text)
            if match:
                violations.append(f"{relative}:{name}:{match.group(0)}")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not _planner_line_is_historical(line):
                violations.append(f"{relative}:{line_number}:active_planner:{line.strip()}")
            if not _plugin_json_line_is_negative(line):
                violations.append(f"{relative}:{line_number}:plugin_json_claim:{line.strip()}")
        lowered = text.lower()
        for snippet in REQUIRED.get(path.name, ()):
            if snippet.lower() not in lowered:
                violations.append(f"{relative}:required:missing:{snippet}")

    if violations:
        print("Public documentation audit failed:")
        for item in violations:
            print(f"- {item}")
        return 1
    print("Public documentation audit: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
