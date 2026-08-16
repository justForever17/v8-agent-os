from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


WEB_ROOT = Path(__file__).resolve().parents[1]
OWNER_SOURCE = WEB_ROOT / "src/components/workbench/creative-canvas/request-owner.ts"
TYPESCRIPT = WEB_ROOT / "node_modules/typescript/lib/typescript.js"


def compile_owner_controller() -> str:
    script = """
const fs = require('node:fs');
const ts = require(process.argv[1]);
const source = fs.readFileSync(process.argv[2], 'utf8');
process.stdout.write(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 }
}).outputText);
"""
    result = subprocess.run(
        ["node", "-e", script, str(TYPESCRIPT), str(OWNER_SOURCE)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout + "\nglobalThis.__createCanvasSessionRequestCoordinator = createCanvasSessionRequestCoordinator;"


HARNESS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; background: #f4f4f5; color: #18181b; font: 14px system-ui, sans-serif; }
  main { width: min(920px, calc(100% - 32px)); margin: 32px auto; }
  header { display: flex; align-items: end; justify-content: space-between; gap: 16px; border-bottom: 1px solid #d4d4d8; padding-bottom: 18px; }
  h1 { margin: 0; font-size: 22px; letter-spacing: 0; }
  header p { margin: 6px 0 0; color: #71717a; }
  #surface { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 20px 0; }
  .metric { min-height: 76px; border: 1px solid #d4d4d8; border-radius: 8px; background: white; padding: 12px; }
  .metric span { display: block; color: #71717a; font-size: 11px; }
  .metric strong { display: block; margin-top: 7px; overflow-wrap: anywhere; }
  #audit { display: grid; gap: 8px; }
  .row { display: grid; grid-template-columns: 150px 1fr 90px; gap: 10px; align-items: center; border: 1px solid #e4e4e7; border-radius: 8px; background: white; padding: 11px 12px; }
  .row code { color: #52525b; font-size: 11px; overflow-wrap: anywhere; }
  .pass { color: #047857; font-weight: 700; text-align: right; }
  @media (max-width: 620px) {
    main { width: min(100% - 20px, 920px); margin: 16px auto; }
    header { align-items: start; flex-direction: column; }
    #surface { grid-template-columns: 1fr 1fr; }
    .row { grid-template-columns: 1fr auto; }
    .row code { grid-column: 1 / -1; }
  }
</style>
</head>
<body>
<main>
  <header><div><h1>Canvas Session race matrix</h1><p>Deferred browser requests and runtime events</p></div><strong id="result">RUNNING</strong></header>
  <section id="surface">
    <div class="metric"><span>Session</span><strong id="session">-</strong></div>
    <div class="metric"><span>Lock</span><strong id="lock">-</strong></div>
    <div class="metric"><span>Status</span><strong id="status">-</strong></div>
    <div class="metric"><span>Progress / error</span><strong id="detail">-</strong></div>
  </section>
  <section id="audit"></section>
</main>
</body>
</html>"""


HARNESS_SCRIPT = """
() => {
  const coordinator = globalThis.__createCanvasSessionRequestCoordinator('session-a');
  const pending = new Map();
  const owners = new Map();
  const settled = new Set();
  const view = { sessionId: 'session-a', locked: false, status: 'idle', progress: 0, error: '' };
  let sequence = 0;

  const render = () => {
    document.querySelector('#session').textContent = view.sessionId;
    document.querySelector('#lock').textContent = view.locked ? 'locked' : 'open';
    document.querySelector('#status').textContent = view.status;
    document.querySelector('#detail').textContent = view.error || `${view.progress}%`;
    document.body.dataset.sessionId = view.sessionId;
    document.body.dataset.locked = String(view.locked);
    document.body.dataset.status = view.status;
    document.body.dataset.progress = String(view.progress);
    document.body.dataset.error = view.error;
  };

  globalThis.fetch = (input) => new Promise((resolve, reject) => {
    pending.set(String(input), { resolve, reject });
  });

  const activate = (sessionId) => {
    coordinator.activateSession(sessionId);
    view.sessionId = sessionId;
    view.locked = false;
    view.status = 'idle';
    view.progress = 0;
    view.error = '';
    render();
  };

  const start = (key, kind, sessionId) => {
    const owner = { sessionId, token: `${kind}-${++sequence}` };
    owners.set(key, owner);
    if (!coordinator.acquire(owner)) throw new Error(`owner rejected: ${key}`);
    view.locked = true;
    view.status = kind === 'cancel' ? 'cancelling' : kind === 'retry' ? 'queued' : 'running';
    view.progress = kind === 'poll' ? 25 : 0;
    view.error = '';
    render();
    void (async () => {
      try {
        const response = await fetch(`/deferred/${key}`);
        const payload = await response.json();
        if (!coordinator.isActive(owner)) return;
        view.status = payload.status;
        view.progress = payload.progress;
        view.error = payload.error || '';
        render();
      } catch (error) {
        if (!coordinator.isActive(owner)) return;
        view.error = error instanceof Error ? error.message : String(error);
        view.status = 'failed';
        render();
      } finally {
        if (coordinator.release(owner)) {
          view.locked = false;
          render();
        }
        settled.add(key);
      }
    })();
  };

  const resolve = (key, payload) => {
    const deferred = pending.get(`/deferred/${key}`);
    if (!deferred) throw new Error(`missing deferred request: ${key}`);
    pending.delete(`/deferred/${key}`);
    deferred.resolve({ ok: true, json: async () => payload });
  };

  const reject = (key, message) => {
    const deferred = pending.get(`/deferred/${key}`);
    if (!deferred) throw new Error(`missing deferred request: ${key}`);
    pending.delete(`/deferred/${key}`);
    deferred.reject(new Error(message));
  };

  addEventListener('canvas-runtime-state', (event) => {
    const { key, payload } = event.detail;
    const owner = owners.get(key);
    if (!owner || !coordinator.isActive(owner)) return;
    view.status = payload.status;
    view.progress = payload.progress;
    view.error = payload.error || '';
    render();
  });

  const emit = (key, payload) => dispatchEvent(new CustomEvent('canvas-runtime-state', { detail: { key, payload } }));
  const snapshot = () => ({ ...view, owner: coordinator.current() });
  const audit = (label) => {
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `<strong>${label}</strong><code>${JSON.stringify(snapshot())}</code><span class="pass">PASS</span>`;
    document.querySelector('#audit').appendChild(row);
  };
  const finish = () => { document.querySelector('#result').textContent = 'PASS'; document.querySelector('#result').className = 'pass'; };
  globalThis.__matrix = { activate, audit, emit, finish, owners, pending, reject, resolve, settled, snapshot, start };
  render();
}
"""


def state(page: Page) -> dict[str, object]:
    return page.evaluate("() => window.__matrix.snapshot()")


def wait_pending(page: Page, key: str) -> None:
    page.wait_for_function("key => window.__matrix.pending.has(`/deferred/${key}`)", arg=key)


def wait_settled(page: Page, key: str) -> None:
    page.wait_for_function("key => window.__matrix.settled.has(key)", arg=key)


def activate_and_start(page: Page, session_id: str, key: str, kind: str) -> None:
    page.evaluate("args => { window.__matrix.activate(args.sessionId); window.__matrix.start(args.key, args.kind, args.sessionId); }", {
        "sessionId": session_id,
        "key": key,
        "kind": kind,
    })
    wait_pending(page, key)


def resolve(page: Page, key: str, *, status: str, progress: int, error: str = "") -> None:
    page.evaluate("args => window.__matrix.resolve(args.key, args.payload)", {
        "key": key,
        "payload": {"status": status, "progress": progress, "error": error},
    })
    wait_settled(page, key)


def assert_b_isolated(value: dict[str, object], *, locked: bool, status: str, progress: int, error: str = "") -> None:
    assert value["sessionId"] == "session-b", value
    assert value["locked"] is locked, value
    assert value["status"] == status, value
    assert value["progress"] == progress, value
    assert value["error"] == error, value


def run_matrix(page: Page) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []

    # A poll resolves first. Its result and finally must not clear B's run owner.
    activate_and_start(page, "session-a", "poll-a", "poll")
    activate_and_start(page, "session-b", "run-b", "run")
    page.evaluate("() => window.__matrix.emit('poll-a', { status: 'failed', progress: 99, error: 'stale poll event' })")
    resolve(page, "poll-a", status="failed", progress=99, error="stale poll response")
    current = state(page)
    assert_b_isolated(current, locked=True, status="running", progress=0)
    evidence.append({"case": "poll A before run B", **current})
    page.evaluate("() => window.__matrix.audit('poll A before run B')")
    resolve(page, "run-b", status="succeeded", progress=100)

    # B poll resolves before A run. A's late response and event cannot regress B.
    activate_and_start(page, "session-a", "run-a", "run")
    activate_and_start(page, "session-b", "poll-b", "poll")
    resolve(page, "poll-b", status="succeeded", progress=100)
    resolve(page, "run-a", status="failed", progress=70, error="late A run")
    page.evaluate("() => window.__matrix.emit('run-a', { status: 'running', progress: 75, error: 'late A event' })")
    current = state(page)
    assert_b_isolated(current, locked=False, status="succeeded", progress=100)
    evidence.append({"case": "run A after poll B", **current})
    page.evaluate("() => window.__matrix.audit('run A after poll B')")

    # A retry rejects while B cancel is pending. The stale catch/finally cannot leak an error or unlock B.
    activate_and_start(page, "session-a", "retry-a", "retry")
    activate_and_start(page, "session-b", "cancel-b", "cancel")
    page.evaluate("() => window.__matrix.reject('retry-a', 'late A retry error')")
    wait_settled(page, "retry-a")
    current = state(page)
    assert_b_isolated(current, locked=True, status="cancelling", progress=0)
    evidence.append({"case": "retry A error before cancel B", **current})
    page.evaluate("() => window.__matrix.audit('retry A error before cancel B')")
    resolve(page, "cancel-b", status="cancelled", progress=100)

    # A cancel resolves after B retry. B remains authoritative.
    activate_and_start(page, "session-a", "cancel-a", "cancel")
    activate_and_start(page, "session-b", "retry-b", "retry")
    resolve(page, "retry-b", status="running", progress=45)
    resolve(page, "cancel-a", status="cancelled", progress=100)
    current = state(page)
    assert_b_isolated(current, locked=False, status="running", progress=45)
    evidence.append({"case": "cancel A after retry B", **current})
    page.evaluate("() => window.__matrix.audit('cancel A after retry B')")

    # Switching back to A reloads its authoritative terminal state.
    activate_and_start(page, "session-a", "reload-a", "poll")
    resolve(page, "reload-a", status="cancelled", progress=100)
    current = state(page)
    assert current["sessionId"] == "session-a", current
    assert current["locked"] is False, current
    assert current["status"] == "cancelled", current
    assert current["progress"] == 100, current
    assert current["error"] == "", current
    evidence.append({"case": "reload A authority", **current})
    page.evaluate("() => { window.__matrix.audit('reload A authority'); window.__matrix.finish(); }")

    # Returning to A must invalidate an older A request, even though the
    # session id is identical. Its late response/finally cannot clear the new
    # A owner.
    activate_and_start(page, "session-a", "old-a", "run")
    page.evaluate("() => window.__matrix.activate('session-b')")
    activate_and_start(page, "session-a", "return-a", "run")
    page.evaluate("() => window.__matrix.emit('old-a', { status: 'failed', progress: 3, error: 'stale return' })")
    resolve(page, "old-a", status="failed", progress=3, error="stale return")
    current = state(page)
    assert current["sessionId"] == "session-a", current
    assert current["locked"] is True, current
    assert current["status"] == "running", current
    assert current["progress"] == 0, current
    assert current["error"] == "", current
    evidence.append({"case": "return A invalidates old A", **current})
    page.evaluate("() => window.__matrix.audit('return A invalidates old A')")
    resolve(page, "return-a", status="succeeded", progress=100)
    current = state(page)
    assert current["locked"] is False, current
    assert current["status"] == "succeeded", current
    page.evaluate("() => { window.__matrix.finish(); }")
    return evidence


def run(args: argparse.Namespace) -> dict[str, object]:
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    desktop = report_dir / "canvas-session-race-desktop.png"
    mobile = report_dir / "canvas-session-race-mobile.png"
    edge = Path(args.edge).resolve()
    if not edge.exists():
        raise FileNotFoundError(f"Edge executable not found: {edge}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(edge), headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(HARNESS_HTML)
        page.add_script_tag(content=compile_owner_controller(), type="module")
        page.wait_for_function("() => typeof window.__createCanvasSessionRequestCoordinator === 'function'")
        page.evaluate(HARNESS_SCRIPT)
        evidence = run_matrix(page)
        page.screenshot(path=str(desktop), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(mobile), full_page=True)
        browser.close()
    assert not errors, errors
    return {
        "status": "passed",
        "cases": evidence,
        "pageErrors": errors,
        "screenshots": {"desktop": str(desktop), "mobile": str(mobile)},
    }


def main() -> None:
    default_edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    parser = argparse.ArgumentParser()
    parser.add_argument("--edge", default=str(default_edge))
    parser.add_argument("--report-dir", default=str(Path.home() / ".v8-agent-os/reports/canvas-session-race"))
    args = parser.parse_args()
    if not shutil.which("node"):
        raise RuntimeError("Node.js is required to compile the production request owner controller")
    print(json.dumps(run(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
