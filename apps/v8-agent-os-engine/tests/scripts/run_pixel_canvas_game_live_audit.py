from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[2]
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))
DEFAULT_MODEL_FALLBACKS = ["mimo2.5pro", "doubao-seed-2.0-pro", "deepseek-v4-flash"]
TOKEN_RE = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._\-]+|((?:api[_-]?key|token|cookie|authorization)[\"'\s:=]+)[^\"'\s,;]+"
)

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from run_runtime_subagent_closure_live_audit import (  # noqa: E402
    _cancel_active_runs,
    _compact_submit_response,
    _engine_api_base,
    _engine_root_url,
    _json_request,
    _load_durable,
    _redact,
    _session_idle_state,
    _wait_for_engine,
    _wait_for_session_idle,
    LiveCaseResult,
    LiveCaseSpec,
)


@dataclass
class ArtifactCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class PixelGameLiveResult:
    status: str = "pending"
    session_id: str = ""
    run_id: str = ""
    model_profile: str = ""
    output_dir: str = ""
    failure_reason: str | None = None
    submit_response: dict[str, Any] = field(default_factory=dict)
    idle_state: dict[str, Any] = field(default_factory=dict)
    observed_topics: list[str] = field(default_factory=list)
    episode_kinds: list[str] = field(default_factory=list)
    handoff_kinds: list[str] = field(default_factory=list)
    artifact_checks: list[ArtifactCheck] = field(default_factory=list)
    file_inventory: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _safe_session_suffix(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-")[:48] or "model"


def _build_prompt(relative_output_dir: str) -> str:
    return f"""
请在当前工作区完成一个真实项目长 live：创建一个原创像素风横版 run-and-gun Web 游戏项目，气质可以参考经典街机横版射击的节奏，但不要使用 Contra/魂斗罗的名称、角色、地图、音效或任何受版权保护素材；任何交付文件中都不得出现 `Contra` 或 `魂斗罗` 字面，包括“未使用...”这类合规说明。

必须由 V8OS 的 Engineering Runtime 负责交付文件：
- 允许先做简短玩法/视觉/UI/关卡调研与设计归纳；需要外部事实时走 Research Runtime 或可用资料源。
- 需要实际创建文件时走 Engineering Runtime，不要只在 Supervisor 消息里描述。
- 输出目录固定为 `{relative_output_dir}`，不得写到工作区其他无关位置。

交付要求：
1. 全屏 Canvas 游戏，优先纯 HTML/CSS/JavaScript，无构建步骤也能运行；为了可审计验收，代码必须显式包含 `resize` 监听以及 `window.innerWidth` / `window.innerHeight` 或等价全屏视口计算。
2. 必须包含 `index.html`、主要 JS 文件、`README.md`、`DESIGN.md` 或等价设计说明。
3. 核心玩法必须可观察：左右移动、跳跃、射击、敌人、子弹、碰撞、生命/分数 HUD、至少 2 种敌人或 2 个阶段、胜利/失败/重开；为了可审计验收，代码必须显式包含 `keydown` / `keyup` 键盘监听，且必须有命名清楚的 `level` / `wave` / `stage` / `checkpoint` 状态之一。
4. 画面要有像素风：低分辨率内部画布缩放、网格/抖动/像素字体或像素块绘制、横向卷轴或分层背景。
5. README 写清运行方式、操作方式、验收点；DESIGN 写玩法循环、UI、关卡/敌人设计取舍。
6. 验收前请自查：文件存在、Canvas 初始化、requestAnimationFrame 循环、键盘输入、碰撞逻辑、HUD 绘制。

请最终说明实际产物路径、已创建文件和自查结果。
""".strip()


def _repair_guidance(failed_checks: list[str]) -> str:
    guidance: dict[str, str] = {
        "fullscreen_canvas": (
            "把游戏改成真正全屏 Canvas：CSS 使用 100vw/100vh 或等价全屏容器，"
            "JS 使用 resize 监听和 window.innerWidth/window.innerHeight 计算显示尺寸；"
            "保留低分辨率内部画布，但显示层必须铺满视口。"
        ),
        "level_or_wave": (
            "加入显式 level/wave/stage 系统，至少 2 个阶段或波次；"
            "代码中应有可读的 level/wave/stage/checkpoint 状态，并在 README/DESIGN 说明。"
        ),
        "collision_logic": "补齐命名清晰的 collision/collide/intersect/overlap 碰撞函数或等价 AABB 碰撞逻辑。",
        "shooting_mechanics": "补齐射击输入、bullet 数据结构、子弹移动和命中敌人的逻辑。",
        "hud_state": "补齐生命/分数 HUD 绘制，代码中应有 score/life/lives/health/hp 等状态。",
        "restart_or_end_state": "补齐胜利、失败和重开流程，代码中应有 restart/game over/victory/win 等状态。",
        "docs_cover_gameplay_ui_level": "补齐 README/DESIGN，清楚写玩法、UI、关卡或波次设计。",
        "no_contra_branding": "删除所有交付文件中的 `Contra` 和 `魂斗罗` 字面，包括否定式合规说明；改用“未使用受版权保护素材”等泛化表达。"
    }
    lines = []
    for name in failed_checks:
        lines.append(f"- `{name}`: {guidance.get(name, '按验收项补齐对应可运行实现和文档。')}")
    return "\n".join(lines)


def _build_repair_prompt(relative_output_dir: str, failed_checks: list[str]) -> str:
    return f"""
上一轮真实验收没有通过。请继续在同一个输出目录 `{relative_output_dir}` 内修复，不要新建其他目录，不要只回复文字。

必须仍由 Engineering Runtime 修改文件，并保留已有可运行项目结构。

失败验收项：
{_repair_guidance(failed_checks)}

修复后请自查：
1. `index.html`、主 JS、`README.md`、`DESIGN.md` 仍存在。
2. 游戏仍可直接打开 `index.html` 运行。
3. Canvas 真正铺满视口，同时保持像素风低分辨率内部渲染。
4. 至少 2 个显式 level/wave/stage/checkpoint 阶段可在代码和文档中看到。
5. 键盘控制仍必须保留 `keydown` / `keyup` 监听，射击、碰撞、HUD、胜利/失败/重开不得因为修复而回退。
6. 任何交付文件中都不得出现 `Contra` 或 `魂斗罗` 字面，包括“未使用...”这类合规说明；只能写“未使用受版权保护素材”等泛化表达。

请最终说明修改了哪些文件和自查结果。
""".strip()


def _submit_message(
    engine_url: str,
    *,
    session_id: str,
    prompt: str,
    workspace: str,
    model_profile: str,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": f"pixel-canvas-game-{int(time.time() * 1000)}",
        "stream": False,
        "workspacePath": workspace,
        "messages": [{"role": "user", "content": prompt}],
        "data": {
            "conversationId": session_id,
            "modelProfile": model_profile,
        },
    }
    response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    run_id = str(response.get("run_id") or response.get("runId") or "").strip()
    return run_id, response


def _collect_runtime_summary(result: PixelGameLiveResult) -> None:
    bridge_case = LiveCaseSpec(case_id="pixel_canvas_game", title="pixel canvas game", prompt="")
    bridge_result = LiveCaseResult(
        spec=bridge_case,
        session_id=result.session_id,
        run_id=result.run_id,
        model_profile=result.model_profile,
    )
    events, episodes, handoffs, error = _load_durable(bridge_result)
    if error:
        result.notes.append(f"durable lookup error: {error}")
    for event in events:
        topic = str(event.get("topic") or event.get("type") or event.get("event_type") or "").strip()
        if topic and topic not in result.observed_topics:
            result.observed_topics.append(topic)
    for episode in episodes:
        kind = str(episode.get("kind") or episode.get("episodeKind") or episode.get("runtimeKind") or "").strip()
        if kind and kind not in result.episode_kinds:
            result.episode_kinds.append(kind)
    for handoff in handoffs:
        kind = str(handoff.get("handoffKind") or handoff.get("kind") or "").strip()
        if kind and kind not in result.handoff_kinds:
            result.handoff_kinds.append(kind)


def _read_text(path: Path, *, max_chars: int = 250_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""


def _find_output_dir(workspace: Path, expected: Path) -> Path | None:
    if expected.exists():
        return expected
    root = expected.parent if expected.parent.exists() else workspace / ".v8" / "live-audit" / "pixel-run-gun"
    if not root.exists():
        return None
    candidates = [item for item in root.rglob("*") if item.is_dir() and (item / "index.html").exists()]
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _validate_artifacts(workspace: Path, expected_output_dir: Path) -> tuple[Path | None, list[ArtifactCheck], list[str]]:
    output_dir = _find_output_dir(workspace, expected_output_dir)
    checks: list[ArtifactCheck] = []
    inventory: list[str] = []
    if output_dir is None:
        return None, [ArtifactCheck("output_dir_exists", False, str(expected_output_dir))], inventory

    files = [item for item in output_dir.rglob("*") if item.is_file()]
    inventory = [str(item.relative_to(output_dir)).replace("\\", "/") for item in files]
    lower_inventory = {item.lower() for item in inventory}
    index_path = output_dir / "index.html"
    js_files = [item for item in files if item.suffix.lower() == ".js"]
    md_files = [item for item in files if item.suffix.lower() in {".md", ".markdown"}]
    combined_html = _read_text(index_path)
    combined_js = "\n".join(_read_text(item) for item in js_files)
    combined_docs = "\n".join(_read_text(item) for item in md_files)
    combined_code = f"{combined_html}\n{combined_js}"

    def add(name: str, condition: bool, detail: str = "") -> None:
        checks.append(ArtifactCheck(name, bool(condition), detail))

    add("index_html_exists", index_path.exists(), str(index_path))
    add("main_js_exists", bool(js_files) or "<script" in combined_html.lower(), ", ".join(item.name for item in js_files))
    add("readme_exists", any(item in lower_inventory for item in {"readme.md", "readme.markdown"}), ", ".join(inventory))
    add("design_doc_exists", any("design" in item.lower() and item.lower().endswith((".md", ".markdown")) for item in inventory), ", ".join(inventory))
    add("canvas_present", "<canvas" in combined_html.lower() or "createelement('canvas'" in combined_code.lower(), "canvas tag or creation")
    add("fullscreen_canvas", any(token in combined_code.lower() for token in ["innerwidth", "innerheight", "fullscreen", "resize"]), "resize/fullscreen token")
    add("animation_loop", "requestanimationframe" in combined_code.lower(), "requestAnimationFrame")
    add("keyboard_controls", any(token in combined_code.lower() for token in ["keydown", "keyup", "keyboardevent"]), "keyboard listeners")
    add("shooting_mechanics", all(token in combined_code.lower() for token in ["bullet", "shoot"]), "bullet + shoot")
    add("enemy_mechanics", "enemy" in combined_code.lower() or "enemies" in combined_code.lower(), "enemy token")
    add("collision_logic", any(token in combined_code.lower() for token in ["collision", "collide", "intersect", "overlap"]), "collision token")
    add("hud_state", any(token in combined_code.lower() for token in ["score", "life", "lives", "health", "hp"]), "score/life/hp token")
    add("level_or_wave", any(token in combined_code.lower() for token in ["level", "wave", "stage", "checkpoint"]), "level/wave/stage token")
    add("restart_or_end_state", any(token in combined_code.lower() for token in ["restart", "game over", "victory", "win"]), "restart/end token")
    add("docs_cover_gameplay_ui_level", all(token in combined_docs.lower() for token in ["玩法", "ui", "关卡"]) or all(token in combined_docs.lower() for token in ["gameplay", "ui", "level"]), "README/DESIGN content")
    add("no_contra_branding", "contra" not in combined_code.lower() and "魂斗罗" not in combined_code and "contra" not in combined_docs.lower() and "魂斗罗" not in combined_docs, "original IP check")
    return output_dir, checks, inventory


def _write_report(report_dir: Path, result: PixelGameLiveResult) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "status": result.status,
        "sessionId": result.session_id,
        "runId": result.run_id,
        "modelProfile": result.model_profile,
        "outputDir": result.output_dir,
        "failureReason": result.failure_reason,
        "submitResponse": _compact_submit_response(result.submit_response),
        "idleState": result.idle_state,
        "observedTopics": result.observed_topics,
        "episodeKinds": result.episode_kinds,
        "handoffKinds": result.handoff_kinds,
        "artifactChecks": [check.__dict__ for check in result.artifact_checks],
        "fileInventory": result.file_inventory,
        "notes": result.notes,
    }
    (report_dir / "pixel_canvas_game_live_result.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    passed = sum(1 for check in result.artifact_checks if check.passed)
    total = len(result.artifact_checks)
    lines = [
        "# Pixel Canvas Game Live Audit",
        "",
        f"- Status: {result.status}",
        f"- Model: {result.model_profile}",
        f"- Session: `{result.session_id}`",
        f"- Run: `{result.run_id}`",
        f"- Output: `{result.output_dir}`",
        f"- Artifact checks: {passed}/{total}",
        f"- Failure: {result.failure_reason or '-'}",
        "",
        "## Runtime",
        "",
        f"- Episodes: {', '.join(result.episode_kinds) or '-'}",
        f"- Handoffs: {', '.join(result.handoff_kinds) or '-'}",
        f"- Key topics: {', '.join(result.observed_topics[:24])}",
        "",
        "## Artifact Checks",
        "",
        "| Check | Status | Detail |",
        "|---|---:|---|",
    ]
    for check in result.artifact_checks:
        lines.append(f"| {check.name} | {'PASS' if check.passed else 'FAIL'} | {_redact(check.detail)} |")
    lines.extend(["", "## Files", "", *[f"- `{item}`" for item in result.file_inventory[:80]]])
    (report_dir / "PIXEL_CANVAS_GAME_LIVE_AUDIT_ZH.md").write_text("\n".join(lines), encoding="utf-8")


def _run_live(
    *,
    engine_url: str,
    workspace: Path,
    model_profile: str,
    timestamp: str,
    max_wait: int,
    repair_attempts: int,
) -> PixelGameLiveResult:
    relative_output = f".v8/live-audit/pixel-run-gun/{timestamp}-{_safe_session_suffix(model_profile)}"
    expected_output = workspace / relative_output
    session_id = f"pixel-canvas-game-live-{timestamp}-{_safe_session_suffix(model_profile)}"
    prompt = _build_prompt(relative_output)
    result = PixelGameLiveResult(session_id=session_id, model_profile=model_profile, output_dir=str(expected_output))

    run_id, submit_response = _submit_message(
        engine_url,
        session_id=session_id,
        prompt=prompt,
        workspace=str(workspace),
        model_profile=model_profile,
    )
    result.run_id = run_id
    result.submit_response = submit_response
    if not run_id:
        result.status = "failed"
        result.failure_reason = "submit_missing_run_id"
        return result

    attempts_remaining = max(0, repair_attempts)
    attempt_index = 0
    while True:
        idle, idle_state = _wait_for_session_idle(session_id, timeout=max_wait)
        result.idle_state = idle_state
        if not idle:
            cleanup = _cancel_active_runs(engine_url, session_id, idle_state, reason="pixel_canvas_game_live_audit_timeout_cleanup")
            result.notes.append(_redact({"timeoutCleanup": cleanup}))
            result.status = "failed"
            result.failure_reason = "run_did_not_reach_idle"
            _collect_runtime_summary(result)
            return result

        _collect_runtime_summary(result)

        output_dir, checks, inventory = _validate_artifacts(workspace, expected_output)
        if output_dir is not None:
            result.output_dir = str(output_dir)
        result.artifact_checks = checks
        result.file_inventory = inventory

        failed_checks = [check.name for check in checks if not check.passed]
        if "waiting_approval" in json.dumps(idle_state, ensure_ascii=False).lower():
            result.status = "failed"
            result.failure_reason = "run_waiting_approval"
            return result
        if not any(kind in {"engineering", "research"} for kind in result.episode_kinds):
            result.status = "failed"
            result.failure_reason = "missing_runtime_episode"
            return result
        if not failed_checks:
            result.status = "passed"
            result.failure_reason = None
            return result

        result.status = "failed"
        result.failure_reason = f"artifact_checks_failed: {', '.join(failed_checks[:8])}"
        if attempts_remaining <= 0 or output_dir is None:
            return result

        attempt_index += 1
        attempts_remaining -= 1
        result.notes.append(
            _redact(
                {
                    "repairAttempt": attempt_index,
                    "previousRunId": result.run_id,
                    "failedChecks": failed_checks,
                }
            )
        )
        repair_prompt = _build_repair_prompt(relative_output, failed_checks)
        repair_run_id, repair_response = _submit_message(
            engine_url,
            session_id=session_id,
            prompt=repair_prompt,
            workspace=str(workspace),
            model_profile=model_profile,
        )
        if not repair_run_id:
            result.status = "failed"
            result.failure_reason = "repair_submit_missing_run_id"
            result.notes.append(_redact({"repairSubmitResponse": repair_response}))
            return result
        result.run_id = repair_run_id
        result.submit_response = repair_response


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real long live audit that asks V8OS to build a pixel canvas web game.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", default="E:/Projects/test3")
    parser.add_argument("--model-fallbacks", default=",".join(DEFAULT_MODEL_FALLBACKS))
    parser.add_argument("--max-wait", type=int, default=1200)
    parser.add_argument("--repair-attempts", type=int, default=1)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    timestamp = _now_slug()
    model_fallbacks = [item.strip() for item in str(args.model_fallbacks or "").split(",") if item.strip()]
    if not args.live:
        print(
            json.dumps(
                {
                    "live": False,
                    "workspace": str(workspace),
                    "modelFallbacks": model_fallbacks,
                    "promptPreview": _build_prompt(".v8/live-audit/pixel-run-gun/<timestamp>")[:800],
                    "acceptance": [
                        "runtime episode/handoff present",
                        "index.html/js/docs exist",
                        "fullscreen canvas, RAF loop, controls, shooting, enemies, collision, HUD, levels, restart",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    ok, error = _wait_for_engine(args.engine_url)
    if not ok:
        print(f"[pixel-canvas-live] Engine unavailable: {error}", file=sys.stderr)
        return 2
    if not workspace.exists():
        print(f"[pixel-canvas-live] workspace missing: {workspace}", file=sys.stderr)
        return 2

    report_root = Path(args.output_dir).expanduser().resolve() if args.output_dir else DEFAULT_REPORT_ROOT / "pixel_canvas_game" / timestamp
    final_result: PixelGameLiveResult | None = None
    for model_profile in model_fallbacks:
        print(f"[pixel-canvas-live] using {model_profile}")
        result = _run_live(
            engine_url=args.engine_url,
            workspace=workspace,
            model_profile=model_profile,
            timestamp=timestamp,
            max_wait=args.max_wait,
            repair_attempts=args.repair_attempts,
        )
        final_result = result
        model_report_dir = report_root / _safe_session_suffix(model_profile)
        _write_report(model_report_dir, result)
        print(f"[pixel-canvas-live] report: {model_report_dir / 'PIXEL_CANVAS_GAME_LIVE_AUDIT_ZH.md'}")
        if result.status == "passed":
            break
        reason = str(result.failure_reason or "").lower()
        retryable = any(
            token in reason
            for token in [
                "quota",
                "rate",
                "provider",
                "connection",
                "approval",
                "timeout",
                "artifact_checks_failed",
                "output_dir_exists",
                "missing_runtime_episode",
            ]
        )
        if not retryable:
            break

    assert final_result is not None
    if args.write_report:
        _write_report(report_root, final_result)
        print(f"[pixel-canvas-live] final report: {report_root / 'PIXEL_CANVAS_GAME_LIVE_AUDIT_ZH.md'}")
    print(
        json.dumps(
            {
                "status": final_result.status,
                "modelProfile": final_result.model_profile,
                "runId": final_result.run_id,
                "outputDir": final_result.output_dir,
                "failure": final_result.failure_reason,
                "artifactChecks": {check.name: check.passed for check in final_result.artifact_checks},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if final_result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
