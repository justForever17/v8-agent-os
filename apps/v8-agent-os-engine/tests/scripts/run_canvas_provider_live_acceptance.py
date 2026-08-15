from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


def _request(method: str, base_url: str, path: str, *, payload: dict[str, Any] | None = None, timeout: int = 120) -> Any:
    response = requests.request(
        method,
        f"{base_url.rstrip('/')}{path}",
        json=payload,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} failed ({response.status_code}): {response.text[:500]}")
    return response.json()


def _create_session(base_url: str, workspace_path: Path, label: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    payload = _request(
        "POST",
        base_url,
        "/sessions",
        payload={
            "title": f"Canvas provider live acceptance: {label}",
            "userId": "canvas-live-acceptance",
            "projectId": f"canvas-live-{stamp}",
            "workspaceId": f"canvas-live-workspace-{stamp}",
            "workspacePath": str(workspace_path),
            "scopeMode": "explicit",
            "metadata": {"source": "canvas_provider_live_acceptance"},
        },
    )
    session_id = str(payload.get("id") or payload.get("sessionId") or "").strip()
    if not session_id:
        raise RuntimeError(f"session creation returned no id: {payload}")
    return session_id


def _graph(action_id: str, media_type: str, prompt: str, parameters: dict[str, Any]) -> dict[str, Any]:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    action_node_id = f"action-{stamp}"
    result_node_id = f"result-{stamp}"
    return {
        "schema": "v8.creative_canvas_graph.v1",
        "version": 3,
        "graphId": f"canvas-graph-live-{stamp}",
        "nodes": [
            {
                "nodeId": action_node_id,
                "kind": "action",
                "actionDefinitionId": action_id,
                "prompt": prompt,
                "parameters": parameters,
                "configurationRevision": 1,
                "title": "Live provider action",
                "x": 80,
                "y": 80,
                "width": 300,
                "height": 200,
            },
            {
                "nodeId": result_node_id,
                "kind": "result",
                "producerActionNodeId": action_node_id,
                "outputSlot": media_type,
                "mediaType": media_type,
                "title": "Live provider result",
                "x": 480,
                "y": 80,
                "width": 300,
                "height": 200,
            },
        ],
        "edges": [
            {
                "edgeId": f"edge-{stamp}",
                "from": action_node_id,
                "to": result_node_id,
                "fromPort": "right",
                "toPort": "left",
                "fromPortId": "output",
                "toPortId": "input",
                "dataType": media_type,
                "role": "data",
                "order": 0,
                "note": "",
            }
        ],
        "viewport": {"x": 24, "y": 24, "scale": 1},
    }


def _start_graph(base_url: str, session_id: str, graph: dict[str, Any]) -> tuple[str, str]:
    saved = _request(
        "POST",
        base_url,
        f"/sessions/{session_id}/canvas/graph",
        payload={"graph": graph, "expectedRevision": 0},
    )
    revision = int(saved.get("revision") or 0)
    graph_id = str((saved.get("graph") or {}).get("graphId") or "").strip()
    result_node_id = str(graph["nodes"][1]["nodeId"])
    validation = _request(
        "POST",
        base_url,
        f"/sessions/{session_id}/canvas/graph/validate",
        payload={"graphId": graph_id, "graphRevision": revision, "targetNodeIds": [result_node_id]},
    )
    if validation.get("valid") is not True:
        raise RuntimeError(f"Canvas provider readiness failed: {validation}")
    started = _request(
        "POST",
        base_url,
        f"/sessions/{session_id}/canvas/graph/runs",
        payload={"graphId": graph_id, "graphRevision": revision, "targetNodeIds": [result_node_id]},
    )
    graph_run_id = str(started.get("graphRunId") or "").strip()
    if started.get("accepted") is not True or not graph_run_id:
        raise RuntimeError(f"Canvas graph run was not accepted: {started}")
    return graph_run_id, result_node_id


def _get_run(base_url: str, session_id: str, graph_run_id: str) -> dict[str, Any]:
    return dict(_request("GET", base_url, f"/sessions/{session_id}/canvas/graph/runs/{graph_run_id}"))


def _wait_for_provider_job(
    base_url: str,
    session_id: str,
    graph_run_id: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], str]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        run = _get_run(base_url, session_id, graph_run_id)
        for state in dict(run.get("nodeStates") or {}).values():
            job_id = str((state or {}).get("jobId") or "").strip()
            if not job_id:
                continue
            provider_job = _request(
                "GET",
                base_url,
                f"/creative-media/jobs/{job_id}?refresh=false",
            ).get("job") or {}
            provider_task_id = str(
                provider_job.get("providerTaskId")
                or (provider_job.get("providerHandle") or {}).get("taskId")
                or ""
            ).strip()
            if provider_task_id and provider_job.get("status") not in {"succeeded", "failed", "cancelled"}:
                return run, job_id
            if provider_job.get("status") in {"failed", "cancelled"}:
                raise RuntimeError(f"provider job became terminal before cancellation: {provider_job}")
        if str(run.get("status") or "") in {"failed", "cancelled", "succeeded"}:
            raise RuntimeError(f"Canvas run reached terminal state before a cancellable provider job appeared: {run}")
        time.sleep(1)
    raise TimeoutError("No provider job appeared within the live acceptance deadline")


