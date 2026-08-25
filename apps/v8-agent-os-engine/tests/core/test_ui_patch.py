from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from core import ui_patch, workbench_files


@dataclass
class _Authority:
    workspace_root: str
    workspace_id: str = "workspace-1"
    project_id: str = "project-1"


@pytest.fixture()
def scoped_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    authority = _Authority(str(workspace))
    monkeypatch.setattr(ui_patch.workspace_authority_service, "resolve", lambda **_: authority)
    monkeypatch.setattr(workbench_files.workspace_authority_service, "resolve", lambda **_: authority)
    service = ui_patch.UiPatchService()
    service._runtime_root = tmp_path / "runtime" / "ui-patch"
    service._transactions_root = service._runtime_root / "transactions"
    try:
        yield service, workspace
    finally:
        service.shutdown()


def test_css_patch_preserves_rule_and_adds_whitelisted_property():
    source = ".card {\n  width: 100px;\n  color: red;\n}\n"

    patched = ui_patch._apply_rule_changes(
        source,
        ".card",
        {"width": "128px", "border-radius": "8px"},
    )

    assert "width: 128px;" in patched
    assert "border-radius: 8px;" in patched
    assert "color: red;" in patched


def test_css_patch_rejects_ambiguous_selector():
    source = ".card { color: red; }\n.card { color: blue; }\n"

    with pytest.raises(ValueError, match="ambiguous"):
        ui_patch._apply_rule_changes(source, ".card", {"color": "#111827"})


def test_style_value_allowlist_blocks_external_content_and_unknown_properties():
    with pytest.raises(ValueError, match="external content"):
        ui_patch._validate_changes({"background": "url(https://example.com/a.png)"})
    with pytest.raises(ValueError, match="Unsupported"):
        ui_patch._validate_changes({"transform": "scale(2)"})


def test_html_text_patch_matches_unique_selectors_and_escapes_markup():
    source = '<main><h1 id="hero" class="title primary">Original</h1></main>'

    assert len(ui_patch._html_element_spans(source, "#hero")) == 1
    assert len(ui_patch._html_element_spans(source, "h1.title:nth-of-type(1)")) == 1
    assert ui_patch._apply_html_text_change(source, "#hero", "Updated <safe>") == (
        '<main><h1 id="hero" class="title primary">Updated &lt;safe&gt;</h1></main>'
    )
    assert ui_patch._validate_changes({"__text_content": ""}) == {"__text_content": ""}


def test_static_preview_can_commit_and_undo_plain_html_text(scoped_service):
    service, workspace = scoped_service
    html = workspace / "index.html"
    html.write_text('<main><h1 id="hero">Original title</h1></main>', encoding="utf-8")
    if not ui_patch._resolve_node_executable():
        pytest.skip("Packaged or system Node.js is unavailable")

    preview = service.create_preview(
        session_id="session-text",
        parent_origin="http://127.0.0.1:9527",
        entry_path="index.html",
    )
    mapped = service.map_selection(
        session_id="session-text",
        patch_session_id=preview["patchSessionId"],
        selection={
            "selector": "#hero",
            "tagName": "h1",
            "label": "Original title",
            "textContent": "Original title",
            "computedStyles": {},
            "rules": [],
        },
    )

    assert mapped["writable"] is True
    assert mapped["textEditable"] is True
    assert mapped["sourceCandidates"][0]["sourceKind"] == "html_text"
    committed = service.commit(
        session_id="session-text",
        patch_session_id=preview["patchSessionId"],
        selection_ref=mapped["selectionRef"],
        candidate_id=mapped["sourceCandidates"][0]["candidateId"],
        changes={"__text_content": "Updated <safe>"},
    )
    assert "Updated &lt;safe&gt;" in html.read_text(encoding="utf-8")

    service.undo(session_id="session-text", transaction_id=committed["transactionId"])
    assert "Original title" in html.read_text(encoding="utf-8")


