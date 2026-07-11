from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_supervisor_runtime_skill_live_audit import (
    LiveCaseSpec,
    _default_model_profile_label,
    _poll_case,
    _submit_case,
    _wait_for_engine,
)


DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
DEFAULT_WORKSPACE = Path(r"E:\Projects\test1")
REPORT_ROOT = Path.home() / ".v8-agent-os" / "reports" / "creative_media_engineering_joint"


def _workspace_snapshot(workspace: Path) -> dict[str, int]:
    snapshot: dict[str, int] = {}
    for path in workspace.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            snapshot[path.relative_to(workspace).as_posix()] = path.stat().st_mtime_ns
        except OSError:
            continue
    return snapshot


def _changed_files(before: dict[str, int], after: dict[str, int]) -> list[str]:
    return sorted(path for path, stamp in after.items() if before.get(path) != stamp)


def _skill_reference(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    skill_root = workspace / ".agents" / "skills" / "sanyueqi-perspective"
    reference = {
        "id": "sanyueqi-perspective",
        "name": "sanyueqi-perspective",
        "description": "三月七视角与表达风格，用于人物化说明和展示内容。",
        "path": str(skill_root),
        "sourceType": "scoped_workspace",
    }
    mention = {
        "kind": "skill",
        "id": reference["id"],
        "name": reference["name"],
        "label": reference["name"],
        "description": reference["description"],
        "path": reference["path"],
        "sourceType": reference["sourceType"],
    }
    return [reference], [mention]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real Engineering + Creative Media Supervisor live audit.")
    parser.add_argument("--live", action="store_true", help="Required before calling live models and media providers.")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--max-wait", type=float, default=1800.0)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    skill_refs, context_mentions = _skill_reference(workspace)
    if not args.live:
        print("Dry run only. Pass --live to submit the joint task.")
        print(f"Workspace: {workspace}")
        print(f"Skill: {skill_refs[0]['path']}")
        return 0
    if not workspace.exists() or not Path(skill_refs[0]["path"]).exists():
        raise SystemExit("Workspace or sanyueqi-perspective skill is missing.")
    ok, error = _wait_for_engine(args.engine_url)
    if not ok:
        raise SystemExit(f"Engine unavailable: {error}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prompt = (
        "在当前 test1 工作区完成一个真实联合交付。先完整读取并遵循已选择的 "
        "sanyueqi-perspective skill。请让 Engineering Runtime 负责创建一个可直接打开的 V8 Agent OS "
        "展示 HTML 页面和素材集成，让 Creative Media Runtime 使用 Agnes 图像模型、Agnes 视频模型和 "
        "MiniMax voice.design/voice.tts 生成主视觉、约 5 秒演示视频与中文旁白，并把视频嵌入页面。"
        "页面文案和表达采用该 skill 的风格与人设。所有 provider 产物必须进入 V8OS artifact 后再交付，"
        "不要把 provider raw JSON 当结果，不要使用系统 <voice>text</voice> 聊天气泡 TTS。"
        "允许在当前工作区写入本任务文件；请由 Supervisor 验收 HTML、媒体 artifact 与可打开性后再结束。"
    )
    case = LiveCaseSpec(
        case_id="creative_media_engineering_joint",
        title="Engineering 与 Creative Media 联合真实交付",
        prompt=prompt,
        expected_all_tools=["fetch_skill_instructions", "runtime_broker", "creative_media_jobs"],
        skill_required=True,
        skill_references=skill_refs,
        context_mentions=context_mentions,
    )
    before = _workspace_snapshot(workspace)
    result = _submit_case(
        args.engine_url,
        case=case,
        model_profile=_default_model_profile_label(),
        timestamp=timestamp,
        workspace=str(workspace),
    )
    result = _poll_case(args.engine_url, result, max_wait=args.max_wait)
    after = _workspace_snapshot(workspace)
    changed = _changed_files(before, after)
    report_dir = REPORT_ROOT / timestamp
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "sessionId": result.session_id,
        "runId": result.run_id,
        "status": result.status,
        "latencyMs": result.latency_ms,
        "failureReason": result.failure_reason,
        "actualTools": result.actual_tools,
        "observedTopics": result.observed_topics,
        "episodes": result.episodes,
        "handoffs": result.handoffs,
        "changedFiles": changed,
        "finalText": result.final_text,
        "keyEvents": result.key_events,
    }
    report_path = report_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Status: {result.status}; tools={','.join(result.actual_tools) or '-'}")
    print(f"Changed files: {', '.join(changed) or '-'}")
    required = set(case.expected_all_tools)
    missing = sorted(required.difference(result.actual_tools))
    html_files = [path for path in changed if path.lower().endswith((".html", ".htm"))]
    if result.status != "completed" or missing or not html_files:
        print(f"Acceptance gaps: status={result.status}, missingTools={missing}, htmlFiles={html_files}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
