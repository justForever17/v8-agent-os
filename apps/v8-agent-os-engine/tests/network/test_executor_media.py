"""Actual SQLite + ASGI uploads + existing artifact owner, no physical device."""
from __future__ import annotations

import base64
import builtins
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.client_identity.service import ClientIdentityService
from core.database import DatabaseManager
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from runtimes.network_supervisor.executors.media import jpeg_dimensions
from runtimes.network_supervisor.executors.protocol import ExecutorError, action
from runtimes.network_supervisor.executors.service import ExecutorService

# Locally generated solid RGB fixture, 32x24 baseline JPEG; no external/private image.
JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAYACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDhqKKK+jPDCiiigAooooAKKKKAP//Z")
PACKAGE = "com.v8agentos.executorfixture"


@pytest.fixture
def bench(tmp_path, monkeypatch):
    from api import device_executor_routes as routes
    from core import client_identity, workspace_authority
    clock = [1_800_000_000.0]
    identity = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()), clock=lambda: clock[0])
    owner = identity.owners.bootstrap(login="fixture", name="Fixture", now=clock[0])["id"]
    database = DatabaseManager(tmp_path / "runtime.db")
    database.create_or_update_session("session1", "Capture bench", user_id=owner)
    database.create_run_record(run_id="run1", session_id="session1", user_id=owner, run_type="chat", status="running")
    service = ExecutorService(identity, runtime_database=database)
    ticket = service.identities.ticket(owner, device_class="android", name="Phone fixture", base_url="https://example.invalid")
    device = service.enroll({**ticket, "deviceClass": "android"})
    principal = service.identities.verify(device["credential"])
    capabilities = [{"capability": cap, "resourceId": PACKAGE} for cap in ("android.observe", "android.capture", "android.action")]
    service.grant(owner, device["deviceId"], 1, capabilities)
    hello = {"type": "hello", "protocolVersion": 1, **{key: device[key] for key in ("deviceId", "authorityId")},
             "bootId": "boot1", "controlSessionId": "arm1", "capabilityRevision": 1, "capabilities": capabilities, "localEnabled": True}
    epoch = service.hello(principal, hello)["leaseEpoch"]
    monkeypatch.setattr(routes, "get_executor_service", lambda: service)
    monkeypatch.setattr(client_identity, "_service", identity)
    monkeypatch.setattr(workspace_authority.workspace_authority_service, "resolve", lambda **_: SimpleNamespace(workspace_root=str(tmp_path), workspace_id="", project_id=""))
    app = FastAPI()
    app.include_router(routes.router)
    headers = {"Authorization": "Bearer " + device["credential"]}
    with TestClient(app) as client:
        yield SimpleNamespace(service=service, identity=identity, owner=owner, database=database, device=device, principal=principal,
            capabilities=capabilities, hello=hello, epoch=epoch, clock=clock, client=client, headers=headers, root=tmp_path, app=app)


def command(b, name="capture1", capability="android.capture", arguments=None, precondition=None):
    result = b.service.create(owner=b.owner, command_id=name, device=b.device["deviceId"], capability=capability,
        resource=PACKAGE, arguments=arguments or {}, precondition=precondition or {}, ttl_ms=20000, trace={"runId": "run1", "episodeId": name})
    b.service.activate(b.owner, name)
    sent = b.service.outbound(b.device["deviceId"], b.epoch)
    assert len(sent) == 1
    c = result["command"]
    b.service.receipt(b.device["deviceId"], b.epoch, receipt(c, "started", 2))
    return c


def receipt(c, status, seq, **kwargs):
    return {"type": "receipt", "protocolVersion": 1, **{k: c[k] for k in (
        "commandId", "commandDigest", "deviceId", "authorityId", "bootId", "controlSessionId", "leaseEpoch", "grantRevision")},
        "status": status, "receiptSeq": seq, "deviceMonotonicMs": 100, **kwargs}


