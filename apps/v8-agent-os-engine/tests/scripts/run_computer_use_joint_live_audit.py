from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil


ENGINE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
TERMINAL_EPISODE_STATES = {"completed", "failed", "cancelled", "blocked"}


def _progress(message: str) -> None:
    print(f"[computer-use-live] {datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


def _ensure_workspace_binding(workspace: Path) -> dict[str, str]:
    from core.workspace_authority import workspace_authority_service
    from runtimes.memory.project_registry import project_registry_service

    workspace_path = str(workspace)
    project = project_registry_service.find_project_for_workspace(workspace_path=workspace_path)
    trust_state = str(getattr(project, "workspace_trust_state", "") or "").strip().lower() if project else ""
    if project is None or trust_state != "trusted":
        project = project_registry_service.save_project(
            {
                "name": "Computer Use live harness workspace",
                "workspacePath": workspace_path,
                "workspaceTrustState": "trusted",
                "workspaceTrustSource": "user_confirmed",
                "tags": ["live_harness", "computer_use"],
            }
        )
    authority = workspace_authority_service.resolve(
        runtime_kind="computer_use",
        explicit_workspace_path=workspace_path,
    )
    authority_payload = authority.as_dict() if hasattr(authority, "as_dict") else dict(authority or {})
    if not bool(authority_payload.get("sideEffectsAllowed")):
        raise RuntimeError("workspace_trust_preflight_failed")
    return {
        "projectId": str(getattr(project, "project_id", "") or authority_payload.get("projectId") or ""),
        "workspaceId": str(getattr(project, "workspace_id", "") or authority_payload.get("workspaceId") or ""),
        "workspacePath": workspace_path,
        "trustState": str(authority_payload.get("trustState") or ""),
        "trustSource": str(authority_payload.get("trustSource") or ""),
    }


def _json_request(url: str, *, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def _image_evidence(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {"ok": False, "path": str(path), "error": "missing"}
    payload = path.read_bytes()
    mime = None
    if payload.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif payload.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    return {
        "ok": mime is not None and len(payload) > 0,
        "path": str(path),
        "bytes": len(payload),
        "mime": mime,
        "magic": payload[:8].hex().upper(),
        "sha256": hashlib.sha256(payload).hexdigest().upper(),
    }


def _remove_previous_case_output(workspace: Path, *, case_id: str, mode: str) -> None:
    if case_id != "metaso":
        return
    name = "runtime-metaso-image.jpg" if mode == "direct_runtime" else "supervisor-metaso-image.jpg"
    target = workspace / "computer-use-acceptance" / name
    target.unlink(missing_ok=True)


def _qqmusic_processes() -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for process in psutil.process_iter(["pid", "name", "exe", "cmdline", "create_time"]):
        try:
            name = str(process.info.get("name") or "").lower()
            executable = str(process.info.get("exe") or "")
            if name != "qqmusic.exe" and "\\qqmusic\\" not in executable.lower():
                continue
            processes.append({
                "pid": int(process.info["pid"]),
                "name": process.info.get("name"),
                "exe": executable,
                "createdAt": process.info.get("create_time"),
            })
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, TypeError, ValueError):
            continue
    return processes


def _clean_test_processes(runtime: Any, *, timeout_s: float = 15.0) -> dict[str, Any]:
    browser_cleanup: dict[str, Any]
    try:
        browser_cleanup = dict(runtime.browser_automation.close_managed_browser() or {})
    except Exception as exc:  # noqa: BLE001 - cleanup evidence must preserve the actual failure.
        browser_cleanup = {"closed": False, "error": f"{type(exc).__name__}: {exc}"}
    deadline = time.monotonic() + timeout_s
    terminated: set[int] = set()
    quiet_since: float | None = None
    while time.monotonic() < deadline:
        current = _qqmusic_processes()
        if not current:
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= 4.0:
                break
            time.sleep(0.2)
            continue
        quiet_since = None
        for item in current:
            pid = int(item["pid"])
            try:
                psutil.Process(pid).kill()
                terminated.add(pid)
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.Error):
                continue
        time.sleep(0.25)
    return {
        "browser": browser_cleanup,
        "terminatedQQMusicPids": sorted(terminated),
        "remainingQQMusic": _qqmusic_processes(),
    }


def _metaso_brief(stamp: str, output_relative: str) -> dict[str, Any]:
    return {
        "taskBriefId": f"metaso-image-{stamp}",
        "goal": (
            "使用 V8OS Agent 浏览器打开 https://metaso.cn ，利用该专用 profile 已有登录态，"
            "询问‘请介绍故宫太和殿的建筑特点，并在回答中提供至少一张相关图片。’；"
            f"等待回答完成后下载回答正文中的一张真实内容图片到 {output_relative}，最后关闭 Agent 浏览器。"
        ),
        "context": "这是用户明确授权的真实 Computer Use 联合验收。不得使用 web_broker、HTTP 或外部浏览器替代 Agent 浏览器。",
        "writeSet": [output_relative],
        "expectedOutputs": [output_relative, "Agent Browser closed"],
        "acceptanceContract": [
            "必须在秘塔页面实际提交包含图片的问题。",
            "必须等待回答完成并选择回答正文中的内容图片，不得下载 logo、头像或图标。",
            "图片必须写入唯一批准路径，且 JPEG/PNG magic 有效。",
            "结束时必须关闭全部 Agent Browser 标签与本轮专用浏览器进程。",
        ],
        "constraints": ["不得绕过登录或 CAPTCHA。", "不得读取用户日常浏览器 profile。"],
    }


def _qqmusic_brief(stamp: str) -> dict[str, Any]:
    return {
        "taskBriefId": f"qqmusic-{stamp}",
        "goal": (
            "启动 QQ音乐，使用应用顶部搜索框搜索‘晴天 周杰伦’，点击第一条歌曲结果，"
            "进入播放页后优先使用当前应用已绑定的播放/暂停快捷键，确认歌曲处于播放状态；"
            "最后关闭 QQ音乐窗口并关闭本轮启动的全部 QQMusic 进程。"
        ),
        "context": (
            "这是用户明确授权的真实 Computer Use 联合验收。QQ音乐是自绘界面，测试开始前必须没有 QQMusic 进程。"
            "进入搜索结果后，先定位第一行‘晴天 - 周杰伦’，再点击该行或行内绿色播放控件；"
            "播放页必须先读取快捷键档案并使用 media.play_pause；只有快捷键验证失败后才可移动鼠标显露控件，"
            "不得依赖人工移动鼠标维持控件显示，也不得沿用旧坐标。"
        ),
        "writeSet": [],
        "expectedOutputs": ["搜索结果截图", "播放状态截图", "QQMusic 进程清零"],
        "acceptanceContract": [
            "必须实际输入搜索词‘晴天 周杰伦’并提交。",
            "必须点击第一条歌曲结果并进入播放页。",
            "必须通过 media.play_pause 完成播放动作，并从新鲜截图确认应用状态发生变化。",
            "必须关闭窗口并终止本轮启动的全部 QQMusic 进程。",
        ],
        "constraints": ["不得操作 QQ 聊天应用。", "不得使用 shell 或 RPA。", "每一步只能依据当前新鲜截图。"],
    }


def _qqmusic_shortcut_replay_evidence(*, run_id: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
    from runtimes.computer_use.trace_store import trace_store
    from runtimes.rpa.compiler import rpa_trace_compiler

    verified_episode_actions: list[dict[str, Any]] = []
    coordinate_play_actions: list[dict[str, Any]] = []
    for item in actions:
        if not isinstance(item, dict) or not item.get("ok"):
            continue
        args = dict(item.get("args") or {})
        tool_name = str(item.get("tool") or "")
        if tool_name == "desktop_shortcut" and str(args.get("shortcut_id") or "") == "media.play_pause":
            evidence = dict(item.get("evidence") or {})
            verification = dict(evidence.get("verification") or {})
            if bool(verification.get("passed")) and bool(evidence.get("stateChanged")):
                verified_episode_actions.append(item)
        if tool_name == "desktop_click":
            target = str(args.get("target") or "").lower()
            if any(token in target for token in ("底部播放器", "播放器栏", "bottom player", "player bar")) and any(
                token in target for token in ("播放", "play")
            ):
                coordinate_play_actions.append(item)

    trace = trace_store.get_trace(run_id) or {}
    trace_shortcuts = [
        dict(step)
        for step in list(trace.get("steps") or [])
        if isinstance(step, dict)
        and str(dict(dict(step.get("signals") or {}).get("shortcut") or {}).get("shortcutId") or "")
        == "media.play_pause"
    ]
    draft: dict[str, Any] = {}
    compile_error = ""
    try:
        draft = rpa_trace_compiler.compile_run_to_draft(run_id, save=False)
    except Exception as exc:  # noqa: BLE001 - live evidence must preserve the actual failure.
        compile_error = f"{type(exc).__name__}: {exc}"
    replay_steps = [
        dict(step)
        for step in list(draft.get("steps") or [])
        if isinstance(step, dict)
        and str(dict(step.get("params") or {}).get("shortcut_resolution", {}).get("id") or "")
        == "media.play_pause"
    ]
    replay_ready = any(
        bool(dict(dict(step.get("assessment") or {}).get("signals") or {}).get("shortcutReplayReady"))
        for step in replay_steps
    )
    return {
        "runId": run_id,
        "episodeShortcutVerified": bool(verified_episode_actions),
        "coordinatePlayFallbackUsed": bool(coordinate_play_actions),
        "traceFound": bool(trace),
        "traceSchemaVersion": dict(trace.get("metadata") or {}).get("traceSchemaVersion"),
        "traceShortcutStepCount": len(trace_shortcuts),
        "rpaDraftCompiled": bool(draft),
        "shortcutReplayReady": replay_ready,
        "compileError": compile_error or None,
        "passed": bool(verified_episode_actions)
        and not coordinate_play_actions
        and bool(trace_shortcuts)
        and replay_ready,
    }


def _run_direct_case(
    *,
    runtime: Any,
    execute_task: Any,
    workspace: Path,
    binding: dict[str, str],
    case_id: str,
    stamp: str,
) -> dict[str, Any]:
    if case_id == "metaso":
        output_relative = "computer-use-acceptance/runtime-metaso-image.jpg"
        brief = _metaso_brief(stamp, output_relative)
    else:
        output_relative = ""
        brief = _qqmusic_brief(stamp)
    run_id = f"run-computer-use-direct-{case_id}-{stamp}"
    result = execute_task(
        episode_id=f"episode_direct_{case_id}_{stamp}",
        session_id=f"computer-use-direct-{case_id}-{stamp}",
        run_id=run_id,
        user_id="local-owner",
        project_id=binding["projectId"],
        workspace_id=binding["workspaceId"],
        workspace_path=str(workspace),
        task_brief=brief,
        max_rounds=30,
    )
    evidence = _image_evidence(workspace / output_relative) if output_relative else None
    process_state = _qqmusic_processes() if case_id == "qqmusic" else []
    passed = bool(result.get("ok")) and bool((result.get("verification") or {}).get("passed"))
    if evidence is not None:
        passed = passed and bool(evidence.get("ok"))
    if case_id == "qqmusic":
        shortcut_replay = _qqmusic_shortcut_replay_evidence(
            run_id=run_id,
            actions=[dict(item) for item in list(result.get("actions") or []) if isinstance(item, dict)],
        )
        passed = passed and not process_state and bool(shortcut_replay.get("passed"))
    else:
        shortcut_replay = None
    return {
        "case": case_id,
        "mode": "direct_runtime",
        "passed": passed,
        "status": result.get("status"),
        "summary": result.get("summary"),
        "verification": result.get("verification"),
        "artifactRefs": result.get("artifactRefs"),
        "proofRefs": result.get("proofRefs"),
        "actionCount": len(result.get("actions") or []),
        "actionJournal": str(workspace / ".v8-agent-os" / "artifacts" / "computer-use-episode" / f"episode_direct_{case_id}_{stamp}" / "actions.jsonl"),
        "imageEvidence": evidence,
        "remainingQQMusic": process_state,
        "shortcutReplayEvidence": shortcut_replay,
    }


def _submit_supervisor_case(
    *,
    engine_url: str,
    workspace: Path,
    binding: dict[str, str],
    case_id: str,
    stamp: str,
) -> tuple[str, dict[str, Any]]:
    session_id = f"computer-use-supervisor-{case_id}-{stamp}"
    if case_id == "metaso":
        output_relative = "computer-use-acceptance/supervisor-metaso-image.jpg"
        brief = _metaso_brief(stamp, output_relative)
    else:
        brief = _qqmusic_brief(stamp)
    prompt = (
        "这是明确要求通过桌面操作 runtime 执行的真实 live 验收。你是 Supervisor："
        "请根据以下完整合同调用 runtime_broker 路由 computer_use episode，等待真实 handoff 后再验收；"
        "不要自行用 web_broker、命令、文件工具或文字说明替代执行。\n\n"
        f"Task contract:\n{json.dumps(brief, ensure_ascii=False, indent=2)}"
    )
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": f"{case_id}-{stamp}",
        "stream": False,
        "workspacePath": str(workspace),
        "projectId": binding["projectId"],
        "workspaceId": binding["workspaceId"],
        "scopeMode": "explicit",
        "messages": [{"role": "user", "content": prompt}],
        "data": {
            "conversationId": session_id,
            "clientMessageId": f"{case_id}-{stamp}",
            "projectId": binding["projectId"],
            "workspaceId": binding["workspaceId"],
            "workspacePath": str(workspace),
            "scopeMode": "explicit",
            "computerUseJointLiveAudit": True,
        },
    }
    response = _json_request(f"{engine_url.rstrip('/')}/v1/chat/submit", payload=payload)
    return session_id, response


def _wait_supervisor_case(
    *,
    db: Any,
    workspace: Path,
    session_id: str,
    case_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    episodes: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        episodes = [
            item
            for item in db.list_runtime_episodes(session_id=session_id, limit=20)
            if str(item.get("kind") or item.get("runtimeKind") or "").replace("-", "_") == "computer_use"
        ]
        if episodes and str(episodes[0].get("state") or "").lower() in TERMINAL_EPISODE_STATES:
            break
        time.sleep(1.0)
    episode = episodes[0] if episodes else {}
    episode_id = str(episode.get("episodeId") or episode.get("id") or "")
    handoffs = db.list_runtime_episode_handoffs(episode_id) if episode_id else []
    handoff_payloads = [dict(item.get("payload") or {}) for item in handoffs]
    verification_results = [
        result
        for handoff in handoff_payloads
        for result in list(handoff.get("verificationResults") or [])
        if isinstance(result, dict)
    ]
    task_results = [
        result
        for handoff in handoff_payloads
        for result in list(handoff.get("taskBriefResults") or [])
        if isinstance(result, dict)
    ]
    output = workspace / "computer-use-acceptance" / "supervisor-metaso-image.jpg"
    image_evidence = _image_evidence(output) if case_id == "metaso" else None
    process_state = _qqmusic_processes() if case_id == "qqmusic" else []
    state = str(episode.get("state") or "missing").lower()
    valid_verification = [
        item
        for item in verification_results
        if bool(item.get("passed"))
        and (case_id != "metaso" or bool(item.get("browserClosed")))
        and (case_id != "qqmusic" or bool(item.get("applicationClosed")))
    ]
    task_results_ok = bool(task_results) and all(
        bool(item.get("ok"))
        and str(item.get("status") or "").lower() == "completed"
        and not str(item.get("summary") or "").strip().lower().startswith(("blocked", "阻塞"))
        for item in task_results
    )
    episode_inputs = dict(episode.get("inputs") or {}) if isinstance(episode.get("inputs"), dict) else {}
    episode_workspace = str(
        episode_inputs.get("workspacePath") or episode_inputs.get("workspace_path") or ""
    ).strip()
    try:
        episode_workspace_matches = bool(episode_workspace) and Path(episode_workspace).resolve() == workspace.resolve()
    except (OSError, RuntimeError, ValueError):
        episode_workspace_matches = False
    unexpected_supervisor_tools: list[dict[str, Any]] = []
    for message in db.get_chat_canonical_messages(session_id):
        nodes = list(message.get("nodes") or [])
        if not nodes and str(message.get("nodes_json") or "").strip():
            try:
                nodes = list(json.loads(message["nodes_json"]) or [])
            except (TypeError, ValueError):
                nodes = []
        for node in nodes:
            if not isinstance(node, dict) or node.get("executionType") != "tool_call":
                continue
            tool_name = str(node.get("toolName") or "").strip()
            if tool_name not in {"write_native_file", "run_system_command"}:
                continue
            unexpected_supervisor_tools.append(
                {
                    "toolName": tool_name,
                    "args": dict(node.get("args") or {}) if isinstance(node.get("args"), dict) else {},
                }
            )
    passed = (
        state == "completed"
        and bool(handoffs)
        and bool(valid_verification)
        and task_results_ok
        and episode_workspace_matches
        and not unexpected_supervisor_tools
    )
    if image_evidence is not None:
        passed = passed and bool(image_evidence.get("ok"))
    if case_id == "qqmusic":
        episode_run_id = str(episode.get("runId") or episode.get("run_id") or "").strip()
        task_actions = [
            dict(action)
            for item in task_results
            for action in list(item.get("actions") or [])
            if isinstance(action, dict)
        ]
        shortcut_replay = _qqmusic_shortcut_replay_evidence(
            run_id=episode_run_id,
            actions=task_actions,
        )
        passed = passed and not process_state and bool(shortcut_replay.get("passed"))
    else:
        shortcut_replay = None
    return {
        "case": case_id,
        "mode": "supervisor_live",
        "passed": passed,
        "sessionId": session_id,
        "episodeId": episode_id,
        "episodeState": state,
        "episode": episode,
        "handoffs": handoff_payloads,
        "verificationResults": verification_results,
        "taskBriefResults": task_results,
        "episodeWorkspacePath": episode_workspace,
        "episodeWorkspaceMatches": episode_workspace_matches,
        "unexpectedSupervisorTools": unexpected_supervisor_tools,
        "imageEvidence": image_evidence,
        "remainingQQMusic": process_state,
        "shortcutReplayEvidence": shortcut_replay,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="V8OS Computer Use direct runtime and Supervisor live acceptance.")
    parser.add_argument("--live", action="store_true", help="Required: this harness performs real desktop side effects.")
    parser.add_argument("--phase", choices=["direct", "supervisor", "all"], default="all")
    parser.add_argument("--case", choices=["metaso", "qqmusic", "all"], default="all")
    parser.add_argument("--workspace", required=True, help="Explicit trusted workspace used by the live side effects.")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--max-wait", type=float, default=900.0)
    parser.add_argument("--cleanup-test-processes", action="store_true")
    parser.add_argument("--report-dir", default="")
    args = parser.parse_args()
    if not args.live:
        print("Refusing to run without --live; this harness controls real browser and desktop applications.")
        return 2

    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if str(ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT))
    from core.runtime.startup_profile import get_runtime_registry_state

    _progress("loading runtime registry")
    get_runtime_registry_state()
    from core.database import db
    from runtimes.computer_use.episode_agent import execute_computer_use_task_brief
    from runtimes.computer_use.runtime import computer_use_runtime

    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    _progress(f"resolving trusted workspace: {workspace}")
    binding = _ensure_workspace_binding(workspace)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    cases = ["metaso", "qqmusic"] if args.case == "all" else [args.case]
    results: list[dict[str, Any]] = []
    _progress("cleaning prior Agent Browser and QQMusic processes")
    initial_cleanup = _clean_test_processes(computer_use_runtime) if args.cleanup_test_processes else {}
    _progress(f"initial cleanup complete: remainingQQMusic={len(initial_cleanup.get('remainingQQMusic') or [])}")

    try:
        if args.phase in {"direct", "all"}:
            for case_id in cases:
                if case_id == "qqmusic" and _qqmusic_processes():
                    results.append({"case": case_id, "mode": "direct_runtime", "passed": False, "error": "qqmusic_baseline_not_clean"})
                    continue
                _remove_previous_case_output(workspace, case_id=case_id, mode="direct_runtime")
                try:
                    results.append(
                        _run_direct_case(
                            runtime=computer_use_runtime,
                            execute_task=execute_computer_use_task_brief,
                            workspace=workspace,
                            binding=binding,
                            case_id=case_id,
                            stamp=stamp,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - live audit must preserve the real failure.
                    results.append({
                        "case": case_id,
                        "mode": "direct_runtime",
                        "passed": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
        if args.phase in {"supervisor", "all"}:
            for case_id in cases:
                if case_id == "qqmusic" and _qqmusic_processes():
                    results.append({"case": case_id, "mode": "supervisor_live", "passed": False, "error": "qqmusic_baseline_not_clean"})
                    continue
                _remove_previous_case_output(workspace, case_id=case_id, mode="supervisor_live")
                try:
                    _progress(f"submitting Supervisor case: {case_id}")
                    session_id, response = _submit_supervisor_case(
                        engine_url=args.engine_url,
                        workspace=workspace,
                        binding=binding,
                        case_id=case_id,
                        stamp=stamp,
                    )
                    result = _wait_supervisor_case(
                        db=db,
                        workspace=workspace,
                        session_id=session_id,
                        case_id=case_id,
                        timeout_s=args.max_wait,
                    )
                    result["submitResponse"] = response
                    results.append(result)
                    _progress(
                        f"Supervisor case terminal: {case_id} state={result.get('episodeState')} passed={result.get('passed')}"
                    )
                except Exception as exc:  # noqa: BLE001 - live audit must preserve the real failure.
                    results.append({
                        "case": case_id,
                        "mode": "supervisor_live",
                        "passed": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
    finally:
        _progress("performing final process cleanup")
        final_cleanup = _clean_test_processes(computer_use_runtime) if args.cleanup_test_processes else {}

    report_root = (
        Path(args.report_dir).expanduser().resolve()
        if args.report_dir
        else Path.home() / ".v8-agent-os" / "reports" / "computer_use_joint" / stamp
    )
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / "report.json"
    payload = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "workspace": str(workspace),
        "workspaceBinding": binding,
        "phase": args.phase,
        "cases": cases,
        "initialCleanup": initial_cleanup,
        "results": results,
        "finalCleanup": final_cleanup,
        "passed": bool(results) and all(bool(item.get("passed")) for item in results),
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"passed": payload["passed"], "report": str(report_path), "results": results}, ensure_ascii=False, indent=2, default=str))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