def test_transaction_id_rejects_path_like_values(scoped_service):
    service, _ = scoped_service

    with pytest.raises(ValueError, match="Invalid"):
        service._transaction_paths("../outside")


def test_static_preview_maps_source_commits_verifies_and_undoes(scoped_service):
    service, workspace = scoped_service
    html = workspace / "index.html"
    css = workspace / "styles.css"
    html.write_text(
        '<!doctype html><html><head><link rel="stylesheet" href="/styles.css"></head>'
        '<body><button class="card">Save</button></body></html>',
        encoding="utf-8",
    )
    css.write_text(".card {\n  width: 100px;\n  color: #111827;\n}\n", encoding="utf-8")
    (workspace / ".env").write_text("PRIVATE_TOKEN=must-not-be-served", encoding="utf-8")
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")

    preview = service.create_preview(
        session_id="session-1",
        parent_origin="http://127.0.0.1:9527",
        entry_path="index.html",
    )
    with httpx.Client(follow_redirects=True, timeout=5.0) as client:
        response = client.get(preview["previewUrl"])
        assert response.status_code == 200
        assert "/__v8_ui_patch__/bridge.js" in response.text
        assert "connect-src 'self'" in response.headers["content-security-policy"]
        secret_response = client.get(f"{preview['previewOrigin']}/.env")
        assert secret_response.status_code == 404

    mapped = service.map_selection(
        session_id="session-1",
        patch_session_id=preview["patchSessionId"],
        selection={
            "selector": ".card",
            "tagName": "button",
            "label": "Save",
            "computedStyles": {"width": "100px", "color": "rgb(17, 24, 39)"},
            "rules": [
                {
                    "selector": ".card",
                    "sourceHint": {"kind": "href", "value": f"{preview['previewOrigin']}/styles.css"},
                    "declarations": {"width": "100px", "color": "#111827"},
                }
            ],
        },
    )
    assert mapped["writable"] is True
    assert mapped["sourceCandidates"][0]["workspacePath"] == "styles.css"

    committed = service.commit(
        session_id="session-1",
        patch_session_id=preview["patchSessionId"],
        selection_ref=mapped["selectionRef"],
        candidate_id=mapped["sourceCandidates"][0]["candidateId"],
        changes={"width": "128px", "border-radius": "8px"},
    )
    assert "width: 128px;" in css.read_text(encoding="utf-8")
    assert "border-radius: 8px;" in css.read_text(encoding="utf-8")
    assert "styles.css" in committed["diff"]

    verification = service.record_verification(
        session_id="session-1",
        transaction_id=committed["transactionId"],
        status="verified",
        observed_styles={"width": "128px", "border-radius": "8px"},
    )
    assert verification["verificationStatus"] == "verified"

    undone = service.undo(session_id="session-1", transaction_id=committed["transactionId"])
    assert undone["state"] == "undone"
    restored = css.read_text(encoding="utf-8")
    assert "width: 100px;" in restored
    assert "border-radius" not in restored