def manifest(b, c):
    sha = hashlib.sha256(JPEG).hexdigest()
    observation = {**{k: c[k] for k in ("deviceId", "bootId", "controlSessionId", "resourceId")},
        "observationId": "obs_" + c["commandId"], "appId": PACKAGE, "windowId": "window1", "geometryRevision": "geo1",
        "observedUnixMs": b.service.now(), "rotation": 0, "width": 32, "height": 24,
        "viewport": {"left": 0, "top": 0, "width": 320, "height": 240}, "captureScope": c["arguments"].get("scope", "window"),
        "availability": "screenshot_only", "frame": {"frameId": "frame_" + c["commandId"], "sha256": sha, "mimeType": "image/jpeg", "width": 32, "height": 24}}
    return {"commandId": c["commandId"], "commandDigest": c["commandDigest"], "observation": observation, "byteLength": len(JPEG), "sha256": sha, "mimeType": "image/jpeg"}


def stage(b, c):
    payload = manifest(b, c)
    response = b.client.post("/api/executor/media", json=payload, headers=b.headers)
    assert response.status_code == 200, response.text
    reservation = response.json()
    response = b.client.put(reservation["uploadPath"], content=JPEG, headers={**b.headers, "Content-Type": "image/jpeg"})
    assert response.status_code == 200, response.text
    observation = payload["observation"]
    observation["frame"]["mediaId"] = reservation["mediaId"]
    return reservation, observation


def finish(b, c):
    reservation, observation = stage(b, c)
    result = b.service.receipt(b.device["deviceId"], b.epoch, receipt(c, "succeeded", 3, observation=observation))
    assert result["businessVerification"] == "unverified"
    return reservation, observation, b.service.status(b.owner, c["commandId"])


def test_actual_upload_publishes_owned_image_only_after_receipt_and_reuses_artifact_owner(bench):
    b = bench
    c = command(b)
    reservation, observation = stage(b, c)
    assert not b.database.list_runtime_artifacts(session_id="session1")
    b.service.receipt(b.device["deviceId"], b.epoch, receipt(c, "succeeded", 3, observation=observation))
    result = b.service.status(b.owner, c["commandId"])
    artifact = result["artifacts"][0]
    assert artifact["sessionId"] == "session1" and artifact["runId"] == "run1"
    assert artifact["resourceRole"] == "source_derivative" and not artifact["autoAttachToMessage"]
    assert Path(artifact["sourcePath"]).read_bytes() == JPEG and jpeg_dimensions(JPEG) == (32, 24)
    assert artifact["mimeType"] == "image/jpeg" and "sessionId=session1" in artifact["contentUrl"]
    assert result["businessVerification"] == "unverified"
    assert b.service.status(b.owner, c["commandId"])["artifacts"][0]["artifactId"] == artifact["artifactId"]


@pytest.mark.parametrize("observation", [None, {}])
def test_capture_cannot_report_success_without_a_real_frame(bench, observation):
    b = bench; c = command(b)
    with pytest.raises(ExecutorError, match="capture_frame_required"):
        b.service.receipt(b.device["deviceId"], b.epoch, receipt(c, "succeeded", 3, observation=observation))
    assert b.service.status(b.owner, c["commandId"])["status"] == "started"
    assert not b.database.list_runtime_artifacts(session_id="session1")


@pytest.mark.parametrize("event", ["cancel", "revoke", "disconnect", "grant", "deadline"])
def test_late_upload_after_authority_change_cannot_publish_or_leave_bytes(bench, event):
    b = bench; c = command(b)
    payload = manifest(b, c)
    reserved = b.client.post("/api/executor/media", json=payload, headers=b.headers).json()
    if event == "cancel": b.service.cancel(b.owner, c["commandId"])
    if event == "revoke": b.service.revoke(b.owner, b.device["deviceId"])
    if event == "disconnect": b.service.disconnect(b.device["deviceId"], b.epoch)
    if event == "grant": b.service.grant(b.owner, b.device["deviceId"], 2, [])
    if event == "deadline": b.clock[0] += 21
    response = b.client.put(reserved["uploadPath"], content=JPEG, headers={**b.headers, "Content-Type": "image/jpeg"})
    assert response.status_code in (401, 403, 409)
    assert not b.database.list_runtime_artifacts(session_id="session1")
    assert not list(b.service.media.root.glob("*.jpg")) and not list(b.service.media.root.glob("*.upload"))


