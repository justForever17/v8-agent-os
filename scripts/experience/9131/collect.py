"""Read-only source coverage inventory. No user state or application imports."""
import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path

DIMENSIONS = ["function", "convenience", "visual", "performance"]
STATES = ["default", "custom", "loading", "empty", "error", "dirty", "saving", "cancel", "conflict", "late_response", "reload"]
VARIANTS = ["1440x900 light", "1440x900 dark", "1024x768", "768x1024", "390x844 touch", "200% text", "keyboard", "reduced_motion"]
SUBVIEWS = {
    "/admin/model-hub": ["providers", "models", "modality", "voice", "add provider", "edit model", "exact parameters", "OAuth/local/platform"],
    "/admin/chat-runtime": ["supervisor", "subagents", "query/back"],
    "/admin/subagents": ["create/edit", "family", "research shards", "recursive budget", "external worker", "empty allowlist"],
    "/admin/memory": ["context", "preferences", "logs", "knowledge", "workflows", "artifacts", "graph", "agent", "runtime", "config", "upload"],
    "/admin/automation": ["cron", "hooks", "wake ingress"],
    "/admin/automation/cron": ["create/edit", "timezone/DST", "target/recovery", "enable/run/delete"],
    "/admin/automation/hooks": ["create/edit", "event types", "target", "enable/delete"],
    "/admin/extensions": ["skills", "mcp", "scope", "import ZIP", "stdio", "HTTP/SSE", "env/headers", "call settings"],
    "/admin/extensions/store": ["skills", "mcp", "international", "modelscope", "search/pagination", "detail README", "install status", "manual login resume"],
    "/admin/plugins": ["list", "detail", "connect/config", "permissions", "activity", "dry-run", "update/uninstall/rollback"],
    "/admin/rpa": ["canvas", "steps", "properties", "variables", "elements", "run", "diagnostics", "JSON"],
    "/admin/system-base": ["service", "storage/S3", "feature packs", "browser profile", "remote observation", "elevation", "unlock", "exact config"],
    "/admin/network-supervisor-runtime": ["peers", "invite", "connection", "task", "relay", "third-party apps"],
    "/admin/operations-center": ["pending", "runs", "problems", "focusRun/focusSession", "raw evidence", "pagination"],
    "/admin/projects-workspaces": ["list", "default directory", "trust", "AGENTS full editor", "Git/non-Git", "switch draft"],
    "/admin/users": ["owner", "pair", "expire/consume/revoke", "profile/instance", "logout/lock"],
    "/chat": ["composer", "history", "queue", "attachments", "selection", "canvas", "terminal", "settings", "background", "Admin/Shell return"],
}


def command(repo, *args):
    r = subprocess.run(args, cwd=repo, text=True, encoding="utf-8", errors="replace", capture_output=True)
    return {"exit": r.returncode, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}


def collect(repo):
    pages = []
    for app in ("admin", "web", "phone"):
        base = repo / "apps" / f"v8-agent-os-{app}"
        paths = sorted((base / ("app" if app == "phone" else "src/app")).rglob("*.tsx" if app == "phone" else "page.tsx"))
        for file in paths:
            if app == "phone" and (file.name.startswith("_") or file.name == "+html.tsx"):
                continue
            parts = file.relative_to(base / ("app" if app == "phone" else "src/app")).parts
            segments = [p for p in parts[:-1] if not p.startswith("(")]
            if app == "phone" and file.stem != "index":
                segments.append(file.stem)
            route = "/" + "/".join(segments)
            pages.append({"id": f"{app}:{route}", "app": app, "route": route, "file": file.relative_to(repo).as_posix(), "sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "subviews": SUBVIEWS.get(route, ["see acceptance-matrix.md capability ledger"]), "evidence": "SOURCE", "dimensions": dict.fromkeys(DIMENSIONS, "NOT_RUN")})
    return {"schema": 1, "sourceHead": command(repo, "git", "rev-parse", "HEAD")["stdout"], "branch": command(repo, "git", "branch", "--show-current")["stdout"], "platform": platform.platform(), "node": command(repo, "node", "--version"), "counts": {app: sum(x["app"] == app for x in pages) for app in ("admin", "web", "phone")}, "qualification": "Route inventory only. SOURCE never implies any dimension passed.", "applicableStates": STATES, "applicableVariants": VARIANTS, "pages": pages}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    repo = args.repo.resolve()
    if command(repo, "git", "rev-parse", "--show-toplevel")["exit"]:
        p.error("not a Git repository; worktree .git files are supported")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result = collect(repo)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"head": result["sourceHead"], "counts": result["counts"], "out": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
