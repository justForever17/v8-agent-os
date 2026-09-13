"""Render real Admin pages against synthetic API responses on owned port 22824.

Own launcher is necessary to isolate both V8 state and os.homedir consumers.
Never opens a user profile, reads real credentials, or calls an Engine instance.
"""
import argparse
import json
import os
import secrets
import shutil
import socket
import statistics
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ORIGIN = "http://127.0.0.1:22824"
APP = "apps/v8-agent-os-admin"


def measure(page):
    return page.evaluate("""() => {
      const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
      const rect = e => { const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
      const controls=[...document.querySelectorAll('button,input,select,textarea,a,[role=button]')].filter(visible);
      const viewport={width:innerWidth,height:innerHeight};
      return {viewport, title:document.title, headings:[...document.querySelectorAll('h1,h2')].filter(visible).map(e=>e.textContent.trim()),
        controls:controls.map(e=>({tag:e.tagName,role:e.getAttribute('role'),name:(e.getAttribute('aria-label')||e.textContent||e.getAttribute('placeholder')||'').trim().slice(0,90),disabled:!!e.disabled,tabIndex:e.tabIndex,rect:rect(e)})),
        scrollHosts:[...document.querySelectorAll('main,aside,nav,[role=dialog],main div')].filter(e=>visible(e)&&e.scrollHeight>e.clientHeight+2&&['auto','scroll'].includes(getComputedStyle(e).overflowY)).map(e=>({tag:e.tagName,role:e.getAttribute('role'),height:e.clientHeight,scrollHeight:e.scrollHeight,rect:rect(e)})),
        rootFont:getComputedStyle(document.documentElement).fontSize,
        viewportOverflow:document.documentElement.scrollWidth>innerWidth+1,
        spinners:document.querySelectorAll('.animate-spin').length,
        animationCount:document.getAnimations().length,
        dialogs:[...document.querySelectorAll('[role=dialog]')].filter(visible).map(e=>({text:e.textContent.slice(0,120),rect:rect(e)}))};
    }""")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--routes", nargs="*", default=["/admin", "/admin/model-hub", "/admin/system-base", "/admin/subagents", "/admin/extensions/store", "/admin/rpa"])
    p.add_argument("--all-admin", action="store_true")
    p.add_argument("--warm-samples", type=int, default=0)
    args = p.parse_args()
    if args.warm_samples and args.warm_samples < 30:
        p.error("warm performance collection requires at least 30 samples")
    repo = args.repo.resolve()
    if args.all_admin:
        from collect import collect
        args.routes = [row["route"].replace("[id]", "synthetic-provider") for row in collect(repo)["pages"] if row["app"] == "admin"]
        args.routes += ["/admin/memory?tab=" + tab for tab in ["context", "preferences", "logs", "knowledge", "workflows", "artifacts", "graph", "agent", "runtime", "config", "upload"]]
    out = args.out.resolve()
    if not out.is_relative_to(repo / "tmp"):
        p.error("outputs including synthetic state must remain in this worktree/tmp")
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo, text=True).strip()
    if branch != "codex/9131-experience":
        p.error("own launcher requires codex/9131-experience")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 22824)) == 0:
            p.error("22824 is already occupied; will not replace an existing process")
    out.mkdir(parents=True, exist_ok=True)
    fake_home = out / ("isolated-home-" + secrets.token_hex(5))
    state = fake_home / ".v8-agent-os"
    state.mkdir(parents=True)
    fixture_config = {"bridge": {"engineBaseUrl": "http://127.0.0.1:9/v1", "engineWsBaseUrl": "ws://127.0.0.1:9/v1", "adminBaseUrl": ORIGIN + "/api", "desktopLiveBridgeBaseUrl": "http://127.0.0.1:9/v1"}, "systemBase": {"desktopLive": {"enabled": False}}, "appearance": {"theme": "light"}}
    (state / "config.json").write_text(json.dumps(fixture_config), encoding="utf-8")
    env = dict(os.environ)
    # Only this child receives these non-secret test paths. HOME/CODEX_HOME stay untouched.
    env.update(V8_AGENT_OS_HOME=str(state), USERPROFILE=str(fake_home), V8_ADMIN_HOSTNAME="127.0.0.1", NEXT_TELEMETRY_DISABLED="1")
    env.pop("V8_NEXT_BUILD", None)
    log = (out / "server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([shutil.which("node"), str(repo / "scripts/experience/9131/run-isolated-admin.mjs")], cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    rows, requests, errors = [], [], []
    report = {"sourceHead": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(), "fixture": "synthetic-unavailable-and-long-readme-v1", "runtime": "real Admin dev source; API boundary fixtures; no Engine", "rows": rows, "requests": requests, "errors": errors}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            report["browserVersion"] = browser.version
            report["viewport"] = {"width": 1440, "height": 900, "deviceScaleFactor": 1}
            report["reducedMotion"] = True
            context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN", reduced_motion="reduce")
            page = context.new_page()
            page.set_default_timeout(20000)
            deadline = time.monotonic() + 75
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError("owned server exited; inspect its isolated log")
                try:
                    page.goto(ORIGIN + "/login", wait_until="networkidle", timeout=15000)
                    if page.locator("#login").count():
                        break
                except Exception:
                    time.sleep(0.5)
            page.locator("#login").wait_for()
            page.screenshot(path=str(out / "login.png"))
            password = secrets.token_urlsafe(24)
            page.locator("#login").fill("synthetic-experience")
            page.locator("#name").fill("合成验收账户")
            page.locator("#password").fill(password)
            page.locator("#confirmPassword").fill(password)
            # Local, generated owner setup is the only real mutation in this harness.
            def api(route):
                req = route.request
                url = urlparse(req.url)
                if url.netloc != "127.0.0.1:22824":
                    requests.append({"path": url.path, "method": req.method, "action": "external-aborted"})
                    route.abort(); return
                if url.path.startswith("/api/auth/") and url.path in {"/api/auth/bootstrap", "/api/auth/providers", "/api/auth/csrf", "/api/auth/callback/credentials", "/api/auth/session"}:
                    route.continue_(); return
                if not url.path.startswith("/api/"):
                    route.continue_(); return
                requests.append({"path": url.path, "method": req.method})
                payload, status = {"error": "合成服务不可用，可重试", "code": "synthetic_unavailable"}, 503
                if req.method != "GET":
                    payload, status = {"error": "Synthetic harness blocks product mutation", "code": "fixture_write_blocked"}, 409
                elif url.path == "/api/extensions/store/skills":
                    payload, status = {"items": [{"id": "fixture/long-skill", "name": "合成长文技能", "source": "fixture", "skillId": "long-skill", "description": "用于检验说明很长时安装按钮的位置", "installs": 321, "detailUrl": "https://fixture.invalid/skill"}], "warnings": []}, 200
                elif url.path == "/api/extensions/store/skills/detail":
                    payload, status = {"name": "合成长文技能", "source": "fixture", "skillId": "long-skill", "description": "合成验收", "markdown": "\n\n".join(f"## 合成段落 {n}\n内容只用于长说明滚动与安装操作可达性。" for n in range(100)), "detailUrl": "https://fixture.invalid/skill"}, 200
                route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))
            context.route("**/*", api)
            page.locator("form button[type=submit]").click()
            page.wait_for_url("**/admin", timeout=60000)
            password = None
            page.on("pageerror", lambda error: errors.append(str(error)[:250]))
            for index, route in enumerate(args.routes):
                start = time.perf_counter()
                page.goto(ORIGIN + route, wait_until="networkidle", timeout=90000)
                if route == "/admin/extensions/store":
                    page.get_by_text("合成长文技能", exact=True).wait_for()
                page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                data = measure(page)
                data.update(route=route, url=urlparse(page.url).path, level="RENDERED", navigationMs=round((time.perf_counter() - start) * 1000), performanceQualification="one dev cold/warm mixed observation, not a performance comparison", screenshot=f"route-{index}.png")
                page.screenshot(path=str(out / data["screenshot"]))
                rows.append(data)
                print(json.dumps({"route": route, "headings": data["headings"], "controls": len(data["controls"]), "spinners": data["spinners"]}, ensure_ascii=False), flush=True)
                (out / "observations.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            # Revisit synthetic non-empty store and inspect its actual long detail.
            page.goto(ORIGIN + "/admin/extensions/store", wait_until="networkidle")
            page.get_by_text("合成长文技能", exact=True).wait_for()
            page.get_by_role("button", name="展开详情", exact=True).click()
            page.get_by_role("dialog").wait_for()
            page.get_by_role("dialog").get_by_text("合成段落 99", exact=True).wait_for(state="attached")
            detail = measure(page)
            detail.update(route="/admin/extensions/store#long-detail", level="INTERACTED", screenshot="long-detail.png")
            rows.append(detail)
            page.screenshot(path=str(out / "long-detail.png"))
            page.keyboard.press("Escape")
            page.get_by_role("dialog").wait_for(state="hidden")
            rows.append({"route": "/admin/extensions/store#escape", "level": "INTERACTED", "openDialogs": page.get_by_role("dialog").count()})
            if args.warm_samples:
                durations = []
                page.evaluate("""() => document.addEventListener('click', e => {
                  if (e.target.closest('button')?.textContent.trim()==='展开详情') window.__detailStart=performance.now();
                }, true)""")
                for index in range(args.warm_samples):
                    page.get_by_role("button", name="展开详情", exact=True).click()
                    page.get_by_role("dialog").get_by_text("合成段落 99", exact=True).wait_for(state="attached")
                    elapsed = page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(performance.now()-window.__detailStart))))")
                    durations.append(round(elapsed, 2))
                    page.keyboard.press("Escape")
                    page.get_by_role("dialog").wait_for(state="hidden")
                values = sorted(durations)
                report["performance"] = {"scenario": "open-long-readme-click-to-content-two-frames", "buildMode": "dev-webpack", "network": "in-process synthetic API boundary", "coldOrWarm": "warm", "samplesMs": durations, "n": len(durations), "medianMs": statistics.median(durations), "p95Ms": values[max(0, __import__('math').ceil(len(values)*0.95)-1)], "qualification": "Real browser event-to-content observation, includes fixture request and test synchronization. Not production INP or native frame performance. Compare only the same fixture/environment."}
            page.set_viewport_size({"width": 390, "height": 844})
            narrow = measure(page)
            narrow.update(route="/admin/extensions/store#390", level="RENDERED", screenshot="narrow.png")
            rows.append(narrow)
            page.screenshot(path=str(out / "narrow.png"))
            browser.close()
    except Exception as error:
        report["harnessError"] = str(error)[:500]
        raise
    finally:
        (out / "observations.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
            proc.wait(timeout=15)
        log.close()


if __name__ == "__main__":
    main()