def test_stop_during_stream_aborts_staged_bytes(bench):
    b = bench; c = command(b)
    reserved = b.client.post("/api/executor/media", json=manifest(b, c), headers=b.headers).json()
    def chunks():
        yield JPEG[:100]
        b.service.cancel(b.owner, c["commandId"])
        yield JPEG[100:]
    response = b.client.put(reserved["uploadPath"], content=chunks(), headers={**b.headers, "Content-Type": "image/jpeg"})
    assert response.status_code == 409
    assert not list(b.service.media.root.glob("*.jpg")) and not list(b.service.media.root.glob("*.upload"))


def test_received_bytes_do_not_publish_after_late_cancel_or_forged_frame(bench):
    b = bench; c = command(b)
    _, observation = stage(b, c)
    tampered = json.loads(json.dumps(observation)); tampered["viewport"]["left"] = 1
    with pytest.raises(ExecutorError, match="frame_observation_mismatch"):
        b.service.receipt(b.device["deviceId"], b.epoch, receipt(c, "succeeded", 3, observation=tampered))
    b.service.cancel(b.owner, c["commandId"])
    with pytest.raises(ExecutorError, match="media_command_not_current"):
        b.service.receipt(b.device["deviceId"], b.epoch, receipt(c, "succeeded", 3, observation=observation))
    assert not b.database.list_runtime_artifacts(session_id="session1")


def test_frame_only_gesture_and_new_observation_replaces_old_frame(bench):
    b = bench; c = command(b)
    _, observation, _ = finish(b, c)
    precondition = {k: observation[k] for k in ("observationId", "deviceId", "bootId", "controlSessionId", "resourceId", "appId", "windowId", "geometryRevision", "rotation", "viewport", "width", "height")}
    precondition["frameId"] = observation["frame"]["frameId"]
    assert "nodeMapRevision" not in precondition
    gesture = b.service.create(owner=b.owner, command_id="tap1", device=b.device["deviceId"], capability="android.action", resource=PACKAGE,
        arguments={"action": "tap", "x": 12, "y": 8}, precondition=precondition, ttl_ms=5000, trace={"runId": "run1", "episodeId": "tap1"})
    assert gesture["status"] == "authorized"
    b.service.cancel(b.owner, "tap1")
    b.clock[0] += .001
    finish(b, command(b, "capture2"))
    with pytest.raises(ExecutorError, match="observation_superseded"):
        b.service.create(owner=b.owner, command_id="tap2", device=b.device["deviceId"], capability="android.action", resource=PACKAGE,
            arguments={"action": "tap", "x": 12, "y": 8}, precondition=precondition, ttl_ms=5000, trace={"runId": "run1", "episodeId": "tap2"})


def test_explicit_display_scope_requires_both_grants_and_cannot_authorize_gestures(bench):
    b = bench
    with pytest.raises(ExecutorError, match="display_capture_not_granted"):
        command(b, arguments={"scope": "display"})
    capabilities = b.capabilities + [{"capability": "android.capture", "resourceId": "display"}]
    b.service.grant(b.owner, b.device["deviceId"], 2, capabilities)
    b.epoch = b.service.hello(b.principal, {**b.hello, "capabilities": capabilities})["leaseEpoch"]
    _, obs, _ = finish(b, command(b, arguments={"scope": "display"}))
    pre = {k: v for k, v in obs.items() if k != "frame"}; pre["frameId"] = obs["frame"]["frameId"]
    with pytest.raises(ExecutorError, match="gesture_requires_window_capture"):
        command(b, "tap1", "android.action", {"action": "tap", "x": 10, "y": 10}, pre)


