from __future__ import annotations

import io
import json
import stat
import threading
import time
import zipfile
from pathlib import Path

import pytest

from core import extensions_modelscope as modelscope
from core import extensions_store_operations as operations
from core import extensions_store_service as store
from core import mcp_connection_setup as setup
from core import mcp_config_service as mcp
from core import skills_archive as archive
from core import skills_install_service as skills
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend, resolve_config_credential_refs


def package(root="bundle-r1/", text="first"):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as handle:
        for path, content in {"SKILL.md": "---\nname: shared-name\ndescription: fixture\n---\n" + text,
                              "scripts/run.py": "print('fixture')", "references/guide.md": text,
                              "assets/data.bin": "asset", "LICENSE": "fixture license"}.items():
            handle.writestr(root + path, content)
    return data.getvalue()


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "_target_root", lambda: tmp_path / "installed")
    monkeypatch.setattr(skills, "V8_AGENT_OS_HOME", tmp_path / "state")
    monkeypatch.setattr(operations, "V8_AGENT_OS_HOME", tmp_path / "state")
    monkeypatch.setattr("runtimes.extensions.skills.loader.SkillLoader.reload_skills", lambda: None)
    monkeypatch.setattr("core.audit_logger.audit_logger.log", lambda **kw: None)
    monkeypatch.setenv("V8_AGENT_OS_EXTENSIONS_STORE_CACHE_DIR", str(tmp_path / "cache"))
    credentials = CredentialRefStore(MemoryCredentialBackend())
    monkeypatch.setattr(setup, "credential_ref_store", credentials)
    monkeypatch.setattr(store.storage, "get_mcp_config", lambda: {"mcpServers": {}})
    return credentials


@pytest.mark.parametrize("path", ["../escape/SKILL.md", "/root/SKILL.md", "C:/root/SKILL.md",
    "//host/share/SKILL.md", "bundle/../SKILL.md", "bundle\\SKILL.md", "bundle/a:stream", "bundle/NUL", "bundle/a."])
