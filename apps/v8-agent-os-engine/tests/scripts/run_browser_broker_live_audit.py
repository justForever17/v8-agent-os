"""Explicit real-local browser acceptance; never invokes a model or user browser cleanup."""
from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import shutil
import sys
import tempfile
import threading
import time


HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Browser Broker Fixture</title></head>
<body><main><h1>Browser Broker Fixture</h1><form id="fixture-form">
<label for="title">Title</label><input id="title" name="title">
<button type="button" id="save">Save</button><p role="status" id="status">Waiting</p>
<label for="password">Password</label><input id="password" type="password" value="PRIVATE-PASSWORD-CANARY">
</form><button>Duplicate</button><button>Duplicate</button></main>
<script>document.querySelector('#save').onclick=()=>{document.querySelector('#status').textContent='Saved: '+document.querySelector('#title').value;};
const extension=document.createElement('div');document.body.append(extension);const extensionBody=document.createElement('body');extensionBody.textContent='Injected extension panel';extension.attachShadow({mode:'open'}).append(extensionBody);
</script>
</body></html>"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--isolated-root")
    parser.add_argument("--video", action="store_true", help="Also verify an owned synthetic video; requires ffmpeg.")
    args = parser.parse_args(argv)
    if not args.live:
        print("Refused: --live is required before creating state or starting any browser.")
        return 2
    root = Path(args.isolated_root).resolve() if args.isolated_root else Path(tempfile.mkdtemp(prefix="v8-browser-live-"))
    if root.exists() and any(root.iterdir()):
        raise SystemExit("isolated root must be new or empty")
    root.mkdir(parents=True, exist_ok=True)
    os.environ["V8_AGENT_OS_HOME"] = str(root / "state")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.database import db
    from core.storage import storage
    from core.tools.native.browser import browser_broker
    from erc.runtime_context import bind_runtime_context
    from runtimes.computer_use.browser_automation import agent_browser_automation
    from runtimes.computer_use.browser_session_service import browser_session_service

    workspace = root / "workspace"
    workspace.mkdir()
    fixture_html = HTML
    if args.video:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required for the synthetic video fixture")
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=320x180:d=2:r=10",
                        "-f", "lavfi", "-i", "color=c=green:s=320x180:d=2:r=10", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2:r=10",
                        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]", "-map", "[v]", "-c:v", "libx264",
                        "-movflags", "+faststart", str(workspace / "fixture.mp4")], check=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        (workspace / "fixture.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:02.000\nFirst red frame\n\n00:02.000 --> 00:04.000\nSecond green frame\n\n00:04.000 --> 00:06.000\nThird blue frame\n", encoding="utf-8")
        fixture_html = HTML.replace("</main>", '<video id="clip" src="fixture.mp4" width="320" height="180" muted controls preload="auto"><track kind="captions" src="fixture.vtt" srclang="en" default></video><video id="other-clip" src="fixture.mp4" width="160" height="90" muted preload="auto"></video></main>')
    (workspace / "index.html").write_text(fixture_html, encoding="utf-8")
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args): pass
        def handle(self):
            try: super().handle()
            except (ConnectionResetError, BrokenPipeError): pass  # Browser closes fixture connections during teardown.
        def end_headers(self):
            if self.path == "/index.html":
                self.send_header("Set-Cookie", "v8_browser_fixture=SESSION-CANARY; HttpOnly; SameSite=Lax; Path=/")
            super().end_headers()
        def do_GET(self):
            if self.path == "/fixture.mp4" and args.video:
                data = (workspace / "fixture.mp4").read_bytes()
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
                start = int(match[1]) if match else 0
                end = min(len(data)-1, int(match[2])) if match and match[2] else len(data)-1
                self.send_response(206 if match else 200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(end-start+1))
                if match: self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.end_headers(); self.wfile.write(data[start:end+1]); return
            if self.path == "/protected":
                authorized = "v8_browser_fixture=SESSION-CANARY" in self.headers.get("Cookie", "")
                text = "PROFILE_REUSE_PROOF. " + (
                    "This fixture serves evidence only to the browser session established at its index page. "
                    "A separate incognito context or plain HTTP client cannot read this document. "
                    "The response contains no credential values. The browser must preserve its actual persistent context. "
                    "The original user page remains open and unchanged after this temporary read. "
                ) if authorized else "Authentication required"
                body = f"<html><head><title>Profile fixture</title></head><body><article><p>{text}</p></article></body></html>".encode()
                self.send_response(200 if authorized else 401)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers(); self.wfile.write(body)
                return
            super().do_GET()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(workspace)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for _ in range(30):
        with socket.socket() as proxy, socket.socket() as target:
            proxy.bind(("127.0.0.1", 0))
            proxy_port = proxy.getsockname()[1]
            if proxy_port > 65000:
                continue
            try:
                target.bind(("127.0.0.1", proxy_port + 100))
            except OSError:
                continue
            break
    else:
        raise RuntimeError("no isolated browser ports available")
    config = storage.get_computer_use_config()
    config["browserLane"].update({"enabled": True, "proxyPort": proxy_port, "connectTimeoutMs": 10000})
    storage.save_computer_use_config(config)
    base_config = storage.get_system_base_config()
    base_config["webFetch"].update({"useAgentBrowserProfile": True, "agentBrowserProfileAllowlist": []})
    storage.save_system_base_config(base_config)
    db.create_or_update_session("browser-live", "Browser local audit", user_id="fixture-owner")
    db.create_run_record("browser-live-run", "browser-live", user_id="fixture-owner")
    db.upsert_session_scope_binding({"session_id": "browser-live", "user_id": "fixture-owner", "workspace_path": str(workspace),
                                    "resolved_scope": "workspace", "scope_source": "explicit_fixture", "status": "active"})
    report = {"live": True, "layer": "real_local_no_model", "steps": [], "issues": []}
    ids = {}

    async def call(action, **kwargs):
        start = time.monotonic()
        result = await browser_broker.ainvoke({"type": "tool_call", "id": f"browser-live-{len(report['steps'])}", "name": "browser_broker", "args": {"action": action, **kwargs}})
        content = result.content
        report["steps"].append({"action": action, "seconds": round(time.monotonic() - start, 3),
                                "contentChars": len(content), "contentSha256": hashlib.sha256(content.encode()).hexdigest(),
                                "failed": f"Browser {action}: failed" in content or "Browser observe: failed" in content})
        for field in ("browser_session_id", "page_id", "observation_id"):
            match = re.search(rf"^{field}: (.+)$", content, re.MULTILINE)
            if match:
                ids[field] = match[1]
        assert "PRIVATE-PASSWORD-CANARY" not in content, "password leaked into Agent surface"
        return content

    async def scenario():
        result = await call("open", url=f"http://127.0.0.1:{server.server_port}/index.html", screenshot=True)
        assert "Browser observe: completed" in result, result
        assert "Accessibility tree:" in result and 'button "Save"' in result and "Screenshot:" in result
        from core.agent_browser_access import access_snapshot
        from core.tools import web_fetcher
        snapshot = await asyncio.to_thread(access_snapshot, refresh=True)
        assert any(site["host"] == "127.0.0.1" and site["sessionPresent"] for site in snapshot["sites"]), snapshot
        protected = f"http://127.0.0.1:{server.server_port}/protected"
        # Recreate the old bug with the installed library, on this disposable
        # profile only: CDP connects, but its new context cannot read the cookie.
        def legacy_read():
            from scrapling.fetchers import DynamicFetcher
            return DynamicFetcher.fetch(protected, cdp_url=web_fetcher._active_agent_browser_cdp_context()["cdpUrl"], timeout=5000).status
        report["legacyProfileStatus"] = await asyncio.to_thread(legacy_read)
        assert report["legacyProfileStatus"] == 401
        fetched = await asyncio.to_thread(web_fetcher.web_broker.func, mode="read", target=protected)
        assert "PROFILE_REUSE_PROOF" in fetched, fetched[:800]
        assert "SESSION-CANARY" not in fetched
        report["profileRead"] = {"cookieOnlyAccessVerified": True, "manualAllowlistRequired": False,
                                 "credentialsExported": False, "newContextControlStatus": 401}
        if args.video:
            candidates = await call("observe", browser_session_id=ids["browser_session_id"], selector="video")
            assert "Matched 2 elements" in candidates and "selector=video >> nth=0" in candidates, candidates
            media_result = await call("media", **ids, selector="video >> nth=0", sample_times=[0.5, 2.5, 4.5])
            assert "Browser media: completed" in media_result, media_result
            assert "playbackRestored=True" in media_result and "First red frame" in media_result, media_result
            frames = re.findall(r"Frame (\d+) at ([0-9.]+)s; vision file_path: (.+)", media_result)
            report["sampleObservations"] = [{"index": index, "time": timestamp} for index, timestamp, _ in frames]
            from PIL import Image, ImageStat
            # Verify actual decoded pixels rather than trusting requested timestamps.
            channels = []
            for index, timestamp, path in frames:
                assert abs(float(timestamp) - [0.5, 2.5, 4.5][int(index) - 1]) < 0.1, f"Frame {index}: actual timestamp {timestamp} did not match requested time"
                with Image.open(path.strip()) as image:
                    width, height = image.size
                    mean = ImageStat.Stat(image.convert("RGB").crop((width//3, height//4, width*2//3, height//2))).mean
                channels.append(max(range(3), key=lambda index: mean[index]))
            assert channels == [0, 1, 2], channels
            report["video"] = {"frameCount": len(frames), "actualPixelChannels": channels, "subtitlesRead": True, "playbackRestored": True}
            await call("observe", browser_session_id=ids["browser_session_id"])
        old = dict(ids)
        filled = await call("fill", **ids, role="textbox", name="Title", text="Native browser verified")
        assert "Action applied" in filled, filled
        stale = await call("click", **old, role="button", name="Save")
        assert "stale_observation" in stale, stale
        await call("observe", browser_session_id=ids["browser_session_id"], selector="#fixture-form")
        clicked = await call("click", **ids, role="button", name="Save")
        assert "Action applied" in clicked, clicked
        result = await call("observe", browser_session_id=ids["browser_session_id"], screenshot=True)
        assert "Saved: Native browser verified" in result, result
        ambiguous = await call("click", **ids, role="button", name="Duplicate")
        assert "locator_ambiguous" in ambiguous, ambiguous
        browser_session_service.take_control(ids["browser_session_id"], "live-user")
        held = await call("click", **ids, role="button", name="Save")
        assert "browser_user_control_active" in held, held
        browser_session_service.release_control(ids["browser_session_id"], "live-user")
        stale = await call("click", **ids, role="button", name="Save")
        assert "browser_reobserve_required" in stale, stale
        await call("observe", browser_session_id=ids["browser_session_id"])
        assert "Browser close: completed" in await call("close", **ids)
        artifacts = db.list_runtime_artifacts(session_id="browser-live")
        report["screenshots"] = [{"artifactId": item["artifactId"], "sha256": hashlib.sha256(Path(item["sourcePath"]).read_bytes()).hexdigest()}
                                 for item in artifacts]
        assert len(artifacts) == (5 if args.video else 2)
        from core.tools.vision_image_inputs import prepare_ordered_images
        prepared = prepare_ordered_images([{"file_path": item["sourcePath"]} for item in artifacts],
                                           runtime_context={"session_id": "browser-live", "workspace_path": str(workspace)},
                                           remote_guard=lambda _url: (_ for _ in ()).throw(AssertionError("local screenshots must not use a remote request")))
        report["visionRead"] = {"imageCount": len(prepared), "resourceKinds": [item["source"]["resourceKind"] for item in prepared],
                                "sha256": [item["source"]["sourceSha256"] for item in prepared], "modelInvoked": False}
        assert report["visionRead"]["resourceKinds"] == ["artifact"] * len(artifacts)

    try:
        with bind_runtime_context(session_id="browser-live", run_id="browser-live-run", user_id="fixture-owner",
                                  workspace_path=str(workspace), agent_id="supervisor", actor_role="supervisor", safety_approval_mode="minimal"):
            asyncio.run(scenario())
    except Exception as exc:
        report["issues"].append({"type": type(exc).__name__, "message": str(exc)[:1800]})
    finally:
        # This instance was imported only after isolating state/profile and ports.
        # shutdown owns its processes; it never enumerates/kills user browsers.
        agent_browser_automation.shutdown()
        server.shutdown()
        server.server_close()
    report["passed"] = not report["issues"]
    report_path = root / "browser_broker_live_results.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "stepCount": len(report["steps"]), "report": str(report_path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