@pytest.mark.parametrize("mutation", ["spoof_hash", "size", "dimensions", "truncated", "metadata", "trailing"])
def test_corrupt_image_never_commits(bench, mutation):
    b = bench; c = command(b); payload = manifest(b, c); data = JPEG
    if mutation == "spoof_hash": payload["sha256"] = payload["observation"]["frame"]["sha256"] = "0" * 64
    if mutation == "size": data += b"extra"
    if mutation == "dimensions": payload["observation"]["width"] = payload["observation"]["frame"]["width"] = 31
    if mutation == "truncated": data = JPEG[:-2]
    if mutation == "metadata": data = JPEG[:2] + b"\xff\xfe\x00\x05abc" + JPEG[2:]
    if mutation == "trailing": data += b"<script>fixture</script>"
    if mutation in {"truncated", "metadata", "trailing"}:
        payload["byteLength"] = len(data); payload["sha256"] = payload["observation"]["frame"]["sha256"] = hashlib.sha256(data).hexdigest()
    reserved = b.client.post("/api/executor/media", json=payload, headers=b.headers).json()
    response = b.client.put(reserved["uploadPath"], content=data, headers={**b.headers, "Content-Type": "image/jpeg"})
    assert response.status_code in (400, 413)
    assert not list(b.service.media.root.glob("*.jpg")) and not list(b.service.media.root.glob("*.upload"))


def test_independent_credentials_cannot_upload_cross_device_or_as_human(bench):
    b = bench; c = command(b)
    reserved = b.client.post("/api/executor/media", json=manifest(b, c), headers=b.headers).json()
    human = b.identity.create_session(name="Human", surface="phone")
    assert b.client.put(reserved["uploadPath"], content=JPEG, headers={"Authorization": "Bearer " + human["accessToken"], "Content-Type": "image/jpeg"}).status_code == 401
    t = b.service.identities.ticket(b.owner, device_class="android", name="Other", base_url="https://example.invalid")
    other = b.service.enroll({**t, "deviceClass": "android"})
    assert b.client.put(reserved["uploadPath"], content=JPEG, headers={"Authorization": "Bearer " + other["credential"], "Content-Type": "image/jpeg"}).status_code == 404


def test_owner_deletion_and_expiry_never_recreate_published_artifact(bench):
    b = bench; c = command(b)
    reserved, _, result = finish(b, c)
    human = b.identity.create_session(name="Human", surface="phone")
    path = "/api/client/executors/media/" + reserved["mediaId"]
    assert b.client.delete(path, headers=b.headers).status_code == 401
    assert b.client.delete(path, headers={"Authorization": "Bearer " + human["accessToken"]}).status_code == 200
    assert b.service.status(b.owner, c["commandId"])["mediaStatus"] == "gone"
    assert not Path(result["artifacts"][0]["sourcePath"]).exists()
    finish(b, command(b, "capture2"))
    b.clock[0] += 86401
    b.service.media.cleanup()
    assert b.service.status(b.owner, "capture2")["mediaStatus"] == "gone"


