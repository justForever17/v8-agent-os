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


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[2]
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
DEFAULT_WORKSPACE = Path("E:/Projects/test3")
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))
TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "canceled"}
ACTIVE_QUEUE_STATES = ["pending", "promoted", "queued"]

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


@dataclass
class StageObservation:
    stage: str
    found: bool = False
    approved: bool = False
    path: str = ""
    content_chars: int = 0
    quality_findings: list[str] = field(default_factory=list)


@dataclass
class SpecLiveResult:
    status: str = "pending"
    session_id: str = ""
    run_ids: list[str] = field(default_factory=list)
    spec_id: str = ""
    spec_dir: str = ""
    marker: str = ""
    target_dir: str = ""
    submit_latencies_ms: list[int] = field(default_factory=list)
    stages: list[StageObservation] = field(default_factory=list)
    episode_kinds: list[str] = field(default_factory=list)
    handoff_kinds: list[str] = field(default_factory=list)
    output_files: dict[str, dict[str, Any]] = field(default_factory=dict)
    findings: list[dict[str, str]] = field(default_factory=list)
    key_events: list[dict[str, Any]] = field(default_factory=list)


def _json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def _engine_api_base(engine_url: str) -> str:
    base = engine_url.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def _engine_root_url(engine_url: str) -> str:
    base = engine_url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _wait_for_engine(engine_url: str, timeout_s: float = 20.0) -> tuple[bool, str]:
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            _json_request(f"{_engine_root_url(engine_url)}/health", timeout=3)
            return True, ""
        except Exception as exc:  # noqa: BLE001 - live diagnostics preserve connectivity error.
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.75)
    return False, last_error


def _submit(
    engine_url: str,
    *,
    session_id: str,
    workspace: Path,
    model_profile: str,
    prompt: str,
    spec_id: str = "",
    client_tag: str,
) -> tuple[str, int, dict[str, Any]]:
    data: dict[str, Any] = {
        "conversationId": session_id,
        "clientMessageId": client_tag,
        "modelProfile": model_profile,
        "specMode": True,
        "taskPlanningMode": False,
        "plannerMode": "off",
        "plannerDispatchMode": "suggest",
    }
    if spec_id:
        data["specId"] = spec_id
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": client_tag,
        "stream": False,
        "workspacePath": str(workspace),
        "messages": [{"role": "user", "content": prompt}],
        "data": data,
    }
    started = time.perf_counter()
    response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    latency_ms = int((time.perf_counter() - started) * 1000)
    run_id = str(response.get("run_id") or response.get("runId") or "")
    return run_id, latency_ms, response


def _session_idle_state(session_id: str) -> tuple[bool, dict[str, Any]]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}
    try:
        runs = db.list_run_records(session_id=session_id, run_type="chat", limit=20)
        active_runs = [
            {"id": item.get("id"), "status": item.get("status")}
            for item in runs
            if str(item.get("status") or "").strip().lower() not in TERMINAL_RUN_STATES
        ]
        queue_items = db.list_chat_user_message_queue(session_id=session_id, states=ACTIVE_QUEUE_STATES, limit=20)
        active_queue = [{"id": item.get("id"), "state": item.get("state")} for item in queue_items]
        return not active_runs and not active_queue, {"activeRuns": active_runs, "activeQueue": active_queue}
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}


def _wait_for_idle(session_id: str, *, timeout_s: int) -> tuple[bool, dict[str, Any]]:
    deadline = time.time() + max(5, timeout_s)
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        idle, state = _session_idle_state(session_id)
        last_state = state
        if idle:
            return True, state
        time.sleep(2)
    return False, last_state


