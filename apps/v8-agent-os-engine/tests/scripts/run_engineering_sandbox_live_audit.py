from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.database import db  # noqa: E402
from run_supervisor_runtime_skill_live_audit import (  # noqa: E402
    DEFAULT_ENGINE_URL,
    _engine_api_base,
    _json_request,
    _wait_for_engine,
)


TERMINAL_RUN_STATES = {
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "waiting_input",
    "waiting_approval",
}
TERMINAL_EPISODE_STATES = {"completed", "merged", "degraded", "failed", "cancelled"}


def _git_head(workspace: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return str(result.stdout or "").strip() if result.returncode == 0 else ""


def _submit(
    *,
    engine_url: str,
    session_id: str,
    project_id: str,
    workspace: Path,
    target_relative: str,
    model_profile: str = "",
    target_agent: str = "",
) -> str:
    data: dict[str, Any] = {
        "conversationId": session_id,
        "clientMessageId": f"engineering-sandbox-{session_id}",
        "projectId": project_id,
        "workspaceId": project_id,
        "workspacePath": str(workspace),
        "scopeMode": "explicit",
        "engineeringSandboxLiveAudit": True,
        "engineeringMode": "force",
        "safetyApprovalMode": "reduced",
    }
    normalized_model_profile = str(model_profile or "").strip()
    if normalized_model_profile:
        data["modelProfile"] = normalized_model_profile
    normalized_target_agent = str(target_agent or "").strip()
    target_agent_instruction = (
        f"该直接子 Agent 必须精确选择 {normalized_target_agent}；"
        if normalized_target_agent
        else ""
    )
    response = _json_request(
        f"{_engine_api_base(engine_url)}/chat/submit",
        method="POST",
        payload={
            "session_id": session_id,
            "conversationId": session_id,
            "clientMessageId": f"engineering-sandbox-{session_id}",
            "stream": False,
            "projectId": project_id,
            "workspaceId": project_id,
            "workspacePath": str(workspace),
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "这是受控工程沙箱 live 验收。请修复工作区中的 "
                        f"{target_relative}，当前运行会触发 NameError。修复后执行文件，标准输出必须严格为 "
                        "sandbox-live-ok。只允许修改这个文件，不要创建报告或其他产物。"
                        "必须由 Engineering runtime 执行：先委派一个直接子 Agent 完成修复；"
                        f"{target_agent_instruction}"
                        "该子 Agent 必须再委派一个孙 Agent 对文件内容和实际运行结果做独立验证并回流证据。"
                        "孙 Agent 必须是直接子 Agent 的临时镜像 worker，不得选择、冒充或持久化为另一个注册 Agent；"
                        "验证角色只是能力要求，不是身份名称。验证时直接读取文件并运行 `python src/sandbox_live.py`，"
                        "复用工具返回的命令、退出码、stdout 和 stderr 作为证据，不要创建临时取证文件或复杂重定向命令。"
                        "Supervisor 最后必须验收回流结果；不能用本地直接写入替代委派，也不能只给说明。"
                        "Supervisor 不得另外派一个平级验证任务来冒充孙 Agent；孙 Agent 的 parentEpisodeId 必须指向该直接子 Agent。"
                    ),
                }
            ],
            "data": data,
        },
        timeout=30,
    )
    return str(response.get("run_id") or response.get("runId") or "").strip()