def test_server_upload_and_artifact_registration_do_not_import_pillow_or_numpy(bench, monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name.split(".", 1)[0] not in {"PIL", "numpy"}, "server image path introduced an optional desktop dependency"
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    _, _, result = finish(bench, command(bench))
    assert result["mediaStatus"] == "available"


def test_real_client_artifact_read_requires_owned_session_not_executor_credential(bench, monkeypatch):
    from api import client_routes, session_workflow_routes
    b = bench
    b.app.state.client_database = b.database
    b.app.state.client_internal_router = session_workflow_routes.router
    b.app.include_router(client_routes.router)
    monkeypatch.setattr(session_workflow_routes, "db", b.database)
    monkeypatch.setattr(session_workflow_routes, "_memory_runtime", lambda: SimpleNamespace(get_artifact=b.database.get_runtime_artifact))
    _, _, result = finish(b, command(b))
    artifact = result["artifacts"][0]
    human = b.identity.create_session(name="Human", surface="phone")
    headers = {"Authorization": "Bearer " + human["accessToken"]}
    path = "/api/client/artifacts/" + artifact["artifactId"] + "/content?sessionId=session1"
    response = b.client.get(path, headers=headers)
    assert response.status_code == 200 and response.content == JPEG, response.text[:200]
    assert b.client.get(path, headers=b.headers).status_code == 401
    b.database.create_or_update_session("foreign", "Other", user_id="other-owner")
    # Existing client ownership hides a foreign session's existence with 404.
    assert b.client.get(path.replace("session1", "foreign"), headers=headers).status_code == 404
    status = b.client.get("/api/client/executors/commands/capture1", headers=headers).json()
    assert "/api/client/artifacts/" in status["artifacts"][0]["contentUrl"]


def test_image_budget_and_receipt_without_matching_upload_fail_closed(bench):
    b = bench; c = command(b); payload = manifest(b, c)
    huge = {**payload, "byteLength": 2 * 1024 * 1024 + 1}
    assert b.client.post("/api/executor/media", json=huge, headers=b.headers).status_code == 400
    obs = payload["observation"]; obs["frame"]["mediaId"] = "media_" + "0" * 32
    with pytest.raises(ExecutorError, match="media_frame_unavailable"):
        b.service.receipt(b.device["deviceId"], b.epoch, receipt(c, "succeeded", 3, observation=obs))
    assert b.service.status(b.owner, "capture1")["status"] == "started"


@pytest.mark.parametrize("pre", [
    {"rotation": 1}, {"width": 31}, {"geometryRevision": "old"}, {"frameId": "other"},
    {"viewport": {"left": 1, "top": 0, "width": 320, "height": 240}},
])
def test_frame_geometry_tampering_cannot_dispatch_gesture(bench, pre):
    b = bench; c = command(b); _, obs, _ = finish(b, c)
    supplied = {k: v for k, v in obs.items() if k != "frame"}
    supplied["frameId"] = obs["frame"]["frameId"]
    supplied.update(pre)
    with pytest.raises(ExecutorError, match="observation_target_stale|frame_mismatch"):
        command(b, "tap1", "android.action", {"action": "tap", "x": 0, "y": 0}, supplied)


def vision_context(b, monkeypatch):
    from core.creative_media_resource_authority import creative_media_resource_authority
    from core.tools import vision_image_inputs
    from core.runtime import startup_profile
    from runtimes.network_supervisor.executors import service as service_module
    monkeypatch.setenv("ENGINE_INSTALL_PROFILE", "server")
    # This fixture tests image authority/tool reachability, not installation
    # migration. Do not persist its server profile in the suite's shared home.
    monkeypatch.setattr(startup_profile, "ensure_runtime_registry_installation_state", lambda: None)
    monkeypatch.setattr(startup_profile.storage, "get_runtime_registry_config", lambda: {
        "installProfile": "server", "installedRuntimeFamilies": list(startup_profile.DEFAULT_RUNTIME_FAMILIES_BY_PROFILE["server"]),
        "featurePacks": {},
    })
    monkeypatch.setattr(creative_media_resource_authority, "_database", b.database)
    monkeypatch.setattr(service_module, "get_executor_service", lambda: b.service)
    # Workspace preflight still denies the external cache path; the real ledger
    # authority (not this preflight stub) must resolve the exact owned artifact.
    monkeypatch.setattr(vision_image_inputs, "resolve_workspace_tool_path", lambda path, **_: {
        "ok": False, "resolvedPath": str(path), "binding": {"activeWorkspaceRoot": str(b.root)}})
    return {"session_id": "session1", "run_id": "run1", "workspace_path": str(b.root)}


def test_server_vision_fixture_does_not_migrate_the_following_tests_profile(bench):
    from core.runtime import startup_profile
    from core.storage import storage
    from copy import deepcopy
    before = deepcopy(storage.read_json("runtime_registry.json"))
    checked = startup_profile._RUNTIME_REGISTRY_MIGRATION_CHECKED
    with pytest.MonkeyPatch.context() as isolated:
        vision_context(bench, isolated)
        assert startup_profile.get_configured_install_profile() == "server"
        assert not startup_profile.runtime_family_installed("creative_media")
    assert storage.read_json("runtime_registry.json") == before
    assert startup_profile._RUNTIME_REGISTRY_MIGRATION_CHECKED == checked


def test_server_real_vision_tool_preparation_keeps_exact_pixels_and_order_without_pillow(bench, monkeypatch):
    from core.tools.vision_image_inputs import prepare_ordered_images, ordered_image_content
    b = bench; context = vision_context(b, monkeypatch)
    _, _, result = finish(b, command(b))
    path = result["screenshotRef"]["filePath"]
    original = builtins.__import__
    def no_image_library(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"PIL", "numpy"}:
            raise ImportError("Server profile intentionally has no image decoder")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_image_library)
    prepared = prepare_ordered_images([{"file_path": path, "label": "first"}, {"file_path": path, "label": "second"}],
                                     runtime_context=context, remote_guard=lambda _: pytest.fail("unexpected remote upload"))
    content = ordered_image_content(prepared, prompt="Inspect fixture", api_standard="openai", provider_id="fixture", model_id="fixture-vision")
    actual = [part["image_url"]["url"] for part in content if part["type"] == "image_url"]
    assert len(actual) == 2 and all(base64.b64decode(url.split(",", 1)[1]) == JPEG for url in actual)
    assert [p["source"]["label"] for p in prepared] == ["first", "second"]
    assert all(p["source"]["inputWidth"] == 32 and not p["source"]["resized"] for p in prepared)
    assert all(p["source"]["resourceId"] == result["artifacts"][0]["artifactId"] for p in prepared)