def _find_spec_by_marker_or_target(workspace: Path, marker: str, target_rel: str, *, spec_id: str = "") -> dict[str, Any] | None:
    root = workspace / ".v8" / "specs"
    if not root.exists():
        return None
    newest: tuple[float, dict[str, Any]] | None = None
    normalized_target = target_rel.replace("\\", "/").lower()
    expected_spec_id = str(spec_id or "").strip()
    for manifest_path in root.glob("*/spec.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if expected_spec_id and str(manifest.get("specId") or "").strip() != expected_spec_id:
            continue
        haystack = json.dumps(manifest, ensure_ascii=False)
        for doc_path in manifest_path.parent.glob("*.md"):
            try:
                haystack += "\n" + doc_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
        normalized_haystack = haystack.replace("\\", "/").lower()
        if marker not in haystack and normalized_target not in normalized_haystack:
            continue
        mtime = manifest_path.stat().st_mtime
        payload = {"manifest": manifest, "manifestPath": str(manifest_path), "specDir": str(manifest_path.parent)}
        if newest is None or mtime > newest[0]:
            newest = (mtime, payload)
    return newest[1] if newest else None


def _wait_for_stage(workspace: Path, marker: str, target_rel: str, stage: str, *, timeout_s: int, spec_id: str = "") -> dict[str, Any] | None:
    deadline = time.time() + max(5, timeout_s)
    while time.time() < deadline:
        found = _find_spec_by_marker_or_target(workspace, marker, target_rel, spec_id=spec_id)
        if found:
            manifest = found["manifest"]
            doc = (manifest.get("documents") or {}).get(stage)
            if isinstance(doc, dict) and doc.get("relativePath"):
                return found
        time.sleep(2)
    return None


def _read_stage(workspace: Path, manifest: dict[str, Any], stage: str) -> tuple[Path | None, str]:
    doc = (manifest.get("documents") or {}).get(stage)
    if not isinstance(doc, dict):
        return None, ""
    rel = str(doc.get("relativePath") or "").strip()
    path = workspace / rel if rel else Path(str(doc.get("path") or ""))
    if not path.exists():
        return path, ""
    return path, path.read_text(encoding="utf-8", errors="ignore")


def _stage_quality(stage: str, content: str, *, marker: str, target_rel: str) -> list[str]:
    lower = content.lower()
    findings: list[str] = []
    if marker not in content:
        findings.append("marker_missing")
    if target_rel.replace("\\", "/").lower() not in lower.replace("\\", "/"):
        findings.append("target_path_missing")
    if stage == "requirements":
        if not any(token in lower for token in ("req-", "fr-", "nfr-", "bfix-")):
            findings.append("requirement_ids_missing")
        if "验收" not in content and "acceptance" not in lower and "shall" not in lower:
            findings.append("acceptance_criteria_missing")
        if "index.html" not in lower or "readme" not in lower:
            findings.append("deliverable_files_missing")
    elif stage == "design":
        if "index.html" not in lower or "readme" not in lower:
            findings.append("design_files_missing")
        if "验证" not in content and "verification" not in lower:
            findings.append("verification_strategy_missing")
    elif stage == "tasks":
        if "task-" not in lower and "tsk-" not in lower:
            findings.append("task_ids_missing")
        if not any(token in lower for token in ("req-", "fr-", "nfr-", "bfix-")):
            findings.append("task_requirement_links_missing")
    return findings


def _approve_stage(
    engine_url: str,
    *,
    session_id: str,
    spec_id: str,
    stage: str,
    comment: str,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001 - live diagnostics preserve storage errors.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    normalized_stage = str(stage or "").strip().lower()
    deadline = time.time() + max(1.0, timeout_s)
    last_error = ""
    while time.time() < deadline:
        try:
            pending = db.list_pending_approvals(session_id=session_id, status="pending")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        for approval in pending:
            if str(approval.get("approval_kind") or "").strip() != "spec_stage_approval":
                continue
            request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
            if str(request.get("specId") or "").strip() != spec_id:
                continue
            if str(request.get("stage") or "").strip().lower() != normalized_stage:
                continue
            approval_id = str(approval.get("id") or approval.get("approval_id") or "").strip()
            if not approval_id:
                continue
            response = _json_request(
                f"{_engine_api_base(engine_url)}/approvals/{approval_id}/approve",
                method="POST",
                payload={
                    "reason": comment,
                    "response": {
                        "decision": "approved",
                        "source": "spec_mode_project_live_audit",
                        "specId": spec_id,
                        "stage": normalized_stage,
                        "comment": comment,
                    },
                },
                timeout=12,
            )
            return {
                "ok": True,
                "approvalId": approval_id,
                "stage": normalized_stage,
                "response": response,
                "resumeScheduled": bool(response.get("resume_scheduled")) if isinstance(response, dict) else False,
            }
        last_error = "pending_spec_stage_approval_not_found"
        time.sleep(1.5)
    return {"ok": False, "error": last_error or "pending_spec_stage_approval_not_found", "specId": spec_id, "stage": normalized_stage}


def _collect_durable(result: SpecLiveResult) -> None:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        result.key_events.append({"durableLookupError": f"{type(exc).__name__}: {exc}"})
        return
    episodes: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    try:
        episodes.extend(db.list_runtime_episodes(session_id=result.session_id, limit=200))
        for run_id in result.run_ids:
            episodes.extend(db.list_runtime_episodes(run_id=run_id, limit=200))
        seen: set[str] = set()
        for episode in episodes:
            episode_id = str(episode.get("episodeId") or episode.get("id") or episode.get("needId") or "")
            if not episode_id or episode_id in seen:
                continue
            seen.add(episode_id)
            kind = str(episode.get("kind") or episode.get("runtimeKind") or "").strip()
            if kind and kind not in result.episode_kinds:
                result.episode_kinds.append(kind)
            handoffs.extend(db.list_runtime_episode_handoffs(episode_id))
        for row in handoffs:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
            kind = str((payload or {}).get("kind") or "").strip()
            if kind and kind not in result.handoff_kinds:
                result.handoff_kinds.append(kind)
    except Exception as exc:  # noqa: BLE001
        result.key_events.append({"durableLookupError": f"{type(exc).__name__}: {exc}"})


def _validate_outputs(result: SpecLiveResult) -> None:
    target = Path(result.target_dir)
    for rel in ("index.html", "README.md"):
        path = target / rel
        exists = path.exists()
        content = path.read_text(encoding="utf-8", errors="ignore") if exists else ""
        result.output_files[rel] = {
            "exists": exists,
            "chars": len(content),
            "containsMarker": result.marker in content,
            "path": str(path),
        }
        if not exists:
            result.findings.append({"severity": "P0", "code": f"{rel}_missing", "summary": f"缺少交付文件 {rel}"})
        elif result.marker not in content:
            result.findings.append({"severity": "P1", "code": f"{rel}_marker_missing", "summary": f"{rel} 未包含 live marker，可能不是本轮产物"})
    index_text = (target / "index.html").read_text(encoding="utf-8", errors="ignore") if (target / "index.html").exists() else ""
    if index_text and "spec mode live counter" not in index_text.lower():
        result.findings.append({"severity": "P1", "code": "counter_title_missing", "summary": "index.html 未体现 Spec Mode Live Counter 交付目标"})
    if index_text and re.search(r"live audit|审计面板|audit dashboard", index_text, re.IGNORECASE):
        result.findings.append({"severity": "P1", "code": "audit_dashboard_drift", "summary": "index.html 被带偏成审计/报告页面，而不是计数器项目"})
    if index_text and not re.search(r"<button|onclick|addEventListener", index_text, re.IGNORECASE):
        result.findings.append({"severity": "P1", "code": "interactive_quality_missing", "summary": "index.html 缺少可判断的交互按钮/事件"})


def _write_report(result: SpecLiveResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "spec_mode_project_live_result.json"
    json_path.write_text(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=lambda value: value.__dict__), encoding="utf-8")
    lines = [
        "# Spec Mode Project Live Audit",
        "",
        f"- Status: {result.status}",
        f"- Session: `{result.session_id}`",
        f"- Spec: `{result.spec_id}`",
        f"- Target: `{result.target_dir}`",
        "",
        "## Stages",
        "",
        "| Stage | Found | Approved | Chars | Findings |",
        "|---|---:|---:|---:|---|",
    ]
    for stage in result.stages:
        lines.append(f"| {stage.stage} | {stage.found} | {stage.approved} | {stage.content_chars} | {', '.join(stage.quality_findings) or '-'} |")
    lines.extend(
        [
            "",
            "## Runtime",
            "",
            f"- Episode kinds: {', '.join(result.episode_kinds) or '-'}",
            f"- Handoff kinds: {', '.join(result.handoff_kinds) or '-'}",
            "",
            "## Findings",
            "",
        ]
    )
    if result.findings:
        for finding in result.findings:
            lines.append(f"- [{finding.get('severity')}] {finding.get('code')}: {finding.get('summary')}")
    else:
        lines.append("- none")
    lines.extend(["", f"Raw JSON: `{json_path}`", ""])
    md_path = output_dir / "SPEC_MODE_PROJECT_LIVE_AUDIT_ZH.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def _run(args: argparse.Namespace) -> SpecLiveResult:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    workspace = Path(args.workspace).resolve()
    marker = f"SPEC_LIVE_{timestamp}"
    target_rel = f".v8/live-audit/spec-mode-v2/{timestamp}"
    target_dir = workspace / target_rel
    session_id = f"spec-mode-project-live-{timestamp}"
    result = SpecLiveResult(session_id=session_id, marker=marker, target_dir=str(target_dir))

    prompt = (
        "开启 Spec Mode。请为当前工作区创建一个很小但可验收的静态项目，先只写 requirements.md 等待审批。\n"
        f"Live marker: {marker}\n"
        f"目标输出目录：{target_rel}\n"
        "最终交付文件必须是：index.html 和 README.md。\n"
        "为了 live 验收，requirements/design/tasks 与最终 index.html/README.md 都必须包含 Live marker 原文。\n"
        "index.html 需要展示标题“Spec Mode Live Counter”，包含一个按钮，点击后页面计数 +1；README.md 说明如何打开和验收。\n"
        "需求 ID 必须使用 REQ-001、REQ-002 这种稳定格式，非功能要求也用 REQ-### 表达，方便后续执行引用。\n"
        "请调用真实 spec_broker tool 写入完整 Markdown 文档，content 参数必须包含可审批的需求规格，不要只生成空泛模板。\n"
        "禁止把 write_native_file、run_system_command 或 DSML/XML 伪工具块写在正文里；如果无法调用真实 spec_broker，请明确 recoverable_failed。"
    )
    run_id, latency, response = _submit(
        args.engine_url,
        session_id=session_id,
        workspace=workspace,
        model_profile=args.model_profile,
        prompt=prompt,
        client_tag=f"{marker}-requirements",
    )
    if run_id:
        result.run_ids.append(run_id)
    result.submit_latencies_ms.append(latency)
    result.key_events.append({"requirementsSubmit": {"latencyMs": latency, "accepted": response.get("accepted"), "runId": run_id}})
    _wait_for_idle(session_id, timeout_s=max(30, args.max_wait // 2))

    stage_payload = _wait_for_stage(workspace, marker, target_rel, "requirements", timeout_s=args.max_wait)
    if not stage_payload:
        result.findings.append({"severity": "P0", "code": "requirements_missing", "summary": "等待窗口内未生成 requirements.md"})
        result.status = "failed"
        return result
    manifest = stage_payload["manifest"]
    result.spec_id = str(manifest.get("specId") or "")
    result.spec_dir = str(stage_payload["specDir"])

    for stage in ("requirements",):
        path, content = _read_stage(workspace, manifest, stage)
        quality = _stage_quality(stage, content, marker=marker, target_rel=target_rel)
        result.stages.append(StageObservation(stage=stage, found=bool(content), path=str(path or ""), content_chars=len(content), quality_findings=quality))
        for item in quality:
            result.findings.append({"severity": "P1", "code": f"{stage}_{item}", "summary": f"{stage} 文档质量缺口：{item}"})
    approved = _approve_stage(
        args.engine_url,
        session_id=session_id,
        spec_id=result.spec_id,
        stage="requirements",
        comment="live audit approved requirements",
    )
    result.stages[-1].approved = bool(approved.get("ok"))
    result.key_events.append({"requirementsApproval": approved})
    if not approved.get("ok"):
        result.findings.append({"severity": "P0", "code": "requirements_approval_failed", "summary": str(approved.get("error") or "requirements approval failed")})
        result.status = "failed"
        return result

    for stage in ("design", "tasks"):
        stage_payload = _wait_for_stage(workspace, marker, target_rel, stage, timeout_s=args.max_wait, spec_id=result.spec_id)
        if not stage_payload:
            result.findings.append({"severity": "P0", "code": f"{stage}_missing", "summary": f"等待窗口内未生成 {stage}.md"})
            result.status = "failed"
            return result
        manifest = stage_payload["manifest"]
        path, content = _read_stage(workspace, manifest, stage)
        quality = _stage_quality(stage, content, marker=marker, target_rel=target_rel)
        observation = StageObservation(stage=stage, found=bool(content), path=str(path or ""), content_chars=len(content), quality_findings=quality)
        result.stages.append(observation)
        for item in quality:
            result.findings.append({"severity": "P1", "code": f"{stage}_{item}", "summary": f"{stage} 文档质量缺口：{item}"})
        approved = _approve_stage(
            args.engine_url,
            session_id=session_id,
            spec_id=result.spec_id,
            stage=stage,
            comment=f"live audit approved {stage}",
        )
        observation.approved = bool(approved.get("ok"))
        result.key_events.append({f"{stage}Approval": approved})
        if not approved.get("ok"):
            result.findings.append({"severity": "P0", "code": f"{stage}_approval_failed", "summary": str(approved.get("error") or f"{stage} approval failed")})
            result.status = "failed"
            return result

    _wait_for_idle(session_id, timeout_s=args.max_wait)
    time.sleep(2)
    _collect_durable(result)
    _validate_outputs(result)
    if "engineering" not in result.episode_kinds and not any("engineering" in item.lower() for item in result.handoff_kinds):
        result.findings.append({"severity": "P1", "code": "engineering_episode_missing", "summary": "执行阶段未观察到 Engineering episode/handoff"})
    result.status = "failed" if any(item.get("severity") == "P0" for item in result.findings) else ("degraded" if result.findings else "passed")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live Spec Mode project delivery audit.")
    parser.add_argument("--live", action="store_true", help="Submit real live chat runs.")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--model-profile", default="deepseek-v4-flash")
    parser.add_argument("--max-wait", type=int, default=240)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"live": False, "workspace": args.workspace, "modelProfile": args.model_profile}, ensure_ascii=False, indent=2))
        return 0
    ok, error = _wait_for_engine(args.engine_url)
    if not ok:
        print(f"[spec-mode-live] Engine unavailable: {error}", file=sys.stderr)
        return 2
    result = _run(args)
    if args.write_report:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_REPORT_ROOT / "spec_mode_project" / timestamp
        report_path = _write_report(result, output_dir)
        print(f"[spec-mode-live] report: {report_path}")
    print(json.dumps({"status": result.status, "sessionId": result.session_id, "specId": result.spec_id, "findings": result.findings}, ensure_ascii=False, indent=2))
    return 0 if result.status in {"passed", "degraded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