def _load_worktree_evidence(run_id: str) -> list[dict[str, Any]]:
    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT wt.*, repo.original_workspace_root, repo.repository_root,
                   repo.workspace_relative_path, lease.lease_id, lease.actor_role,
                   lease.runtime_kind, lease.execution_mode, lease.network_profile,
                   lease.state AS lease_state, lease.policy_digest,
                   lease.capabilities_json, lease.policy_json
            FROM engineering_worktrees wt
            JOIN managed_git_repositories repo ON repo.repository_id = wt.repository_id
            LEFT JOIN sandbox_execution_leases lease ON lease.worktree_id = wt.worktree_id
            WHERE wt.run_id = ?
            ORDER BY wt.created_at ASC, lease.created_at ASC
            """,
            (run_id,),
        ).fetchall()
    evidence: list[dict[str, Any]] = []
    for row in rows:
        change_set = json.loads(str(row["change_set_json"] or "{}"))
        policy = json.loads(str(row["policy_json"] or "{}"))
        capabilities = json.loads(str(row["capabilities_json"] or "{}"))
        evidence.append(
            {
                "worktreeId": str(row["worktree_id"]),
                "kind": str(row["worktree_kind"] or "task"),
                "state": str(row["state"] or ""),
                "delegationId": str(row["delegation_id"] or "") or None,
                "parentWorktreeId": str(row["parent_worktree_id"] or "") or None,
                "worktreeRoot": str(row["worktree_root"]),
                "worktreeWorkspaceRoot": str(row["worktree_workspace_root"]),
                "originalWorkspaceRoot": str(row["original_workspace_root"]),
                "repositoryRoot": str(row["repository_root"]),
                "workspaceRelativePath": str(row["workspace_relative_path"] or "."),
                "leaseId": str(row["lease_id"] or "") or None,
                "leaseState": str(row["lease_state"] or "") or None,
                "actorRole": str(row["actor_role"] or "") or None,
                "runtimeKind": str(row["runtime_kind"] or "") or None,
                "executionMode": str(row["execution_mode"] or "") or None,
                "networkProfile": str(row["network_profile"] or "") or None,
                "policyDigest": str(row["policy_digest"] or "") or None,
                "policyWorktreeRoot": str(policy.get("worktree_root") or ""),
                "policyOriginalWorkspaceRoot": str(policy.get("original_workspace_root") or ""),
                "policyWriteSet": list(policy.get("write_set") or []),
                "enforcementLevel": str(capabilities.get("enforcement_level") or ""),
                "changedPaths": list(change_set.get("changedPaths") or []),
                "errorCode": str(row["error_code"] or "") or None,
            }
        )
    return evidence


def _cancel_run(engine_url: str, run_id: str) -> None:
    if not run_id:
        return
    try:
        _json_request(
            f"{_engine_api_base(engine_url)}/runs/{run_id}/commands/cancel",
            method="POST",
            payload={"reason": "engineering_sandbox_live_audit_complete"},
            timeout=15,
        )
    except Exception:
        pass


def _iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _canonical_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in messages:
        for item in _iter_dicts(message.get("nodes") or []):
            name = str(item.get("toolName") or item.get("tool_name") or "").strip()
            args = item.get("args") or item.get("arguments") or item.get("input")
            if not name or not isinstance(args, dict):
                continue
            calls.append({"name": name, "args": dict(args)})
    return calls


def _capsule_checks(episodes: list[dict[str, Any]], *, target_relative: str) -> dict[str, Any]:
    checked = 0
    valid = 0
    for episode in episodes:
        inputs = episode.get("inputs") if isinstance(episode.get("inputs"), dict) else {}
        for brief in list(inputs.get("workerBriefs") or inputs.get("tasks") or []):
            if not isinstance(brief, dict):
                continue
            capsule = (
                brief.get("engineeringTaskCapsule")
                if isinstance(brief.get("engineeringTaskCapsule"), dict)
                else {}
            )
            if str(capsule.get("executionMode") or "").strip() != "write":
                continue
            checked += 1
            write_set = [str(value).replace("\\", "/") for value in list(capsule.get("writeSet") or [])]
            expected = [str(value).replace("\\", "/") for value in list(capsule.get("expectedOutputs") or [])]
            if (
                str(capsule.get("contractStatus") or "").strip() == "valid"
                and target_relative in write_set
                and any(target_relative in item for item in expected)
                and capsule.get("acceptance") not in (None, "", [], {})
            ):
                valid += 1
    return {"checked": checked, "valid": valid}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a real Supervisor -> Engineering -> child -> grandchild direct-workspace audit."
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--max-wait", type=int, default=360)
    parser.add_argument(
        "--model-profile",
        default="",
        help="Optional configured model ref for this live audit; role routing remains the default when omitted.",
    )
    parser.add_argument(
        "--target-agent",
        default="",
        help="Optional exact direct-subagent display name used to keep a live audit's provider variable controlled.",
    )
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if not args.live or not args.allow_side_effects:
        print("Refusing live model and workspace writes without --live --allow-side-effects.")
        return 2

    ok, error = _wait_for_engine(args.engine_url, timeout=20)
    if not ok:
        print(f"Engine is unavailable: {error}")
        return 1

    workspace = Path(args.workspace).expanduser().resolve()
    if workspace.exists() and any(workspace.iterdir()):
        print("The live audit workspace must be empty; existing workspaces are never adopted implicitly.")
        return 2
    workspace.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    project_id = f"engineering-sandbox-live-{timestamp}"
    session_id = f"engineering-sandbox-live-{timestamp}"
    target_relative = "src/sandbox_live.py"
    target = workspace / target_relative
    run_id = ""
    project_created = False
    result: dict[str, Any] = {}
    try:
        _json_request(
            f"{_engine_api_base(args.engine_url)}/projects",
            method="POST",
            payload={
                "id": project_id,
                "name": "Engineering sandbox live audit",
                "workspaceId": project_id,
                "workspacePath": str(workspace),
                "workspaceTrustState": "trusted",
                "workspaceTrustSource": "live_audit",
            },
            timeout=30,
        )
        project_created = True
        repository_status = _json_request(
            f"{_engine_api_base(args.engine_url)}/projects/{project_id}/engineering-workspace",
            timeout=20,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("print(sandbox_live_value)\n", encoding="utf-8")
        head_before = _git_head(workspace)
        git_directory_before = (workspace / ".git").exists()

        db.create_or_update_session(
            session_id=session_id,
            title="Engineering sandbox live audit",
            user_id="live-audit",
            metadata={
                "workspacePath": str(workspace),
                "workspace_path": str(workspace),
                "projectId": project_id,
                "workspaceId": project_id,
                "hiddenFromHistory": True,
                "source": "live_audit",
            },
        )
        run_id = _submit(
            engine_url=args.engine_url,
            session_id=session_id,
            project_id=project_id,
            workspace=workspace,
            target_relative=target_relative,
            model_profile=args.model_profile,
            target_agent=args.target_agent,
        )
        deadline = time.monotonic() + max(60, int(args.max_wait or 360))
        run: dict[str, Any] | None = None
        episodes: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            run = db.get_run_record(run_id) if run_id else None
            episodes = db.list_runtime_episodes(run_id=run_id, limit=200) if run_id else []
            run_state = str((run or {}).get("status") or "").strip().lower()
            active_episodes = [
                item
                for item in episodes
                if str(item.get("state") or "").strip().lower() not in TERMINAL_EPISODE_STATES
            ]
            if run_state in TERMINAL_RUN_STATES and not active_episodes:
                break
            time.sleep(1)

        run_state = str((run or {}).get("status") or "").strip().lower()
        engineering_episodes = [
            item for item in episodes if str(item.get("kind") or "").strip().lower() == "engineering"
        ]
        delegation_episodes = [
            item for item in episodes if str(item.get("kind") or "").strip().lower() == "delegation"
        ]
        delegation_ids = {
            str(item.get("id") or item.get("episodeId") or "").strip()
            for item in delegation_episodes
            if str(item.get("id") or item.get("episodeId") or "").strip()
        }
        nested_delegation = any(
            str(item.get("parentEpisodeId") or item.get("parent_episode_id") or "").strip() in delegation_ids
            for item in delegation_episodes
        )
        worktrees = _load_worktree_evidence(run_id)
        canonical_messages = db.get_chat_canonical_messages(session_id)
        canonical_tool_calls = _canonical_tool_calls(canonical_messages)
        manual_local_polling = any(
            item.get("name") == "delegation_broker"
            and str((item.get("args") or {}).get("mode") or "").strip().lower() in {"observe", "status"}
            for item in canonical_tool_calls
        )
        assistant_surface = "\n".join(
            str(item.get("content_text") or "")
            for item in canonical_messages
            if str(item.get("role") or "").strip().lower() == "assistant"
        )
        false_git_prerequisite = bool(
            re.search(
                r"(?i)(git\s+is\s+required|engineering\s+requires\s+git|must\s+enable\s+git|"
                r"必须(?:先)?启用\s*git|工程(?:模式|执行).{0,20}(?:必须|需要).{0,10}git|接管\s*git)",
                assistant_surface,
            )
        )
        progress_events = [
            item
            for item in db.get_runtime_events_for_run(run_id, session_id=session_id, limit=1000)
            if str(item.get("topic") or "").strip() == "runtime.episode.progress"
        ]
        direct_strategy_episodes = [
            item
            for item in delegation_episodes
            if str(
                ((item.get("inputs") or {}).get("engineeringWorkspace") or {}).get(
                    "engineering_workspace_strategy"
                )
                or ""
            ).strip()
            == "direct"
        ]
        capsule_checks = _capsule_checks(delegation_episodes, target_relative=target_relative)
        handoff_count = sum(len(list(item.get("handoffRefs") or [])) for item in delegation_episodes)
        target_execution = subprocess.run(
            [sys.executable, str(target)],
            cwd=str(target.parent),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        head_after = _git_head(workspace)
        git_directory_after = (workspace / ".git").exists()
        changed_paths = {
            str(path)
            for item in worktrees
            for path in list(item.get("changedPaths") or [])
        }
        parallel_isolation = dict(repository_status.get("parallelIsolation") or {})
        passed = bool(
            str((repository_status.get("repository") or {}).get("state") or "") == "adoption_required"
            and bool(parallel_isolation.get("directExecutionAvailable"))
            and not bool(parallel_isolation.get("enabled"))
            and run_state == "completed"
            and engineering_episodes
            and any(
                str(item.get("state") or "").strip().lower() in {"completed", "merged"}
                for item in engineering_episodes
            )
            and len(delegation_episodes) >= 2
            and nested_delegation
            and direct_strategy_episodes
            and capsule_checks["valid"] >= 1
            and handoff_count >= 1
            and progress_events
            and not manual_local_polling
            and not false_git_prerequisite
            and not worktrees
            and not git_directory_before
            and not git_directory_after
            and not head_before
            and not head_after
            and target_execution.returncode == 0
            and str(target_execution.stdout or "").strip() == "sandbox-live-ok"
        )
        result = {
            "status": "ok" if passed else "failed",
            "sessionId": session_id,
            "runId": run_id,
            "runStatus": run_state,
            "projectId": project_id,
            "workspace": str(workspace),
            "modelProfile": str(args.model_profile or "").strip() or "role:supervisor",
            "targetAgent": str(args.target_agent or "").strip() or "supervisor-selected",
            "repositoryState": str((repository_status.get("repository") or {}).get("state") or ""),
            "parallelIsolation": parallel_isolation,
            "workspaceRole": str((repository_status.get("workspace") or {}).get("role") or ""),
            "repositoryRole": str((repository_status.get("repository") or {}).get("role") or ""),
            "worktreeRole": str((repository_status.get("worktree") or {}).get("role") or ""),
            "sandboxRole": str((repository_status.get("sandbox") or {}).get("role") or ""),
            "engineeringEpisodeCount": len(engineering_episodes),
            "delegationEpisodeCount": len(delegation_episodes),
            "nestedDelegationObserved": nested_delegation,
            "directStrategyEpisodeCount": len(direct_strategy_episodes),
            "capsuleChecks": capsule_checks,
            "handoffCount": handoff_count,
            "progressEventCount": len(progress_events),
            "manualLocalPollingObserved": manual_local_polling,
            "falseGitPrerequisiteObserved": false_git_prerequisite,
            "gitDirectoryCreated": git_directory_after,
            "worktreeCount": len(worktrees),
            "changedPaths": sorted(changed_paths),
            "userHeadUnchanged": head_before == head_after,
            "targetReturnCode": target_execution.returncode,
            "targetStdout": str(target_execution.stdout or "").strip(),
            "worktrees": worktrees,
        }
    except Exception as exc:
        result = {
            "status": "failed",
            "sessionId": session_id,
            "runId": run_id,
            "projectId": project_id,
            "workspace": str(workspace),
            "modelProfile": str(args.model_profile or "").strip() or "role:supervisor",
            "targetAgent": str(args.target_agent or "").strip() or "supervisor-selected",
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if run_id:
            run = db.get_run_record(run_id)
            if str((run or {}).get("status") or "").strip().lower() not in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
            }:
                _cancel_run(args.engine_url, run_id)
        if project_created:
            try:
                _json_request(
                    f"{_engine_api_base(args.engine_url)}/projects/{project_id}",
                    method="DELETE",
                    timeout=20,
                )
            except Exception:
                result["projectCleanup"] = "failed"

    output = Path(args.output).expanduser().resolve() if args.output else None
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