@pytest.mark.parametrize("fault", ["cross_session", "expired", "replaced_bytes", "metadata_dimensions", "fake_source"])
def test_server_capture_vision_rejects_foreign_stale_or_forged_source(bench, monkeypatch, fault):
    from core.tools.vision_image_inputs import prepare_ordered_images, VisionImageInputError
    b = bench; context = vision_context(b, monkeypatch)
    _, _, result = finish(b, command(b))
    artifact = result["artifacts"][0]; path = Path(artifact["sourcePath"])
    if fault == "cross_session":
        b.database.create_or_update_session("session2", "Another session", user_id=b.owner)
        context["session_id"] = "session2"
    if fault == "expired": b.clock[0] += 86401
    if fault == "replaced_bytes":
        changed = bytearray(JPEG); changed[-4] ^= 1; path.write_bytes(changed)
    if fault == "metadata_dimensions":
        with b.database.get_connection() as db:
            metadata = {**artifact["metadata"], "width": 31}
            db.execute("UPDATE runtime_artifacts SET metadata_json=? WHERE id=?", (json.dumps(metadata), artifact["artifactId"]))
            db.commit()
    if fault == "fake_source":
        # Even the true session's persisted artifact flag cannot create a native
        # upload qualification without the matching executor ledger entry.
        with b.identity.transaction() as db:
            db.execute("UPDATE executor_media SET artifact_id='different' WHERE artifact_id=?", (artifact["artifactId"],))
    with pytest.raises(VisionImageInputError):
        prepare_ordered_images([{"file_path": str(path)}], runtime_context=context, remote_guard=lambda _: pytest.fail("unexpected network"))


