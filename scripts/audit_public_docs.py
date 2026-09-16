from __future__ import annotations

import re
import posixpath
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "README.md",
    ROOT / "README-ZH.md",
    ROOT / "docs" / "V8_AGENT_OS_QUICK_START_ZH.md",
    ROOT / "docs" / "V8_AGENT_OS_API_REFERENCE_ZH.md",
    ROOT / "docs" / "V8_AGENT_OS_CONFIG_GUIDE_ZH.md",
    ROOT / "docs" / "V8_AGENT_OS_DEVELOPER_GUIDE_ZH.md",
    ROOT / "docs" / "V8_AGENT_OS_CLI_REFERENCE_ZH.md",
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


def tracked_files(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True,
    )
    return set(result.stdout.decode("utf-8").rstrip("\0").split("\0")) - {""}


def boundary_violations(root: Path, tracked: set[str]) -> list[str]:
    """Inspect the index even when private files remain in the local checkout."""
    data = "\0".join(sorted(tracked)).encode("utf-8") + b"\0"
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"], cwd=root,
        input=data, capture_output=True,
    )
    if ignored.returncode not in (0, 1):
        raise RuntimeError(ignored.stderr.decode("utf-8", errors="replace"))
    violations = [
        f"{path}:tracked_outside_public_allowlist"
        for path in ignored.stdout.decode("utf-8").split("\0")
        if path.startswith("docs/") or path.endswith((".md", ".mdx")) or "/public/" in path
    ]
    attribute_paths = tracked | {
        parent.as_posix() for path in tracked for parent in PurePosixPath(path).parents
        if parent.as_posix() != "."
    }
    attributes = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "export-ignore"], cwd=root,
        input="\0".join(sorted(attribute_paths)).encode("utf-8") + b"\0", capture_output=True, check=True,
    ).stdout.decode("utf-8").rstrip("\0").split("\0")
    excluded = set()
    for path, _attribute, value in zip(attributes[::3], attributes[1::3], attributes[2::3]):
        if value == "set":
            excluded.update(p for p in tracked if p == path or p.startswith(path + "/"))
    violations.extend(f"{path}:tracked_local_only_material" for path in sorted(excluded))
    return violations


def _without_fences(text: str) -> str:
    return re.sub(r"^(`{3,}|~{3,})[^\n]*\n.*?^\1[ \t]*$", "", text, flags=re.MULTILINE | re.DOTALL)


def _anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", _without_fences(text), re.MULTILINE):
        heading = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(f"{slug}-{count}" if count else slug)
    anchors.update(re.findall(r'(?:id|name)=["\']([^"\']+)["\']', text))
    return anchors


def link_violations(root: Path, tracked: set[str]) -> list[str]:
    violations = []
    for relative in sorted(tracked):
        if not relative.lower().endswith((".md", ".mdx")):
            continue
        source = root / relative
        text = _without_fences(source.read_text(encoding="utf-8"))
        links = re.findall(r"\[[^]\n]*\]\(<?([^\s)>]+)>?(?:\s+[^)]*)?\)", text)
        links += re.findall(r"^\s*\[[^]]+\]:\s*<?([^\s>]+)>?", text, re.MULTILINE)
        links += re.findall(r'''(?:href|src)\s*=\s*["']([^"']+)["']''', text, re.IGNORECASE)
        for link in links:
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc:
                continue
            decoded_path = unquote(parsed.path)
            target = posixpath.normpath(posixpath.join(posixpath.dirname(relative), decoded_path)) if decoded_path else relative
            if decoded_path.startswith("/"):
                # Public help may use app-root asset links; repository docs use root paths.
                if "/public/" in relative:
                    target = relative.split("/public/", 1)[0] + "/public/" + decoded_path.lstrip("/")
                    if not PurePosixPath(decoded_path).suffix and target not in tracked:
                        continue  # Only app help may link to routes such as /admin.
                else:
                    target = decoded_path.lstrip("/")
                target = posixpath.normpath(target)
            exists_in_index = target == "." or target in tracked or any(p.startswith(target.rstrip("/") + "/") for p in tracked)
            if not exists_in_index:
                violations.append(f"{relative}:unpublished_link:{link}")
            elif parsed.fragment and target.lower().endswith((".md", ".mdx")):
                if unquote(parsed.fragment) not in _anchors((root / target).read_text(encoding="utf-8")):
                    violations.append(f"{relative}:missing_anchor:{link}")
    return violations


def main() -> int:
    tracked = tracked_files(ROOT)
    violations = boundary_violations(ROOT, tracked) + link_violations(ROOT, tracked)
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
    print(f"Public documentation audit: clean ({sum(p.endswith('.md') for p in tracked)} tracked Markdown files; index boundary and local links checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
