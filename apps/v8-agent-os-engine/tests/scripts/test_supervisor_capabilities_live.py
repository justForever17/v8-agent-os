from types import SimpleNamespace

from tests.scripts.run_supervisor_capabilities_live import apply_tool_evidence, evaluate_result, latest_video_samples, video_summary_matches_samples, sampled_frames_were_analyzed


def event(name, status, body, topic="tool.finished"):
    return {"topic": topic, "payload": {"tool": {"toolName": name, "resultStatus": status, "agentVisibleResult": body}}}


def test_called_vision_or_nonempty_prose_does_not_prove_visual_inspection():
    checks = {}
    apply_tool_evidence(checks, [event("vision_media_analyzer", "failed", "Error: file unavailable")], "media")
    assert not checks["visualInspectionSucceeded"]
    apply_tool_evidence(checks, [event("vision_media_analyzer", "completed", "--- Vision Analysis Complete ---\nActual pixels")], "media")
    assert checks["visualInspectionSucceeded"]


def test_video_summary_cannot_hide_failed_cleanup_or_reuse_an_action_start():
    checks = {}
    apply_tool_evidence(checks, [event("browser_broker", "failed", "Error: Browser close: failed\nstale_observation")], "video", resumed=True)
    assert not checks["ownedPageClosed"]
    apply_tool_evidence(checks, [event("browser_broker", "completed", "Browser close: completed", topic="tool.started")], "video", resumed=True)
    assert not checks["ownedPageClosed"]
    apply_tool_evidence(checks, [event("browser_broker", "completed", "Browser close: completed")], "video", resumed=True)
    assert checks["ownedPageClosed"]


def test_gui_action_receipts_do_not_replace_actual_submission():
    result = SimpleNamespace(status="completed", final_text="Done", tool_invocations=[
        {"toolName": "computer_use_observe_scene"}, {"toolName": "computer_use_click_target"}],
        handoffs=[], episodes=[], web_activity_audit={})
    checks = evaluate_result(result, "desktop", submitted={"submittedText": "wrong"}, nonce="required")
    assert not checks["actualGuiSubmission"]


def test_redacted_reports_and_embedded_payloads_remain_valid_json():
    import json
    from tests.scripts.run_supervisor_runtime_skill_live_audit import _redact
    source = {"continuationToken": None, "resumeToken": "PRIVATE-CANARY",
              "input_tokens": 123, "requestedMaxTokens": 0,
              "payload_json": json.dumps({"api_key": "PRIVATE-CANARY", "result": "valid"})}
    rendered = _redact(source)
    decoded = json.loads(rendered)
    assert decoded["continuationToken"] is None and decoded["requestedMaxTokens"] == 0
    assert decoded["input_tokens"] == 123
    assert json.loads(decoded["payload_json"])["result"] == "valid"
    assert "PRIVATE-CANARY" not in rendered


def test_video_time_oracle_uses_actual_samples_not_a_previous_run_fixture():
    body = "Browser media: completed\n" + "\n".join(
        f"Frame {i} at {time}s; vision file_path: frame{i}.jpg" for i, time in enumerate((2, 5, 8), 1))
    samples = latest_video_samples([event("browser_broker", "completed", body)])
    assert video_summary_matches_samples("t≈2s: CPU; t≈5s: photo; t≈8s: dialog", samples)
    assert not video_summary_matches_samples("2秒、6秒、10秒", samples)
    assert not video_summary_matches_samples("2秒", samples)
    started = event("vision_media_analyzer", None, "", topic="tool.started")
    started["payload"]["tool"].update(toolCallId="vision", args={"images": [{"file_path": path} for _, path in samples]})
    finished = event("vision_media_analyzer", "completed", "--- Vision Analysis Complete ---")
    finished["payload"]["tool"]["toolCallId"] = "vision"
    assert sampled_frames_were_analyzed([started, finished], samples)
    finished["payload"]["tool"]["resultStatus"] = "failed"
    assert not sampled_frames_were_analyzed([started, finished], samples)