@pytest.mark.parametrize("input_mode", ["images", "file_path"])
def test_server_existing_vision_tool_calls_selected_model_with_real_frame_and_budget(bench, monkeypatch, input_mode):
    from langchain_core.messages import AIMessage
    from core.tools import vision_media_analyzer as vision
    from core.storage import storage
    b = bench; context = vision_context(b, monkeypatch)
    from core.runtime_tool_access import runtime_tool_available
    assert runtime_tool_available("vision_media_analyzer") and runtime_tool_available("device_broker")
    _, _, captured = finish(b, command(b))
    from core.tool_surface import apply_tool_surface_budget
    from langchain_core.messages import ToolMessage
    projected = apply_tool_surface_budget(ToolMessage(name="device_broker", tool_call_id="capture-view", content=json.dumps(captured)), {"agentVisibleBudget": 6000})
    agent_data = json.loads(projected.content.split("\nData: ", 1)[1])
    path = agent_data["screenshotRef"]["filePath"]
    # Consume only the projected anchors in the next real admission.
    next_action = command(b, "from-agent-frame", "android.action", {"action": "tap", "x": 12, "y": 8}, agent_data["precondition"])
    assert next_action["resourceId"] == PACKAGE
    b.service.cancel(b.owner, "from-agent-frame")
    monkeypatch.setattr(vision, "get_runtime_context", lambda: context)
    monkeypatch.setattr(storage, "get_supervisor_config", lambda: {"compressedDirectImages": False})
    resolution = {"resolvedModelId": "fixture-vision", "resolvedProviderId": "fixture-api",
                  "resolvedModel": {"capabilityClass": "vision_multimodal"}, "resolvedProvider": {"type": "API", "api_standard": "openai"}}
    monkeypatch.setattr(vision.model_control_plane, "resolve_model_for_role", lambda _: resolution)
    monkeypatch.setattr(vision.model_control_plane, "get_config", lambda: {})
    order = []
    def budget(**kwargs):
        assert kwargs["run_id"] == "run1" and kwargs["model_id"] == "fixture-vision"
        order.append("budget")
    monkeypatch.setattr(vision.model_budget_service, "enforce_or_raise", budget)
    class Model:
        def invoke(self, messages, options):
            assert order == ["budget"]
            order.append("model")
            images = [p["image_url"]["url"] for p in messages[0].content if p["type"] == "image_url"]
            assert len(images) == 1 and base64.b64decode(images[0].split(",", 1)[1]) == JPEG
            assert options["metadata"]["images"][0]["sourceSha256"] == hashlib.sha256(JPEG).hexdigest()
            return AIMessage(content="Synthetic blue fixture inspected.")
    monkeypatch.setattr(vision.llm_factory, "create_for_role", lambda role, **_: Model() if role == "vision" else pytest.fail("wrong model role"))
    original = builtins.__import__
    def no_image_library(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"PIL", "numpy"}: raise ImportError("unavailable in Server profile")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_image_library)
    args = {"images": [{"file_path": path}]} if input_mode == "images" else {"file_path": path}
    result = vision.vision_media_analyzer.invoke({"type": "tool_call", "id": "vision-fixture", "name": "vision_media_analyzer", "args": {**args, "prompt": "Inspect this fixture"}})
    assert order == ["budget", "model"] and "Synthetic blue fixture inspected" in result.content
    assert "base64" not in result.content


@pytest.mark.parametrize("kind", ["image", "video", "remote", "array"])
def test_minimal_server_rejects_ordinary_media_before_model_or_download(bench, monkeypatch, kind):
    from core.tools import vision_media_analyzer as vision
    from core.runtime_tool_access import runtime_tool_available
    b = bench; context = vision_context(b, monkeypatch)
    monkeypatch.setattr(vision, "get_runtime_context", lambda: context)
    monkeypatch.setattr(vision.llm_factory, "create_for_role", lambda *_a, **_k: pytest.fail("unsupported media reached model"))
    monkeypatch.setattr(vision, "download_remote_image_bytes", lambda *_a, **_k: pytest.fail("unsupported media fetched"))
    monkeypatch.setattr(vision, "_mount_in_workspace", lambda *_a, **_k: pytest.fail("unsupported video mounted"))
    path = b.root / ("ordinary.jpg" if kind != "video" else "ordinary.mp4")
    path.write_bytes(JPEG if kind != "video" else b"synthetic video")
    from core.tools import vision_image_inputs
    monkeypatch.setattr(vision_image_inputs, "resolve_workspace_tool_path", lambda p, **_: {"ok": True, "resolvedPath": p, "binding": {"activeWorkspaceRoot": str(b.root)}})
    args = {"file_path": str(path)}
    if kind == "remote": args = {"source_url": "https://example.invalid/image.jpg"}
    if kind == "array": args = {"images": [{"file_path": str(path)}]}
    result = vision.vision_media_analyzer.invoke({"type": "tool_call", "id": "rejected", "name": "vision_media_analyzer", "args": args})
    assert "server_media_requires_creative_media" in result.content
    assert not runtime_tool_available("download_media_for_vision")