def test_commit_blocks_when_source_changed_after_selection(scoped_service):
    service, workspace = scoped_service
    html = workspace / "index.html"
    css = workspace / "styles.css"
    html.write_text("<style>.card { width: 100px; }</style><div class='card'></div>", encoding="utf-8")
    css.write_text(".card { width: 100px; }", encoding="utf-8")
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")
    preview = service.create_preview(
        session_id="session-1",
        parent_origin="http://127.0.0.1:9527",
        entry_path="index.html",
    )
    mapped = service.map_selection(
        session_id="session-1",
        patch_session_id=preview["patchSessionId"],
        selection={
            "selector": ".card",
            "rules": [
                {
                    "selector": ".card",
                    "sourceHint": {"kind": "href", "value": f"{preview['previewOrigin']}/styles.css"},
                    "declarations": {"width": "100px"},
                }
            ],
        },
    )
    css.write_text(".card { width: 110px; }", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed after selection"):
        service.commit(
            session_id="session-1",
            patch_session_id=preview["patchSessionId"],
            selection_ref=mapped["selectionRef"],
            candidate_id=mapped["sourceCandidates"][0]["candidateId"],
            changes={"width": "128px"},
        )


def test_commit_requires_durable_undo_checkpoint_before_source_write(scoped_service, monkeypatch: pytest.MonkeyPatch):
    service, workspace = scoped_service
    html = workspace / "index.html"
    css = workspace / "styles.css"
    html.write_text('<link rel="stylesheet" href="/styles.css"><div class="card"></div>', encoding="utf-8")
    css.write_text(".card { width: 100px; }", encoding="utf-8")
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")
    preview = service.create_preview(
        session_id="session-1",
        parent_origin="http://127.0.0.1:9527",
        entry_path="index.html",
    )
    mapped = service.map_selection(
        session_id="session-1",
        patch_session_id=preview["patchSessionId"],
        selection={
            "selector": ".card",
            "rules": [{
                "selector": ".card",
                "sourceHint": {"kind": "href", "value": f"{preview['previewOrigin']}/styles.css"},
                "declarations": {"width": "100px"},
            }],
        },
    )
    monkeypatch.setattr(service, "_write_transaction", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(RuntimeError, match="undo checkpoint"):
        service.commit(
            session_id="session-1",
            patch_session_id=preview["patchSessionId"],
            selection_ref=mapped["selectionRef"],
            candidate_id=mapped["sourceCandidates"][0]["candidateId"],
            changes={"width": "128px"},
        )

    assert css.read_text(encoding="utf-8") == ".card { width: 100px; }"


def test_dev_target_must_be_loopback(scoped_service):
    service, _ = scoped_service

    with pytest.raises(ValueError, match="loopback"):
        service.create_preview(
            session_id="session-1",
            parent_origin="http://127.0.0.1:9527",
            target_url="https://example.com",
        )


def test_preview_spawn_failure_does_not_leave_secret_config(scoped_service, monkeypatch: pytest.MonkeyPatch):
    service, workspace = scoped_service
    (workspace / "index.html").write_text("<main>preview</main>", encoding="utf-8")
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")
    monkeypatch.setattr(ui_patch.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")))

    with pytest.raises(RuntimeError, match="could not start"):
        service.create_preview(
            session_id="session-1",
            parent_origin="http://127.0.0.1:9527",
            entry_path="index.html",
        )

    assert list(service._runtime_root.rglob("proxy-config.json")) == []


def test_compiled_stylesheet_paths_remain_read_only(scoped_service):
    service, workspace = scoped_service
    html = workspace / "index.html"
    compiled = workspace / ".next" / "static" / "compiled.css"
    compiled.parent.mkdir(parents=True)
    html.write_text('<div class="card"></div>', encoding="utf-8")
    compiled.write_text(".card { width: 100px; }", encoding="utf-8")
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")
    preview = service.create_preview(
        session_id="session-1",
        parent_origin="http://127.0.0.1:9527",
        entry_path="index.html",
    )

    mapped = service.map_selection(
        session_id="session-1",
        patch_session_id=preview["patchSessionId"],
        selection={
            "selector": ".card",
            "rules": [{
                "selector": ".card",
                "sourceHint": {"kind": "next", "value": str(compiled)},
                "declarations": {"width": "100px"},
            }],
        },
    )

    assert mapped["writable"] is False
    assert mapped["sourceCandidates"] == []


def test_vite_source_hint_maps_original_workspace_css(scoped_service):
    service, workspace = scoped_service
    html = workspace / "index.html"
    source_css = workspace / "src" / "card.css"
    source_css.parent.mkdir(parents=True)
    html.write_text('<div class="card"></div>', encoding="utf-8")
    source_css.write_text(".card { width: 100px; }", encoding="utf-8")
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")
    preview = service.create_preview(
        session_id="session-1",
        parent_origin="http://127.0.0.1:9527",
        entry_path="index.html",
    )

    mapped = service.map_selection(
        session_id="session-1",
        patch_session_id=preview["patchSessionId"],
        selection={
            "selector": ".card",
            "rules": [{
                "selector": ".card",
                "sourceHint": {"kind": "vite", "value": str(source_css)},
                "declarations": {"width": "100px"},
            }],
        },
    )

    assert mapped["writable"] is True
    assert mapped["sourceCandidates"][0]["workspacePath"] == "src/card.css"


def test_local_dev_proxy_injects_bridge_without_exposing_workspace(scoped_service):
    service, workspace = scoped_service
    (workspace / "index.html").write_text(
        '<!doctype html><html><head><link rel="stylesheet" href="/styles.css"></head><body><main class="app">Dev page</main></body></html>',
        encoding="utf-8",
    )
    (workspace / "styles.css").write_text(".app { padding: 16px; }", encoding="utf-8")
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")

    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: _QuietHandler(*args, directory=str(workspace), **kwargs),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        preview = service.create_preview(
            session_id="session-1",
            parent_origin="http://127.0.0.1:9527",
            target_url=f"http://127.0.0.1:{server.server_port}/",
        )
        with httpx.Client(follow_redirects=True, timeout=5.0) as client:
            response = client.get(preview["previewUrl"])
            assert response.status_code == 200
            assert "/__v8_ui_patch__/bridge.js" in response.text
            stylesheet = client.get(f"{preview['previewOrigin']}/styles.css")
            assert stylesheet.status_code == 200
            assert ".app" in stylesheet.text
        assert str(workspace) not in repr(preview)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_local_dev_proxy_blocks_redirects_outside_selected_origin(scoped_service):
    service, _ = scoped_service
    if not shutil.which("node"):
        pytest.skip("Node.js is unavailable")

    class _RedirectHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "https://example.com/escaped")
            self.end_headers()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        preview = service.create_preview(
            session_id="session-1",
            parent_origin="http://127.0.0.1:9527",
            target_url=f"http://127.0.0.1:{server.server_port}/",
        )
        with httpx.Client(follow_redirects=True, timeout=5.0) as client:
            response = client.get(preview["previewUrl"])
        assert response.status_code == 502
        assert "outside the selected local development origin" in response.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_project_inspection_resolves_nearest_package_and_fixed_dev_command(scoped_service):
    service, workspace = scoped_service
    project = workspace / "apps" / "demo"
    source = project / "src" / "App.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("export default () => <main />", encoding="utf-8")
    (project / "package.json").write_text(
        '{"scripts":{"dev":"vite --host 127.0.0.1"},"dependencies":{"react":"19"},"devDependencies":{"vite":"7"}}',
        encoding="utf-8",
    )
    (project / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    result = service.inspect_project(session_id="session-project", project_path="apps/demo/src/App.tsx")

    assert result["projectPath"] == "apps/demo"
    assert result["framework"] == "react"
    assert result["packageManager"] == "pnpm"
    assert result["devCommand"] == "pnpm run dev"
    assert "react-jsx-inline-style" in result["sourceAdapters"]
    assert result["dynamicBindings"] == "read_only"


def test_project_inspection_rejects_missing_dev_script_and_outside_workspace(scoped_service, tmp_path: Path):
    service, workspace = scoped_service
    (workspace / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")

    with pytest.raises(ValueError, match="scripts.dev"):
        service.inspect_project(session_id="session-project", project_path=".")
    with pytest.raises(PermissionError, match="inside"):
        service.inspect_project(session_id="session-project", project_path=str(outside))


def test_project_inspection_rejects_workspace_without_side_effect_authority(scoped_service, monkeypatch: pytest.MonkeyPatch):
    service, workspace = scoped_service
    (workspace / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
    blocked_authority = SimpleNamespace(
        workspace_root=str(workspace),
        workspace_id="workspace-blocked",
        project_id="project-blocked",
        side_effects_allowed=False,
    )
    monkeypatch.setattr(ui_patch.workspace_authority_service, "resolve", lambda **_: blocked_authority)

    with pytest.raises(PermissionError, match="does not allow"):
        service.inspect_project(session_id="session-project", project_path=".")


def test_project_dev_start_uses_terminal_session_and_proven_output_url(scoped_service, monkeypatch: pytest.MonkeyPatch):
    service, workspace = scoped_service
    (workspace / "package.json").write_text(
        '{"scripts":{"dev":"vite"},"devDependencies":{"vite":"7"}}',
        encoding="utf-8",
    )
    (workspace / "package-lock.json").write_text("{}", encoding="utf-8")
    sent: list[tuple[str, str]] = []
    terminated: list[str] = []

    monkeypatch.setattr("core.client_terminal_broker.create_terminal_session", lambda **_: {"sessionId": "term_project"})
    monkeypatch.setattr(
        "core.client_terminal_broker.send_terminal_input",
        lambda session_id, value: sent.append((session_id, value)) or {"ok": True},
    )
    monkeypatch.setattr(
        "core.client_terminal_broker.read_terminal_session",
        lambda _session_id: {"isRunning": True, "outputDelta": "Local: http://localhost:4317/\n"},
    )
    monkeypatch.setattr(
        "core.client_terminal_broker.terminate_terminal_session",
        lambda session_id: terminated.append(session_id) or {"ok": True},
    )
    monkeypatch.setattr(service, "_probe_local_url", lambda url: url == "http://127.0.0.1:4317")

    project, target_url, terminal_id = service._start_project_dev(session_id="session-project", project_path=".")

    assert project["devCommand"] == "npm run dev"
    assert target_url == "http://127.0.0.1:4317"
    assert terminal_id == "term_project"
    assert sent == [("term_project", "npm run dev" + ("\r" if ui_patch.os.name == "nt" else "\n"))]
    assert terminated == []


def test_project_dev_start_terminates_owned_session_when_readiness_is_unproven(scoped_service, monkeypatch: pytest.MonkeyPatch):
    service, workspace = scoped_service
    (workspace / "package.json").write_text('{"scripts":{"dev":"vite"},"devDependencies":{"vite":"7"}}', encoding="utf-8")
    terminated: list[str] = []
    monkeypatch.setattr(ui_patch, "PROJECT_DEV_START_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("core.client_terminal_broker.create_terminal_session", lambda **_: {"sessionId": "term_project"})
    monkeypatch.setattr("core.client_terminal_broker.send_terminal_input", lambda *_args: {"ok": True})
    monkeypatch.setattr("core.client_terminal_broker.read_terminal_session", lambda _session_id: {"isRunning": False, "outputDelta": "failed"})
    monkeypatch.setattr(
        "core.client_terminal_broker.terminate_terminal_session",
        lambda session_id: terminated.append(session_id) or {"ok": True},
    )

    with pytest.raises(RuntimeError, match="did not become ready"):
        service._start_project_dev(session_id="session-project", project_path=".")
    assert terminated == ["term_project"]


def test_project_preview_replacement_closes_existing_proxy_before_dev_start(scoped_service, monkeypatch: pytest.MonkeyPatch):
    service, workspace = scoped_service
    if not ui_patch._resolve_node_executable():
        pytest.skip("Node.js is unavailable")
    (workspace / "index.html").write_text("<main>preview</main>", encoding="utf-8")
    (workspace / "package.json").write_text('{"scripts":{"dev":"vite"},"devDependencies":{"vite":"7"}}', encoding="utf-8")
    existing = service.create_preview(
        session_id="session-project",
        parent_origin="http://127.0.0.1:9527",
        entry_path="index.html",
    )
    existing_process = service._sessions[existing["patchSessionId"]].process
    terminated: list[str] = []

    def fake_start_project_dev(**_kwargs):
        assert existing_process.poll() is not None
        return service.inspect_project(session_id="session-project", project_path="."), "http://127.0.0.1:4317", "term_replacement"

    monkeypatch.setattr(service, "_start_project_dev", fake_start_project_dev)
    monkeypatch.setattr(service, "_terminate_dev_session", lambda terminal_id: terminated.append(terminal_id))

    replacement = service.create_preview(
        session_id="session-project",
        parent_origin="http://127.0.0.1:9527",
        project_path=".",
        start_dev_server=True,
    )

    with pytest.raises(LookupError):
        service.get_preview(session_id="session-project", patch_session_id=existing["patchSessionId"])
    assert replacement["mode"] == "project"
    service.close_preview(session_id="session-project", patch_session_id=replacement["patchSessionId"])
    assert terminated == ["term_replacement"]


def test_project_proxy_spawn_failure_terminates_owned_dev_session(scoped_service, monkeypatch: pytest.MonkeyPatch):
    service, workspace = scoped_service
    if not ui_patch._resolve_node_executable():
        pytest.skip("Node.js is unavailable")
    (workspace / "package.json").write_text('{"scripts":{"dev":"vite"},"devDependencies":{"vite":"7"}}', encoding="utf-8")
    project = service.inspect_project(session_id="session-project", project_path=".")
    terminated: list[str] = []
    monkeypatch.setattr(service, "_start_project_dev", lambda **_kwargs: (project, "http://127.0.0.1:4317", "term_failed_proxy"))
    monkeypatch.setattr(service, "_terminate_dev_session", lambda terminal_id: terminated.append(terminal_id))
    monkeypatch.setattr(ui_patch.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")))

    with pytest.raises(RuntimeError, match="could not start"):
        service.create_preview(
            session_id="session-project",
            parent_origin="http://127.0.0.1:9527",
            project_path=".",
            start_dev_server=True,
        )
    assert terminated == ["term_failed_proxy"]


def test_vue_sfc_scoped_style_maps_commits_and_undoes(scoped_service):
    service, workspace = scoped_service
    html_path = workspace / "index.html"
    vue_path = workspace / "src" / "App.vue"
    vue_path.parent.mkdir(parents=True)
    html_path.write_text('<main><div class="card">Hello</div></main>', encoding="utf-8")
    vue_path.write_text(
        '<template><div class="card">Hello</div></template>\n<style scoped>\n.card { width: 100px; color: red; }\n</style>\n',
        encoding="utf-8",
    )
    preview = service.create_preview(
        session_id="session-vue",
        parent_origin="http://127.0.0.1:9527",
        entry_path="index.html",
    )
    mapped = service.map_selection(
        session_id="session-vue",
        patch_session_id=preview["patchSessionId"],
        selection={
            "selector": ".card[data-v-a1b2c3]",
            "tagName": "div",
            "textContent": "Hello",
            "computedStyles": {"width": "100px"},
            "rules": [{
                "selector": ".card[data-v-a1b2c3]",
                "sourceHint": {"kind": "vite", "value": f"{vue_path}?vue&type=style&index=0&scoped=a1b2c3&lang.css"},
                "declarations": {"width": "100px"},
            }],
        },
    )
    candidate = next(item for item in mapped["sourceCandidates"] if item["sourceKind"] == "vue_style")
    committed = service.commit(
        session_id="session-vue",
        patch_session_id=preview["patchSessionId"],
        selection_ref=mapped["selectionRef"],
        candidate_id=candidate["candidateId"],
        changes={"width": "144px"},
    )

    assert committed["selector"] == ".card[data-v-a1b2c3]"
    assert "width: 144px" in vue_path.read_text(encoding="utf-8")
    service.undo(session_id="session-vue", transaction_id=committed["transactionId"])
    assert "width: 100px" in vue_path.read_text(encoding="utf-8")


def test_css_module_runtime_selector_maps_back_to_source_class(scoped_service):
    service, workspace = scoped_service
    html_path = workspace / "index.html"
    css_path = workspace / "src" / "card.module.css"
    css_path.parent.mkdir(parents=True)
    html_path.write_text('<div class="card_a1b2c">Hello</div>', encoding="utf-8")
    css_path.write_text(".card { width: 100px; }\n", encoding="utf-8")
    preview = service.create_preview(session_id="session-css-module", parent_origin="http://127.0.0.1:9527", entry_path="index.html")
    mapped = service.map_selection(
        session_id="session-css-module",
        patch_session_id=preview["patchSessionId"],
        selection={
            "selector": ".card_a1b2c",
            "computedStyles": {"width": "100px"},
            "rules": [{
                "selector": ".card_a1b2c",
                "sourceHint": {"kind": "vite", "value": str(css_path)},
                "declarations": {"width": "100px"},
            }],
        },
    )

    assert mapped["sourceCandidates"][0]["selector"] == ".card"
    assert mapped["sourceCandidates"][0]["runtimeSelector"] == ".card_a1b2c"


def test_react_static_inline_style_and_text_are_separate_writable_candidates(scoped_service):
    service, workspace = scoped_service
    html_path = workspace / "index.html"
    react_path = workspace / "src" / "App.tsx"
    react_path.parent.mkdir(parents=True)
    html_path.write_text('<div class="card">Hello</div>', encoding="utf-8")
    react_path.write_text(
        'export function App() { return <div className="card" style={{ width: "100px", color: "#111827" }}>Hello</div>; }\n',
        encoding="utf-8",
    )
    preview = service.create_preview(session_id="session-react", parent_origin="http://127.0.0.1:9527", target_url="http://127.0.0.1:4317")
    mapped = service.map_selection(
        session_id="session-react",
        patch_session_id=preview["patchSessionId"],
        selection={
            "selector": ".card",
            "textContent": "Hello",
            "inlineStyle": {"width": "100px", "color": "rgb(17, 24, 39)"},
            "computedStyles": {"width": "100px"},
            "rules": [],
        },
    )
    by_kind = {item["sourceKind"]: item for item in mapped["sourceCandidates"]}
    assert {"react_inline_style", "component_text"}.issubset(by_kind)

    style_commit = service.commit(
        session_id="session-react",
        patch_session_id=preview["patchSessionId"],
        selection_ref=mapped["selectionRef"],
        candidate_id=by_kind["react_inline_style"]["candidateId"],
        changes={"width": "128px", "border-radius": "8px"},
    )
    assert 'width: "128px"' in react_path.read_text(encoding="utf-8")
    assert 'borderRadius: "8px"' in react_path.read_text(encoding="utf-8")
    service.undo(session_id="session-react", transaction_id=style_commit["transactionId"])

    mapped = service.map_selection(
        session_id="session-react",
        patch_session_id=preview["patchSessionId"],
        selection={"selector": ".card", "textContent": "Hello", "inlineStyle": {"width": "100px"}, "rules": []},
    )
    text_candidate = next(item for item in mapped["sourceCandidates"] if item["sourceKind"] == "component_text")
    service.commit(
        session_id="session-react",
        patch_session_id=preview["patchSessionId"],
        selection_ref=mapped["selectionRef"],
        candidate_id=text_candidate["candidateId"],
        changes={"__text_content": "Updated <safe>"},
    )
    assert "Updated &lt;safe&gt;" in react_path.read_text(encoding="utf-8")


def test_react_dynamic_inline_style_stays_read_only(scoped_service):
    service, workspace = scoped_service
    (workspace / "index.html").write_text('<div class="card">Hello</div>', encoding="utf-8")
    source = workspace / "src" / "App.tsx"
    source.parent.mkdir(parents=True)
    source.write_text('export const App = ({ size }) => <div className="card" style={{ width: size }}>Hello</div>;\n', encoding="utf-8")
    preview = service.create_preview(session_id="session-dynamic", parent_origin="http://127.0.0.1:9527", target_url="http://127.0.0.1:4317")
    mapped = service.map_selection(
        session_id="session-dynamic",
        patch_session_id=preview["patchSessionId"],
        selection={"selector": ".card", "textContent": "Hello", "inlineStyle": {"width": "100px"}, "rules": []},
    )

    assert all(item["sourceKind"] != "react_inline_style" for item in mapped["sourceCandidates"])
    assert any(item["sourceKind"] == "component_text" for item in mapped["sourceCandidates"])
