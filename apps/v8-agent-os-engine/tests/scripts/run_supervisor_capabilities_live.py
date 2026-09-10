"""Opt-in real Supervisor capability audit; reuses the existing session/UI harness."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import hashlib
import io
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

ENGINE_ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def owned_window(directory: Path):
    from tests.scripts.computer_use_owned_window_probe import input_desktop_status, _read_json_when_ready

    if not input_desktop_status().get("available"):
        raise RuntimeError("Unlock the desktop before the owned-window live case")
    directory.mkdir()
    title = "V8-Capability-" + uuid.uuid4().hex[:10]
    fixture = ENGINE_ROOT / "tests/fixtures/computer_use/owned_probe_window.ps1"
    with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
        process = subprocess.Popen([
            "powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(fixture),
            "-ProbeTitle", title, "-ProbeDirectory", str(directory),
        ], stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            ready = _read_json_when_ready(directory / "ready.json", process)
            if ready["pid"] != process.pid or ready["title"] != title:
                raise RuntimeError("Owned GUI identity mismatch")
            yield ready
        finally:
            (directory / "close.marker").touch()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()  # Only the fixture process created above.
                process.wait(timeout=5)


@contextmanager
def owned_page(*, direct_api: bool = False):
    import http.server
    import threading
    from tests.scripts.run_browser_broker_live_audit import HTML
    submitted = {}
    html = HTML.replace("document.querySelector('#status').textContent='Saved: '+document.querySelector('#title').value;",
                        "document.querySelector('#status').textContent='Saved: '+document.querySelector('#title').value; fetch('/submitted',{method:'POST',body:document.querySelector('#title').value});")
    if direct_api:
        html = html.replace('<h1>', '<aside><h2>应用集成接口</h2><p>本应用支持直接控制：GET /api/title 读取当前标题；POST /api/title 用 application/json 提交 {"title":"新标题"}。无需登录，只操作本测试登记条目。</p></aside><h1>')
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            if self.path == '/api/title':
                submitted['reads'] = submitted.get('reads', 0) + 1
                submitted.setdefault('readTitles', []).append(submitted.get('text', ''))
                body = json.dumps({'title': submitted.get('text', '')}).encode()
                self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
                return
            body=html.encode('utf-8')
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            body = self.rfile.read(min(256,int(self.headers.get('Content-Length','0')))).decode()
            submitted['text'] = str(json.loads(body).get('title', '')) if self.path == '/api/title' else body
            submitted['path'] = self.path
            self.send_response(204); self.end_headers()
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        yield {'url':f'http://127.0.0.1:{server.server_port}/', 'submitted':submitted}
    finally:
        server.shutdown(); server.server_close()


def case_spec(kind: str, *, window_title: str = "", nonce: str = "", page_url: str = "", video_url: str = ""):
    from tests.scripts.run_supervisor_runtime_skill_live_audit import LiveCaseSpec

    prompts = {
        "direct_images": (
            "请你自己用 vision_media_analyzer 的 images 参数一次联合查看当前工作区的 frame-2.png、frame-0.png、frame-2.png，"
            "保持这个顺序和重复图片。不用子代理或runtime委派。本次完全禁止调用命令，包括只读列目录；直接把相对文件名传给视觉工具即可。"
            "按图像实际内容回答每张图中蓝色圆形位于左、中还是右，以及橙色矩形的准确数量。"
            "最后输出三行：编号,位置(left/middle/right),数量；可另外中文说明你不能确定的内容。不要把图片准备回执当作已看到图片。"
        ),
        "video": (
            f"Agent浏览器已登录这个视频网站：{video_url} 。请你自己用该浏览器新开测试页，保留原有标签。"
            "观察一个当前可播放的视频，读取可用字幕，并取该视频三个不同时间点的实际画面（优先前10秒内，短视频按实际时长调整），"
            "用视觉工具联合查看后，中文简述画面发生了什么及所取时间点。用户授权此次读取与必要的暂停/定位，完成后恢复播放状态并关闭你新开的页。"
            "不要点赞、关注、评论、购买，不用子代理，也不要把网页标题/推荐文案当成视频内容。若播放器不可访问、字幕不可用或只能读到部分画面，明确说明实际覆盖与失败。"
        ),
        "browser": (
            f"请你自己打开测试网页 {page_url} ，在 Title 输入框填入 {nonce}，点击 Save，"
            "然后读取实际网页检查保存后的提示是否正确。只操作这个测试页，不用子代理，不用命令或直接HTTP请求代替页面操作。"
            "不要碰密码框，不点击两个Duplicate按钮。验证完关闭你新开的测试页，告诉我结果。"
        ),
        "discovery": (
            "帮我看看这台电脑上有哪些浏览器，以及我当前真正接入、能生成图片的模型。"
            "你自己查一下再用中文简短告诉我，不启动生成，不找其他代理，不改配置。"
        ),
        "interface": (
            f"请你自己把测试登记应用 {page_url} 的 Title 改为 {nonce}，核对实际保存状态后简短告诉我。"
            "只操作该测试应用，不委派、不改其他文件或系统配置。"
        ),
        "research_chat": (
            "请用 research_broker 做一个小调研：Python venv 有什么作用，是否复制所有已有第三方包？"
            "本次明确先在秘塔网页聊天里提交问题（复用 Agent 浏览器登录态，不走秘塔 API），"
            "再实际读取其中引用的 Python 官方文档核验。最终中文简答并保留来源，区分网页AI回答和官方原文。"
            "不需要工程任务或子代理，不改任何应用配置。"
        ),
        "desktop": (
            f"请你自己操作已经打开的测试窗口『{window_title}』（不是带-dialog的那个小窗口）。"
            f"把输入框改成『{nonce}』，点击 Submit owned test，然后看窗口确认提交后的文字确实正确。"
            "我授权对这个测试窗口的输入和点击。不要操作其他应用，不要用命令、脚本或文件写入替代界面操作，"
            "不用其他代理，也不要把完整任务转给一个自动桌面助手。完成后简短告诉我实际结果。"
        ),
        "media": (
            "用我已接入且可用的图片生成模型，生成一张方形小插画：奶油白底，一只戴蓝色围巾的橙色猫坐在绿色盆栽旁，"
            "扁平插画，无文字。先查清可用模型，选一个即可，用最小的合适尺寸只生成一张。"
            "这是已授权的生成任务，不需再问风格或样片。我希望你自己把它做完，不调用任何协作代理或创作导演。"
            "检查实际生成的图片再交付，若失败如实说明，不能把草案描述当图片。"
        ),
        "delegation": (
            "请找两个子代理分别帮我做独立只读核验：一位查清当前已接入哪些图片生成模型，"
            "另一位查清本机有哪些浏览器应用。两份任务各由一个实际子代理完成，允许按当前并发限制依次执行，"
            "不让他们再委派、不生成媒体、不操作应用、不改配置或文件。你收到两个结果后检查并用中文合并告诉我。"
        ),
    }
    return LiveCaseSpec(case_id="capability_" + kind, title="Supervisor capability " + kind, prompt=prompts[kind])


def evaluate_result(result, kind: str, *, submitted: dict | None = None, nonce: str = "") -> dict:
    from tests.scripts.run_supervisor_runtime_skill_live_audit import _handoff_payload

    names = {item["toolName"] for item in result.tool_invocations}
    delegated = [item for item in result.handoffs if item.get("kind") == "subagent_result"]
    checks = {"completed": result.status == "completed", "deliveryPresent": bool(result.final_text.strip())}
    if kind not in {"delegation", "research_chat"}:
        checks["noHiddenDelegation"] = not delegated and not result.episodes and "computer_use_execute_task" not in names
    if kind == "discovery":
        checks["actualDiscovery"] = {"computer_use_list_apps", "creative_media_capabilities"} <= names
        checks["noGeneration"] = "creative_media_jobs" not in names
    elif kind == "direct_images":
        checks["oneVisualRead"] = [t["toolName"] for t in result.tool_invocations].count("vision_media_analyzer") == 1
        checks["visibleImageFacts"] = all(re.search(rf"(?m)^\s*{i}\s*[,，]\s*{position}\s*[,，]\s*{count}\s*$", result.final_text)
            for i, position, count in [(1, "right", 3), (2, "left", 1), (3, "right", 3)])
        checks["noCommandSubstitute"] = not names & {"run_system_command", "read_native_file"}
    elif kind == "desktop":
        checks["actualGuiSubmission"] = (submitted or {}).get("submittedText") == nonce
        checks["directObservedAction"] = {"computer_use_observe_scene", "computer_use_click_target"} <= names
        checks["noShellOrFileSubstitute"] = not names & {"run_system_command", "write_native_file", "edit_native_file", "replace_native_file"}
    elif kind == "browser":
        checks["actualPageSubmission"] = (submitted or {}).get("text") == nonce
        checks["directBrowserUsed"] = "browser_broker" in names
        checks["noAlternateExecution"] = not names & {"run_system_command", "http_request", "computer_use_execute_task"}
        checks["observedDelivery"] = nonce in result.final_text
    elif kind == "interface":
        checks["directInterfaceMutation"] = (submitted or {}).get('path') == '/api/title' and (submitted or {}).get('text') == nonce
        checks["actualReadback"] = nonce in (submitted or {}).get('readTitles', [])
        checks["observedDelivery"] = nonce in result.final_text
    elif kind == "research_chat":
        # The route compiler owns the L3 Research episode; runtime_broker is
        # the visible parent receipt and the episode handoff is the execution
        # proof. The child broker is intentionally not exposed as a second
        # Supervisor planner/tool surface.
        checks["researchRuntimeRoute"] = 'runtime_broker' in names and any(
            str(item.get('kind')) == 'research' and str(item.get('state')) == 'completed'
            for item in result.episodes
        )
        checks["noHiddenDelegation"] = not delegated
        checks["deliveredAnswerAndSource"] = 'venv' in result.final_text.lower() and 'docs.python.org' in result.final_text
    elif kind == "media":
        checks["actualMediaJob"] = {"creative_media_capabilities", "creative_media_jobs"} <= names
        checks["visualInspection"] = "vision_media_analyzer" in names
        # Artifact bytes, model identity and visual content are checked below/at review, not inferred from a job call.
    elif kind == "video":
        checks["actualToolsUsed"] = {"browser_broker", "vision_media_analyzer"} <= names
    else:
        identities = {str(item.get("delegationId") or _handoff_payload(item).get("delegationId") or "") for item in delegated}
        checks["twoIndependentResults"] = len(delegated) == 2 and len(identities - {""}) == 2
        checks["bothDiscoveryTasksExecuted"] = {"computer_use_list_apps", "creative_media_capabilities"} <= names
        accepted = [item for item in result.handoffs if item.get("kind") == "subagent_acceptance" and item.get("status") == "accepted"]
        checks["parentAcceptedBoth"] = len(accepted) == 2
    web = result.web_activity_audit or {}
    checks["webLiveReloadParity"] = bool(web.get("performed") and web.get("parity")) and all(web.get("parity", {}).values()) and not web.get("errors")
    return checks


def completed_tool_results(events: list[dict], name: str) -> list[str]:
    results = []
    for event in events:
        tool = (event.get("payload") or {}).get("tool") or {}
        if event.get("topic") != "tool.finished" or tool.get("toolName") != name or tool.get("resultStatus") != "completed":
            continue
        content = tool.get("agentVisibleResult") or tool.get("result")
        if isinstance(content, str):
            results.append(content)
    return results


def apply_tool_evidence(checks: dict, events: list[dict], kind: str, *, resumed: bool = False) -> None:
    if kind in {"media", "video"} and not (resumed and kind == "video"):
        checks["visualInspectionSucceeded"] = any(
            body.startswith("--- Vision Analysis Complete ---")
            for body in completed_tool_results(events, "vision_media_analyzer")
        )
    if kind in {"browser", "video"}:
        checks["ownedPageClosed"] = any(
            body.startswith("Browser close: completed")
            for body in completed_tool_results(events, "browser_broker")
        )


def latest_video_samples(events: list[dict]) -> list[tuple[float, str]]:
    samples = []
    for body in completed_tool_results(events, "browser_broker"):
        if not body.startswith("Browser media: completed"):
            continue
        frames = [(float(time), path.strip()) for time, path in re.findall(
            r"^Frame \d+ at ([0-9.]+)s; vision file_path: (.+)$", body, re.MULTILINE)]
        if len({time for time, _ in frames}) >= 3:
            samples = frames
    return samples


def video_summary_matches_samples(text: str, samples: list[tuple[float, str]]) -> bool:
    # The task permits choosing three times. Never substitute a fixture's old
    # 2/6/10-second choices for the actual player's captured observations.
    return len({time for time, _ in samples}) >= 3 and all(
        re.search(rf"(?<![\d.]){re.escape(f'{time:g}')}\s*(?:秒|s\b)", text)
        for time, _ in samples)


def sampled_frames_were_analyzed(events: list[dict], samples: list[tuple[float, str]]) -> bool:
    normalize = lambda path: str(path).replace("\\", "/")
    expected = [normalize(path) for _, path in samples]
    completed = {(event.get("payload", {}).get("tool") or {}).get("toolCallId") for event in events
                 if event.get("topic") == "tool.finished"
                 and (event.get("payload", {}).get("tool") or {}).get("resultStatus") == "completed"}
    for event in events:
        tool = (event.get("payload") or {}).get("tool") or {}
        if event.get("topic") != "tool.started" or tool.get("toolName") != "vision_media_analyzer" or tool.get("toolCallId") not in completed:
            continue
        images = (tool.get("args") or {}).get("images") or []
        if expected and [normalize(item.get("file_path")) for item in images if isinstance(item, dict)] == expected:
            return True
    return False


def durable_proof(result, engine_url: str, kind: str) -> dict:
    """Read only this harness session; never copy provider credentials or other runs."""
    from core.v8_agent_os_paths import OBSERVABILITY_DB_PATH, STATE_DB_PATH
    import urllib.request
    from urllib.parse import quote
    from PIL import Image

    with sqlite3.connect(OBSERVABILITY_DB_PATH.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT model_id, role, status, latency_ms, input_tokens, output_tokens, metadata_json "
                            "FROM model_invocation_logs WHERE session_id = ? AND run_id = ? ORDER BY started_at",
                            (result.session_id, result.run_id)).fetchall()
    models = []
    for row in rows:
        record = dict(row)
        metadata = json.loads(record.pop("metadata_json") or "{}")
        record["timing"] = {key:metadata.get(key) for key in (
            "timeToFirstChunkMs", "timeToFirstContentChunkMs", "maxInterChunkGapMs", "tailAfterLastChunkMs",
            "contextPreparationMs", "cacheUsage", "finishReason", "requestedMaxTokens",
        )}
        models.append(record)
    jobs = []
    if kind == "media":
        with sqlite3.connect(STATE_DB_PATH.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            job_rows = conn.execute("SELECT payload_json FROM creative_media_jobs WHERE session_id = ?", (result.session_id,)).fetchall()
        for row in job_rows:
            job = json.loads(row[0])
            proof = {key:job.get(key) for key in ("jobId", "status", "modality")}
            request = job.get("request") or {}
            proof["requestedModel"] = {key:request.get(key) for key in ("providerId", "modelId", "modelRef")}
            response = job.get("providerResponse") or {}
            proof["returnedModel"] = {key:response.get(key) for key in ("providerId", "model")}
            artifacts = []
            for artifact in job.get("artifacts") or []:
                artifact_id = artifact.get("artifactId") or artifact.get("id")
                url = engine_url.rstrip("/") + f"/v1/artifacts/{quote(str(artifact_id))}/content?sessionId={quote(result.session_id)}"
                with urllib.request.urlopen(url, timeout=20) as response:
                    data = response.read(30 * 1024 * 1024 + 1)
                if len(data) > 30 * 1024 * 1024:
                    raise RuntimeError("Image artifact exceeds bounded audit input")
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    dimensions = list(image.size)
                artifacts.append({"artifactId": artifact_id, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "dimensions": dimensions})
            proof["artifacts"] = artifacts
            jobs.append(proof)
    return {"modelCalls": models, "mediaJobs": jobs}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--case", choices=["discovery", "desktop", "browser", "interface", "research_chat", "media", "delegation", "video", "direct_images"], required=True)
    parser.add_argument("--video-url", default="", help="Explicitly authorized video page for the video case")
    parser.add_argument("--engine-url", default="http://127.0.0.1:9530")
    parser.add_argument("--web-url", required=True)
    parser.add_argument("--browser-executable")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-wait", type=float, default=480)
    parser.add_argument("--resume-report", help="Continue only a prior media/video harness session, reusing its captured evidence.")
    args = parser.parse_args(argv)
    if not args.live or (args.case in {"desktop", "browser", "interface", "media", "video"} and not args.allow_side_effects):
        parser.error("--live is required; desktop/media also require --allow-side-effects")
    if args.case == "video" and not args.video_url.startswith(("https://", "http://")):
        parser.error("video case requires an explicitly authorized --video-url")
    output = Path(args.output_dir).resolve()
    if output.exists():
        parser.error("output directory must be new")
    sys.path.insert(0, str(ENGINE_ROOT))
    from tests.scripts.run_supervisor_runtime_skill_live_audit import (
        _prepare_engineering_live_workspace, _submit_case, _poll_case, _cancel_timed_out_case,
        _load_durable_runtime_events, _redact, _wait_for_engine,
    )
    from tests.scripts.live_web_activity_audit import WebActivityAuditObserver
    from contextlib import nullcontext

    output.mkdir(parents=True)
    if not _wait_for_engine(args.engine_url, timeout=45)[0]:
        raise RuntimeError("Engine preflight unavailable; no paid task submitted")
    import urllib.request
    # Avoid starting a paid task when its mandatory UI observer cannot attach.
    with urllib.request.urlopen(args.web_url.rstrip("/") + "/chat", timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("Web observer preflight unavailable")
    prior = json.loads(Path(args.resume_report).read_text(encoding="utf-8")) if args.resume_report else None
    if prior and (args.case not in {"media", "video"} or prior.get("case") != args.case
                  or not str(prior.get("result", {}).get("session_id", "")).startswith("supervisor-runtime-skill-live-")):
        parser.error("resume requires a matching media/video harness report")
    workspace = prior["workspace"] if prior else _prepare_engineering_live_workspace(args.engine_url, browser_executable=args.browser_executable)
    if args.case == "direct_images":
        from tests.scripts.run_vision_images_live_audit import generate_images
        from core.storage import storage
        if storage.get_supervisor_config().get("compressedDirectImages") is not True:
            raise RuntimeError("Enable compressed direct images in Admin before this opt-in live case")
        generate_images(Path(workspace))
    nonce = "direct-" + uuid.uuid4().hex[:8]
    fixture = owned_window(output / "window") if args.case == "desktop" else owned_page(direct_api=args.case == 'interface') if args.case in {"browser", "interface"} else nullcontext({})
    with fixture as window:
        spec = case_spec(args.case, window_title=window.get("title", ""), nonce=nonce, page_url=window.get("url",""), video_url=args.video_url)
        if prior:
            spec.prompt = ("刚才已经取得并视觉分析了视频取样画面。请复用这些证据完成简短中文总结，并确认关闭你上轮新开的那个测试页，保留我的原始标签。不要重开网页、不要重复取帧或再做视觉分析，也不委派；若那个测试页已关闭如实说明。摘要按已记录的实际取样时间交代可见变化和未覆盖部分，不把静态帧说成证明了全片镜头运动或人物真实情绪。"
                           if args.case == "video" else "继续检查刚才这轮已经生成的那张猫的图片，直接读取实际图片逐项核查主题、颜色和无文字要求，完成我原先要求的视觉验证。不要重新生成，不委派，不改配置。")
        started = time.perf_counter()
        result = _submit_case(args.engine_url, case=spec, model_profile="configured",
                              timestamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), workspace=workspace,
                              existing_session_id=prior["result"]["session_id"] if prior else None)
        observer = WebActivityAuditObserver(web_url=args.web_url, session_id=result.session_id,
                                           browser_executable=args.browser_executable, headless=True, observe_timers=True)
        try:
            observer.start()
            _poll_case(args.engine_url, result, max_wait=args.max_wait, sample_web_activity=observer.sample_live)
            result.web_activity_audit = observer.finish()
        except Exception as exc:
            result.status = "timeout"
            result.failure_reason = "live_observer_failed:" + type(exc).__name__
            result.web_activity_audit = {"performed": False, "errors": [type(exc).__name__]}
        finally:
            observer.close()
            _cancel_timed_out_case(args.engine_url, result)
        submitted_path = output / "window/submitted.json"
        submitted = json.loads(submitted_path.read_text(encoding="utf-8-sig")) if submitted_path.exists() else None
        if args.case in {"browser", "interface"}: submitted = window["submitted"]
        checks = evaluate_result(result, args.case, submitted=submitted, nonce=nonce)
        if prior:
            checks.pop("actualMediaJob", None)  # Existing real artifact is the target; re-creation would be a bug.
        proof = durable_proof(result, args.engine_url, args.case)
        if args.case == "direct_images":
            checks["modelOwnershipKnown"] = bool(proof["modelCalls"]) and all(str(m.get("role") or "").strip() for m in proof["modelCalls"])
            checks["noSeparateVisionModel"] = checks["modelOwnershipKnown"] and not any(str(m.get("role")) == "vision" for m in proof["modelCalls"])
        if args.case == "media":
            jobs = proof["mediaJobs"]
            checks["oneRealImageDelivered"] = len(jobs) == 1 and jobs[0]["status"] == "succeeded" and len(jobs[0]["artifacts"]) == 1
        events, error = _load_durable_runtime_events(result)
        apply_tool_evidence(checks, events, args.case, resumed=bool(prior))
        if args.case == "video":
            media_observations = []
            for event in events:
                tool = (event.get("payload") or {}).get("tool") or {}
                content = tool.get("agentVisibleResult") or tool.get("result") or ""
                if tool.get("toolName") == "browser_broker" and isinstance(content, str) and content.startswith("Browser media: completed"):
                    media_observations.append(content)
            checks["sampledActualVideo"] = any(content.count("; vision file_path:") >= 3 for content in media_observations)
            if prior:
                checks["reusedSampledVideo"] = bool(prior.get("checks", {}).get("sampledActualVideo"))
                checks.pop("sampledActualVideo")
                checks.pop("actualToolsUsed")
                checks["noRepeatedVision"] = "vision_media_analyzer" not in result.actual_tools
            evidence_events = prior["events"] if prior else events
            samples = latest_video_samples(evidence_events)
            checks["deliveredSampleSummary"] = video_summary_matches_samples(result.final_text, samples)
            checks["sampledFramesVisuallyRead"] = sampled_frames_were_analyzed(evidence_events, samples)
        report = {"case": args.case, "checks": checks, "passed": all(checks.values()),
                  "wallMs": round((time.perf_counter() - started) * 1000, 2), "workspace": workspace,
                  "result": asdict(result), "events": events, "eventReadError": error,
                  "proof": proof,
                  "visualContentManuallyReviewed": False}
        (output / "report.json").write_text(_redact(report), encoding="utf-8")
        print(json.dumps({"case": args.case, "checks": checks, "wallMs": report["wallMs"],
                          "sessionId": result.session_id, "tools": result.actual_tools}, ensure_ascii=False))
        return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
