from __future__ import annotations

import json
import re

import pytest
from langchain_core.messages import ToolMessage

from core.tool_surface import apply_tool_surface_budget
from core.tool_observation_detail import render_tool_observation_detail
from erc.runtime_context import bind_runtime_context


def project(payload, budget=6000):
    return apply_tool_surface_budget(ToolMessage(name="device_broker", tool_call_id="device-fixture", content=json.dumps(payload)),
        {"sessionId": "surface-session", "runId": "surface-run", "agentVisibleBudget": budget})


def data(message):
    return json.loads(message.content.split("\nData: ", 1)[1])


@pytest.fixture
def observations(tmp_path, monkeypatch):
    from core.observability_db import ObservabilityDatabaseManager
    db = ObservabilityDatabaseManager(tmp_path / "observations.db")
    monkeypatch.setattr("core.observability_db.observability_db", db)
    return db


def test_list_retains_exact_available_device_resource_and_grant_intersection(observations):
    grant = {"capability": "android.capture", "resourceId": "com.example.fixture"}
    devices = [{"deviceId": "d1", "online": True, "revoked": False, "grantRevision": 2,
                "grants": [grant], "capabilities": [grant, {"capability": "android.action", "resourceId": "com.example.fixture"}],
                "credential": "SYNTHETIC_DEVICE_SECRET"}]
    shown = project({"items": devices})
    assert data(shown)["items"][0] == {k: v for k, v in devices[0].items() if k != "credential"}
    assert "SYNTHETIC_DEVICE_SECRET" not in shown.content


@pytest.mark.parametrize("status", ["queued", "succeeded", "unknown_outcome", "cancelled", "failed"])
def test_command_identity_and_non_business_success_survive_minimum_budget(observations, status):
    payload = {"commandId": "cmd", "episodeId": "episode", "deviceId": "device", "resourceId": "com.example.fixture",
               "status": status, "businessVerification": "unverified", "reason": "large reason " * 2000}
    shown = project(payload, 1200)
    parsed = data(shown)
    assert len(shown.content) <= 1200
    assert all(parsed[k] == payload[k] for k in ("commandId", "episodeId", "deviceId", "resourceId", "status", "businessVerification"))
    assert parsed["statusQuery"] == {"mode": "status", "command_id": "cmd"}
    if status == "unknown_outcome":
        assert "不要重新执行" in shown.content and "设备驱动已完成" not in shown.content


def test_large_node_map_and_secrets_have_lossless_redacted_paged_recovery(observations):
    nodes = [{"nodeId": f"n{i}", "text": "node content " * 80, "token": "SYNTHETIC_SECRET_0123456789"} for i in range(35)]
    payload = {"commandId": "cmd", "status": "succeeded", "businessVerification": "unverified",
               "receipt": {"observation": {"observationId": "o1", "deviceId": "d", "bootId": "b", "controlSessionId": "s",
                   "resourceId": "fixture", "appId": "fixture", "windowId": "1", "nodeMapRevision": "1", "nodes": nodes}},
               "traceRef": {"secret": "SYNTHETIC_SECRET_0123456789"}}
    shown = project(payload, 1200)
    parsed = data(shown)
    assert len(shown.content) <= 1200 and "SYNTHETIC_SECRET" not in shown.content
    assert "nodes" not in parsed.get("observation", {}) and parsed.get("nodesOmitted", parsed.get("evidenceOmitted"))
    ref = parsed["detailRef"]
    retained = observations.get_tool_observation_record(ref)
    assert json.loads(retained["raw_body_text"]) == payload
    pages, offset = [], 0
    while True:
        text = render_tool_observation_detail(ref, max_chars=700, start_char=offset)
        assert "SYNTHETIC_SECRET" not in text and "Fragment only" in text
        pages.append(text.split("<preview>\n", 1)[1].split("\n</preview>", 1)[0])
        following = re.search(r"next_start_char=(\d+)", text)
        if not following: break
        offset = int(following.group(1))
    recovered = json.loads("".join(pages))
    assert len(recovered["receipt"]["observation"]["nodes"]) == 35
    assert recovered["receipt"]["observation"]["nodes"][-1]["nodeId"] == "n34"
    assert recovered["traceRef"]["secret"] == "<redacted>"


def test_incomplete_frame_never_invents_action_anchors(observations):
    shown = project({"status": "succeeded", "businessVerification": "unverified",
        "receipt": {"observation": {"frame": {"frameId": "f"}, "width": 320, "height": 240}}})
    assert not data(shown)["preconditionComplete"] and "precondition" not in data(shown)


@pytest.mark.parametrize("status,expected", [
    ("authorized", "running"), ("queued", "running"), ("sent", "running"), ("received", "running"), ("started", "running"),
    ("succeeded", "completed"), ("failed", "failed"), ("rejected", "blocked"), ("cancelled", "terminated"),
    ("expired", "timed_out"), ("unknown_outcome", "unknown"),
])
def test_every_executor_protocol_state_is_classified_from_original_facts_after_projection(observations, status, expected):
    from runtimes.chat.runtime import ChatRuntime
    payload = {"commandId": "cmd", "status": status, "businessVerification": "unverified"}
    projected = project(payload, 1200)
    # Domain text is not JSON and may contain misleading observed text. Status
    # remains from the original tool facts, never from summary word matching.
    assert projected.content.startswith("Device executor result\n")
    assert projected.additional_kwargs["v8_device_execution"]["status"] == status
    assert ChatRuntime._resolve_tool_result_status(payload)[0] == expected
    assert ChatRuntime._resolve_tool_result_status(projected)[0] == expected
    misleading = projected.model_copy(update={"content": "Summary: succeeded completed"})
    assert ChatRuntime._resolve_tool_result_status(misleading)[0] == expected


def test_overlong_path_and_ids_are_omitted_atomically_not_cut_into_arguments(observations):
    shown = project({"status": "succeeded", "businessVerification": "unverified", "commandId": "c" * 1500,
                     "screenshotRef": {"filePath": "/fixture/" + "x" * 5000}}, 1200)
    parsed = data(shown)
    assert parsed["evidenceOmitted"] and parsed["businessVerification"] == "unverified"
    assert "screenshotRef" not in parsed and "commandId" not in parsed


def test_maximum_protocol_ids_survive_minimum_budget_without_duplicate_examples(observations):
    payload = {key: "x" * 191 for key in ("commandId", "episodeId", "deviceId", "resourceId")}
    payload.update(status="succeeded", businessVerification="unverified", reason="large" * 5000)
    shown = project(payload, 1200)
    parsed = data(shown)
    assert len(shown.content) <= 1200
    assert all(parsed[key] == value for key, value in payload.items() if key != "reason")
    assert parsed["evidenceOmitted"] and not parsed["preconditionComplete"]


def test_raw_persistence_failure_reports_unrecoverable_omission(monkeypatch):
    monkeypatch.setattr("core.tool_surface.record_raw_observation", lambda **_: "")
    shown = project({"status": "succeeded", "businessVerification": "unverified", "reason": "large" * 5000}, 1200)
    assert "tool_observation_detail(raw_ref='')" not in shown.content
    assert data(shown)["businessVerification"] == "unverified"
    assert data(shown)["detailUnavailable"] is True
