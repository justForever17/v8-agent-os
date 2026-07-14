from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