def _wait_for_terminal(base_url: str, session_id: str, graph_run_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        run = _get_run(base_url, session_id, graph_run_id)
        if str(run.get("status") or "") in {"succeeded", "failed", "cancelled", "interrupted"}:
            return run
        time.sleep(2)
    raise TimeoutError("Canvas run did not reach a terminal state within the live acceptance deadline")


def _assert_realtime_reload(base_url: str, session_id: str, graph_run_id: str, expected_statuses: list[str]) -> None:
    events_payload = _request("GET", base_url, f"/sessions/{session_id}/runtime-events")
    events = list(events_payload.get("events") or events_payload.get("items") or [])
    statuses = [
        str((event.get("payload") or {}).get("status") or "")
        for event in events
        if event.get("topic") == "canvas.graph.run.state"
        and str((event.get("payload") or {}).get("graphRunId") or "") == graph_run_id
    ]
    if statuses != expected_statuses:
        raise RuntimeError(f"unexpected Canvas realtime sequence: {statuses}")
    for label, path in (
        ("compact snapshot", f"/sessions/{session_id}/snapshot?compact=1"),
        ("history", f"/sessions/{session_id}/history"),
    ):
        payload = _request("GET", base_url, path)
        serialized = json.dumps(payload, ensure_ascii=False)
        if graph_run_id not in serialized or "canvas.graph.run.state" not in serialized:
            raise RuntimeError(f"authoritative {label} did not retain the Canvas graph run milestone")


def _run_video_cancel(base_url: str, workspace_path: Path, model_ref: str) -> dict[str, Any]:
    session_id = _create_session(base_url, workspace_path, "video cancel")
    graph = _graph(
        "creative_media.generate_video",
        "video",
        "5-second product shot of a single glass cube on a white desk, one slow camera push, no audio.",
        {
            "modelRef": model_ref,
            "duration": 5,
            "ratio": "16:9",
            "resolution": "720p",
            "generateAudio": False,
        },
    )
    graph_run_id, _result_node_id = _start_graph(base_url, session_id, graph)
    _run, provider_job_id = _wait_for_provider_job(base_url, session_id, graph_run_id, 60)
    cancelled = _request(
        "POST",
        base_url,
        f"/sessions/{session_id}/canvas/graph/runs/{graph_run_id}/cancel",
        payload={"reason": "live_acceptance_cancel"},
        timeout=180,
    )
    terminal = _wait_for_terminal(base_url, session_id, graph_run_id, 10)
    if terminal.get("status") != "cancelled":
        raise RuntimeError(f"Canvas run did not remain cancelled: {terminal}")
    provider_job = _request("GET", base_url, f"/creative-media/jobs/{provider_job_id}?refresh=false").get("job") or {}
    lifecycle = dict((provider_job.get("lifecycle") or {}).get("cancel") or {})
    cleanup = dict((provider_job.get("lifecycle") or {}).get("cleanup") or {})
    if lifecycle.get("status") != "completed" or lifecycle.get("remoteTaskMayContinue") is not False:
        raise RuntimeError(f"provider cancellation was not accepted with a remote task handle: {provider_job}")
    if cleanup.get("status") not in {"completed", "not_active"}:
        raise RuntimeError(f"provider cleanup lifecycle was not persisted: {provider_job}")
    _assert_realtime_reload(
        base_url,
        session_id,
        graph_run_id,
        ["queued", "running", "cancelling", "cancelled"],
    )
    return {
        "sessionId": session_id,
        "graphRunId": graph_run_id,
        "providerJobId": provider_job_id,
        "graphStatus": terminal.get("status"),
        "providerCancellation": lifecycle.get("status"),
        "remoteTaskMayContinue": lifecycle.get("remoteTaskMayContinue"),
        "providerCleanup": cleanup.get("status"),
        "cancelResponseStatus": cancelled.get("status"),
    }


def _run_success_image(base_url: str, workspace_path: Path, model_ref: str) -> dict[str, Any]:
    session_id = _create_session(base_url, workspace_path, "image success")
    parameters = {"ratio": "1:1", "responseFormat": "url"}
    if model_ref:
        parameters["modelRef"] = model_ref
    graph = _graph(
        "creative_media.generate_image",
        "image",
        "Clean product photo of a single translucent glass cube on a white desk, soft studio light.",
        parameters,
    )
    graph_run_id, result_node_id = _start_graph(base_url, session_id, graph)
    terminal = _wait_for_terminal(base_url, session_id, graph_run_id, 300)
    if terminal.get("status") != "succeeded":
        raise RuntimeError(f"live image Canvas run failed: {terminal}")
    graph_runtime = dict(
        (_request("GET", base_url, f"/sessions/{session_id}/canvas/graph").get("runtime") or {})
    )
    if str(graph_runtime.get("graphRunId") or "") != graph_run_id:
        raise RuntimeError(f"Canvas graph reload returned a different run: {graph_runtime}")
    outputs = list((graph_runtime.get("outputs") or {}).get(result_node_id) or [])
    artifact_id = str((outputs[0] if outputs else {}).get("artifactId") or "").strip()
    if not artifact_id:
        raise RuntimeError(f"live image run produced no governed artifact: {graph_runtime}")
    response = requests.get(
        f"{base_url.rstrip('/')}/artifacts/{artifact_id}/content",
        params={"sessionId": session_id},
        timeout=120,
    )
    response.raise_for_status()
    if not response.content:
        raise RuntimeError("live image artifact content was empty")
    other_session_id = _create_session(base_url, workspace_path, "cross-session denial")
    denied = requests.get(
        f"{base_url.rstrip('/')}/artifacts/{artifact_id}/content",
        params={"sessionId": other_session_id},
        timeout=120,
    )
    if denied.status_code != 404:
        raise RuntimeError(f"cross-session artifact read was not denied: HTTP {denied.status_code}")
    _assert_realtime_reload(base_url, session_id, graph_run_id, ["queued", "running", "completed"])
    return {
        "sessionId": session_id,
        "graphRunId": graph_run_id,
        "artifactId": artifact_id,
        "artifactBytes": len(response.content),
        "crossSessionStatus": denied.status_code,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Canvas real-provider acceptance. Dry-run unless --live is explicit.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9530/v1")
    parser.add_argument("--workspace-path", default="")
    parser.add_argument("--video-model-ref", default="")
    parser.add_argument("--image-model-ref", default="")
    parser.add_argument("--include-success-image", action="store_true")
    parser.add_argument(
        "--image-only",
        action="store_true",
        help="Run only the real image success and artifact-authority acceptance; skip video cancellation.",
    )
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    video_requested = not args.image_only
    image_requested = bool(args.include_success_image or args.image_only)
    plan = {
        "videoCancel": video_requested,
        "successImage": image_requested,
        "imageOnly": bool(args.image_only),
        "workspacePath": args.workspace_path,
        "videoModelRef": args.video_model_ref,
        "imageModelRef": args.image_model_ref or "configured preference",
    }
    if not args.live:
        print(json.dumps({"ok": True, "dryRun": True, "plan": plan}, ensure_ascii=False, indent=2))
        return 0
    workspace_path = Path(args.workspace_path).expanduser().resolve()
    if not workspace_path.is_dir():
        raise SystemExit("--workspace-path must be an existing directory for --live")
    if video_requested and not args.video_model_ref.strip():
        raise SystemExit("--video-model-ref is required for --live provider cancellation")

    result: dict[str, Any] = {
        "ok": True,
        "kind": "REAL-PROVIDER",
    }
    if video_requested:
        result["videoCancel"] = _run_video_cancel(args.base_url, workspace_path, args.video_model_ref.strip())
    if image_requested:
        result["successImage"] = _run_success_image(
            args.base_url,
            workspace_path,
            args.image_model_ref.strip(),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