def test_reject_archive_before_any_write(tmp_path, path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as handle:
        handle.writestr("bundle/SKILL.md", "good")
        handle.writestr(path, "bad")
    target = tmp_path / "extract"
    with pytest.raises(archive.ArchiveValidationError):
        archive.extract_archive(data.getvalue(), target)
    assert not target.exists()


def test_reject_links_duplicate_case_and_budget(monkeypatch, tmp_path):
    cases = [["bundle/SKILL.md", "bundle/SKILL.md"], ["bundle/A/a", "bundle/a/b"], ["bundle/a", "bundle/a/b"]]
    for names in cases:
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as handle:
            for name in names:
                handle.writestr(name, "payload")
        with pytest.raises(archive.ArchiveValidationError):
            archive.extract_archive(data.getvalue(), tmp_path / "out")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as handle:
        link = zipfile.ZipInfo("bundle/SKILL.md")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        handle.writestr(link, "../../outside")
    with pytest.raises(archive.ArchiveValidationError):
        archive.extract_archive(data.getvalue(), tmp_path / "out")
    monkeypatch.setattr(archive, "MAX_EXTRACTED_BYTES", 8)
    with pytest.raises(archive.ArchiveValidationError):
        archive.extract_archive(package(), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_root_package_complete_stable_identity_and_rollback(isolated, monkeypatch, tmp_path):
    identity = {"provider": "modelscope", "itemId": "author/skill", "revision": "r1"}
    first = skills.install_skills_from_zip("bundle.zip", package(""), identity=identity)
    target = Path(first["installed"][0]["path"])
    assert sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()) == [
        "LICENSE", "SKILL.md", "assets/data.bin", "references/guide.md", "scripts/run.py"]
    second = skills.install_skills_from_zip("bundle.zip", package("another-revision/"), identity=identity)
    assert second["skipped"][0]["path"] == str(target)
    other = skills.install_skills_from_zip("bundle.zip", package(), identity={**identity, "itemId": "other/skill"})
    assert other["installed"][0]["path"] != str(target)
    with pytest.raises(ValueError):
        skills.install_skills_from_zip("bundle.zip", package(text="changed"), identity=identity)
    assert (target / "references/guide.md").read_text() == "first"
    def fail_reload():
        raise OSError("synthetic loader failure")
    monkeypatch.setattr("runtimes.extensions.skills.loader.SkillLoader.reload_skills", fail_reload)
    with pytest.raises(OSError):
        skills.install_skills_from_zip("bundle.zip", package(text="changed"), identity=identity, overwrite=True)
    assert (target / "references/guide.md").read_text() == "first"
    assert skills.get_skill_receipt("modelscope", "author/skill")["revision"] == "r1"
    assert not list(tmp_path.glob(".v8-skill-install-*"))


def test_endpoint_and_secret_keep_replace_clear_and_failure(isolated, monkeypatch):
    marker = "SYNTHETIC_ENDPOINT_SECRET"
    config = {"type": "http", "url": f"https://fixture.invalid/dedicated/{marker}", "headers": {"Authorization": marker},
              "env": {"API_KEY": marker, "UNKNOWN": "keep"}, "futureSetting": {"preserve": True}}
    saved, refs = setup.secure_mcp_config(config)
    assert marker not in json.dumps(saved)
    assert len(refs) == 3
    assert mcp.validate_mcp_server_map({"demo": saved})["demo"]["endpointRef"]
    assert resolve_config_credential_refs(saved, store=isolated)["url"] == config["url"]
    kept, created = setup.secure_mcp_config({**saved, "headers": {"Authorization": "********"}})
    assert created == [] and kept == saved
    changed, _ = setup.secure_mcp_config({**saved, "url": "https://fixture.invalid/rotated", "headers": {"Authorization": "ROTATED"}})
    assert mcp.mcp_config_revision(saved) != mcp.mcp_config_revision(changed)
    ephemeral = resolve_config_credential_refs(changed, store=isolated)
    assert ephemeral["headers"]["Authorization"] == "ROTATED" and ephemeral["env"]["UNKNOWN"] == "keep"
    assert ephemeral["futureSetting"] == {"preserve": True}
    cleared = {**saved, "x-v8-credential-refs": {}}
    assert "Authorization" not in resolve_config_credential_refs(cleared, store=isolated)["headers"]
    error = RuntimeError(f"401 {config['url']} Authorization={marker}")
    assert marker not in setup.connection_failure(error)


def test_process_mirror_is_explicit_and_private_auth_is_rejected(isolated):
    original = {"command": "uvx", "args": ["mcp-server-fetch==2025.4.7"]}
    assert setup.process_mirror_environment(original) == {}
    domestic = {**original, "x-v8-package-source": "domestic"}
    assert set(setup.process_mirror_environment(domestic)) == {"UV_DEFAULT_INDEX"}
    assert "env" not in original
    assert setup.process_mirror_environment({**domestic, "command": "npx"}) == {"npm_config_registry": "https://registry.npmmirror.com"}
    with pytest.raises(ValueError):
        setup.process_mirror_environment({**domestic, "env": {"NPM_TOKEN": "fixture"}})
    assert "GitHub" in setup.connection_failure(RuntimeError("postinstall git+https://github.com/fixture"))


def test_modelscope_paging_body_and_hosted_are_separate(isolated, monkeypatch):
    calls = []
    def public(path, **kwargs):
        calls.append((path, kwargs))
        return {"mcp_server_list": [{"id": "author/fetch", "name": "same"}], "total_count": 2}
    monkeypatch.setattr(modelscope, "read_public", public)
    first = modelscope.list_items("mcp", query="fetch", limit=1, page=1, refresh=False)
    second = modelscope.list_items("mcp", query="fetch", limit=1, page=2, refresh=False)
    assert first["hasMore"] and not second["hasMore"]
    assert calls[0][1] == {"method": "PUT", "params": {"search": "fetch", "page_number": 1, "page_size": 1}}
    assert first["items"][0]["isHosted"] is None
    monkeypatch.setattr(modelscope, "_detail", lambda *a: {"id": "author/fetch", "is_hosted": True,
        "server_config": [{"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}]})
    detail = modelscope.mcp_detail("author/fetch")
    assert {c["transport"] for c in detail["candidates"]} == {"sse", "http", "stdio"}
    assert "_serverConfig" not in json.dumps(detail)
    assert all("deploy" not in path for path, _ in calls)


def wait_done(operation_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = operations.get_operation(operation_id)
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("operation did not finish")


def test_operation_dedupe_cancel_and_secret_free_restart(isolated, monkeypatch):
    release = threading.Event()
    called = []
    def install(payload):
        called.append(payload)
        assert release.wait(2)
        operations.checkpoint("publishing", can_cancel=False)
        raise AssertionError("cancel must stop before publish")
    payload = {"provider": "modelscope", "skillId": "author/skill", "values": {"secret": "SYNTHETIC_PRIVATE"}}
    first = operations.start_operation("skills", payload, install)
    again = operations.start_operation("skills", payload, install)
    assert first["operationId"] == again["operationId"]
    operations.cancel_operation(first["operationId"])
    release.set()
    result = wait_done(first["operationId"])
    assert result["status"] == "cancelled" and len(called) <= 1
    assert "SYNTHETIC_PRIVATE" not in operations._path(first["operationId"]).read_text(encoding="utf-8")
    record = json.loads(operations._path(first["operationId"]).read_text(encoding="utf-8"))
    record.update(owner="old-process", status="running")
    operations._save(record)
    assert operations.get_operation(first["operationId"])["status"] == "interrupted"
    assert len(called) <= 1


def test_npm_preparation_pins_integrity_and_preserves_runtime_arguments(isolated, monkeypatch, tmp_path):
    from core import mcp_dependency_setup as dependencies
    monkeypatch.setattr(dependencies, "V8_AGENT_OS_HOME", tmp_path)
    monkeypatch.setattr(dependencies.shutil, "which", lambda command: f"/runtime/{command}")
    monkeypatch.setattr(store, "_fetch_json", lambda url: {"version": "1.2.3", "dist": {"integrity": "sha512-fixture"}})
    calls = []
    def install(argv, environment, cwd):
        calls.append((argv, environment))
        package_dir = cwd / "node_modules" / "@fixture" / "mcp"
        package_dir.mkdir(parents=True)
        (package_dir / "package.json").write_text(json.dumps({"bin": {"mcp": "server.js"}}))
        (package_dir / "server.js").write_text("// fixture")
        (cwd / "package-lock.json").write_text(json.dumps({"packages": {"node_modules/@fixture/mcp": {"integrity": "sha512-fixture"}}}))
    monkeypatch.setattr(dependencies, "_run", install)
    original = {"command": "npx", "type": "stdio", "args": ["--yes", "@fixture/mcp", "allowed-dir"],
                "env": {"API_KEY": "SYNTHETIC_RUNTIME_ONLY"}, "x-v8-package-source": "domestic"}
    prepared = dependencies.prepare_stdio(original, target="provider:item")
    assert Path(prepared["args"][0]).is_file()
    assert prepared["args"][1:] == ["allowed-dir"]
    assert prepared["x-v8-package-receipt"]["version"] == "1.2.3"
    assert calls[0][0][-1] == "@fixture/mcp@1.2.3"
    assert calls[0][1]["npm_config_registry"] == "https://registry.npmmirror.com"
    assert "SYNTHETIC_RUNTIME_ONLY" not in json.dumps(calls)
    assert dependencies.prepare_stdio(original, target="provider:item") == prepared
    assert len(calls) == 1


def test_mcp_config_route_keeps_unknown_fields_and_rolls_back_secret_failure(isolated, monkeypatch):
    import asyncio
    from api import platform_routes
    state = {"mcpServers": {}}
    writes = []
    def save(config):
        state.clear(); state.update(config); writes.append(json.dumps(config))
    monkeypatch.setattr(mcp.storage, "get_mcp_config", lambda: state)
    monkeypatch.setattr(mcp.storage, "save_mcp_config", save)
    monkeypatch.setattr(mcp, "request_mcp_inventory_refresh", lambda reason: None)
    async def refreshed(**kwargs):
        return {"changed": True}
    monkeypatch.setattr(platform_routes.extensions_runtime_service, "refresh_inventory_if_changed", refreshed)
    monkeypatch.setattr(platform_routes.extensions_runtime_service, "build_health", lambda: {})
    marker = "SYNTHETIC_ROUTE_PRIVATE"
    asyncio.run(platform_routes.update_mcp_config({"mcpServers": {"fixture": {
        "type": "http", "url": f"https://fixture.invalid/{marker}", "headers": {"Authorization": marker},
        "env": {"API_KEY": marker, "UNKNOWN": "kept"}, "unknownSetting": {"keep": 7}}}}))
    original = json.loads(json.dumps(state["mcpServers"]["fixture"]))
    for payload in [original, {**original, "headers": {"Authorization": "********"}}]:
        asyncio.run(platform_routes.update_mcp_config({"mcpServers": {"fixture": payload}}))
        assert state["mcpServers"]["fixture"] == original
    changed = {**original, "headers": {"Authorization": "NEW_SYNTHETIC"}, "x-v8-edit-base": original}
    asyncio.run(platform_routes.update_mcp_config({"mcpServers": {"fixture": changed}}))
    assert state["mcpServers"]["fixture"]["unknownSetting"] == {"keep": 7}
    assert "x-v8-edit-base" not in writes[-1]
    assert all(marker not in text and "NEW_SYNTHETIC" not in text for text in writes)
    before = json.dumps(state)
    monkeypatch.setattr(mcp.storage, "save_mcp_config", lambda cfg: (_ for _ in ()).throw(OSError("synthetic disk error")))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        asyncio.run(platform_routes.update_mcp_config({"mcpServers": {"fixture": {
            **state["mcpServers"]["fixture"], "url": f"https://fixture.invalid/new/{marker}"}}}))
    assert marker not in str(failure.value.detail)
    assert json.dumps(state) == before


def test_hosted_setup_wait_survives_restart_without_deployment(isolated):
    payload = {"provider": "modelscope", "id": "author/hosted", "candidateId": "http", "waitForInput": True}
    operation = operations.start_operation("mcp", payload, lambda _: pytest.fail("must not deploy or connect"))
    assert operation["status"] == "awaiting_input"
    record = json.loads(operations._path(operation["operationId"]).read_text(encoding="utf-8"))
    record["owner"] = "old-process"
    operations._save(record)
    assert operations.get_operation(operation["operationId"])["status"] == "awaiting_input"


def test_private_transport_logger_strips_url_payload_and_traceback():
    import logging
    from runtimes.extensions.mcp.transport_logging import private_transport, PrivateTransportFilter
    marker = "SYNTHETIC_PRIVATE_ENDPOINT"
    record = logging.LogRecord("mcp.client.sse", logging.ERROR, __file__, 1,
        f"POST https://fixture.invalid/{marker}", (), (ValueError, ValueError(marker), None))
    token = private_transport.set(True)
    try:
        assert PrivateTransportFilter().filter(record)
        assert marker not in logging.Formatter().format(record)
        assert record.exc_info is None
    finally:
        private_transport.reset(token)
