from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

import core.creative_canvas_graph as graph_module
from core.artifact_store import ArtifactStore
from core.creative_canvas_graph import (
    CreativeCanvasGraphConflict,
    CreativeCanvasGraphError,
    CreativeCanvasGraphService,
)
from core.creative_media_resource_authority import CreativeMediaResourceAuthorityService
from core.database import DatabaseManager
from core.runtime_projection import project_runtime_timeline_from_events
from core.workspace_identity import workspace_path_key
from erc.session_history_contract import build_session_history_ledger_entries


def _authority(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_root=str(workspace),
        workspace_id="workspace-a",
        project_id="project-a",
        side_effects_allowed=True,
    )


@pytest.fixture()
def output_review_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = DatabaseManager(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    output_dir = workspace / ".v8" / "outputs"
    output_dir.mkdir(parents=True)
    for session_id in ("session-a", "session-b"):
        database.create_or_update_session(session_id, session_id, user_id="user-a")

    authority_service = SimpleNamespace(resolve=lambda **_kwargs: _authority(workspace))
    resource_authority = CreativeMediaResourceAuthorityService(
        database=database,
        authority_service=authority_service,
    )
    monkeypatch.setattr(graph_module, "db", database)
    monkeypatch.setattr(graph_module, "creative_media_resource_authority", resource_authority)
    monkeypatch.setattr(graph_module, "artifact_store", ArtifactStore(database=database))
    monkeypatch.setattr(graph_module.workspace_authority_service, "resolve", authority_service.resolve)

    graph_id = "canvas-graph-review"
    graph_run_id = "canvas-run-review"
    now = "2026-08-18T00:00:00Z"
    with database.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO creative_canvas_graphs(
                graph_id, session_id, workspace_key, schema_version, revision,
                graph_json, created_at, updated_at
            ) VALUES (?, 'session-a', ?, 3, 1, ?, ?, ?)
            """,
            (
                graph_id,
                workspace_path_key(workspace),
                json.dumps({"schema": "v8.creative_canvas_graph.v1", "version": 3, "graphId": graph_id}),
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO creative_canvas_graph_runs(
                graph_run_id, graph_id, session_id, canvas_operation_id, graph_revision,
                target_node_ids_json, plan_json, node_states_json, status, created_at,
                updated_at, completed_at
            ) VALUES (?, ?, 'session-a', 'canvas-op-review', 1, '["result-node"]', '{}', '{}',
                      'succeeded', ?, ?, ?)
            """,
            (graph_run_id, graph_id, now, now, now),
        )
        conn.commit()

    for version, provider, model in (
        (1, "provider-old", "model-old"),
        (2, "provider-new", "model-new"),
    ):
        artifact_id = f"artifact-review-{version}"
        relative_path = Path(".v8") / "outputs" / f"version-{version}.png"
        source_path = workspace / relative_path
        source_path.write_bytes(f"version-{version}".encode("ascii"))
        database.add_runtime_artifact(
            artifact_id,
            "image",
            "image/png",
            session_id="session-a",
            title=f"Version {version}",
            source_path=str(source_path),
            workspace_path=relative_path.as_posix(),
            external_url=f"https://secret.example/{artifact_id}",
            metadata={
                "workspaceId": "workspace-a",
                "projectId": "project-a",
                "workspaceRoot": str(workspace),
                "workspaceRelativePath": relative_path.as_posix(),
                "storageClass": "workspace",
                "pathPlane": "workspace_artifact",
            },
        )
        proof = {
            "schema": "v8.creative_canvas_output_proof.v1",
            "available": True,
            "status": "succeeded",
            "provider": provider,
            "model": model,
            "recipeId": f"recipe-{version}",
            "operationKind": "image.edit",
            "elapsedMs": 1000 + version,
            "cost": version / 10,
            "currency": "USD",
            "quality": {"status": "passed", "score": 0.9, "raw": "hidden"},
            "qa": {"status": "passed", "passed": True, "raw": "hidden"},
        }
        with database.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO creative_canvas_node_outputs(
                    output_version_id, graph_run_id, graph_id, session_id, action_node_id,
                    result_node_id, version_index, artifact_id, job_id, media_type,
                    output_slot, config_digest, metadata_json, created_at
                ) VALUES (?, ?, ?, 'session-a', 'action-node', 'result-node', ?, ?, ?,
                          'image', 'image_derivative', ?, ?, ?)
                """,
                (
                    f"canvas-output-{version}",
                    graph_run_id,
                    graph_id,
                    version,
                    artifact_id,
                    f"provider-job-{version}",
                    f"digest-{version}",
                    json.dumps({
                        "proof": proof,
                        "providerHandle": "must-not-project",
                        "sourcePath": "must-not-project",
                        "rawResponse": "must-not-project",
                    }),
                    now,
                ),
            )
            conn.commit()

    return CreativeCanvasGraphService(), database, workspace


def test_output_versions_project_their_own_authorized_resource_and_proof(output_review_service) -> None:
    service, _database, _workspace = output_review_service

    graph = service.get_graph(session_id="session-a")
    versions = graph["runtime"]["outputs"]["result-node"]

    assert [item["version"] for item in versions] == [2, 1]
    assert [item["proof"]["provider"] for item in versions] == ["provider-new", "provider-old"]
    assert [item["proof"]["model"] for item in versions] == ["model-new", "model-old"]
    assert versions[1]["resource"]["resourceRef"] == {
        "kind": "artifact_content",
        "artifactId": "artifact-review-1",
        "sessionId": "session-a",
    }
    assert versions[0]["review"] == {
        "decision": "pending",
        "revision": 0,
        "note": "",
        "selectedForDelivery": False,
        "reviewedAt": None,
        "deliveryManifestArtifactId": None,
        "deliveredAt": None,
        "delivery": {
            "status": "idle",
            "attempt": 0,
            "errorDetailCode": None,
            "manifestArtifactId": None,
            "deliveredAt": None,
        },
    }
    serialized = json.dumps(versions)
    for forbidden in ("providerHandle", "sourcePath", "rawResponse", "secret.example", "provider-job"):
        assert forbidden not in serialized


def test_output_history_is_not_truncated_by_the_general_artifact_catalog_limit(output_review_service) -> None:
    service, database, _workspace = output_review_service
    with database.get_connection() as conn:
        template = conn.execute(
            "SELECT * FROM creative_canvas_node_outputs WHERE output_version_id = 'canvas-output-2'"
        ).fetchone()
        assert template
        for version in range(3, 106):
            conn.execute(
                """
                INSERT INTO creative_canvas_node_outputs(
                    output_version_id, graph_run_id, graph_id, session_id, action_node_id,
                    result_node_id, version_index, artifact_id, job_id, media_type,
                    output_slot, config_digest, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"canvas-output-{version}",
                    template["graph_run_id"],
                    template["graph_id"],
                    template["session_id"],
                    template["action_node_id"],
                    template["result_node_id"],
                    version,
                    template["artifact_id"],
                    f"provider-job-{version}",
                    template["media_type"],
                    template["output_slot"],
                    f"digest-{version}",
                    template["metadata_json"],
                    template["created_at"],
                ),
            )
        conn.commit()

    versions = service.get_graph(session_id="session-a")["runtime"]["outputs"]["result-node"]

    assert len(versions) == 105
    assert versions[0]["version"] == 105
    assert versions[-1]["version"] == 1


def test_review_is_revision_fenced_and_selection_moves_atomically(output_review_service) -> None:
    service, _database, _workspace = output_review_service

    first = service.review_output(
        session_id="session-a",
        output_version_id="canvas-output-1",
        decision="approved",
        note="First choice",
        selected_for_delivery=True,
        expected_revision=0,
    )
    assert first["review"]["revision"] == 1
    second = service.review_output(
        session_id="session-a",
        output_version_id="canvas-output-2",
        decision="approved",
        note="Final choice",
        selected_for_delivery=True,
        expected_revision=1,
    )

    assert second["review"]["revision"] == 2
    assert {item["outputVersionId"] for item in second["affectedReviews"]} == {
        "canvas-output-1",
        "canvas-output-2",
    }
    versions = service.get_graph(session_id="session-a")["runtime"]["outputs"]["result-node"]
    by_version = {item["version"]: item for item in versions}
    assert by_version[1]["review"]["selectedForDelivery"] is False
    assert by_version[1]["review"]["revision"] == 2
    assert by_version[2]["review"]["selectedForDelivery"] is True
    with pytest.raises(CreativeCanvasGraphConflict, match="changed"):
        service.review_output(
            session_id="session-a",
            output_version_id="canvas-output-1",
            decision="rejected",
            note="Stale",
            selected_for_delivery=False,
            expected_revision=1,
        )
    with pytest.raises(CreativeCanvasGraphError, match="Only an approved"):
        service.review_output(
            session_id="session-a",
            output_version_id="canvas-output-1",
            decision="rejected",
            note="Invalid selection",
            selected_for_delivery=True,
            expected_revision=2,
        )


def test_cross_session_review_and_delivery_have_no_side_effects(output_review_service) -> None:
    service, database, workspace = output_review_service
    delivery_dir = workspace / ".v8" / "creative-media" / "delivery"

    with pytest.raises(CreativeCanvasGraphError, match="unavailable"):
        service.review_output(
            session_id="session-b",
            output_version_id="canvas-output-1",
            decision="approved",
            note="Cross session",
            selected_for_delivery=True,
            expected_revision=0,
        )
    with pytest.raises(CreativeCanvasGraphError, match="unavailable"):
        service.create_delivery_manifest(
            session_id="session-b",
            output_version_id="canvas-output-1",
            expected_review_revision=0,
        )

    with database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creative_canvas_output_reviews").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_artifacts WHERE id LIKE 'art_canvas_delivery_%'"
        ).fetchone()[0] == 0
    assert not delivery_dir.exists()


def test_cross_session_artifact_bound_to_local_output_is_fail_closed(output_review_service) -> None:
    service, database, workspace = output_review_service
    foreign_path = workspace / ".v8" / "outputs" / "foreign.png"
    foreign_path.write_bytes(b"foreign")
    database.add_runtime_artifact(
        "artifact-foreign",
        "image",
        "image/png",
        session_id="session-b",
        source_path=str(foreign_path),
        workspace_path=".v8/outputs/foreign.png",
        external_url="https://foreign.example/private.png",
        metadata={
            "workspaceId": "workspace-a",
            "projectId": "project-a",
            "workspaceRoot": str(workspace),
            "workspaceRelativePath": ".v8/outputs/foreign.png",
            "storageClass": "workspace",
            "pathPlane": "workspace_artifact",
        },
    )
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE creative_canvas_node_outputs SET artifact_id = 'artifact-foreign' "
            "WHERE output_version_id = 'canvas-output-1'"
        )
        conn.commit()

    version = service.get_graph(session_id="session-a")["runtime"]["outputs"]["result-node"][1]
    assert version["resource"]["availability"] == "unavailable"
    assert "resourceRef" not in version["resource"]
    assert "foreign.example" not in json.dumps(version)
    service.review_output(
        session_id="session-a",
        output_version_id="canvas-output-1",
        decision="approved",
        note="Must still fail",
        selected_for_delivery=True,
        expected_revision=0,
    )
    with pytest.raises(CreativeCanvasGraphConflict, match="not available"):
        service.create_delivery_manifest(
            session_id="session-a",
            output_version_id="canvas-output-1",
            expected_review_revision=1,
        )
    assert not (workspace / ".v8" / "creative-media" / "delivery").exists()
    with database.get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_artifacts WHERE id LIKE 'art_canvas_delivery_%'"
        ).fetchone()[0] == 0


def test_delivery_dry_run_is_write_free_and_actual_delivery_is_idempotent(output_review_service) -> None:
    service, database, workspace = output_review_service
    service.review_output(
        session_id="session-a",
        output_version_id="canvas-output-2",
        decision="approved",
        note="Approved final",
        selected_for_delivery=True,
        expected_revision=0,
    )

    preview = service.create_delivery_manifest(
        session_id="session-a",
        output_version_id="canvas-output-2",
        expected_review_revision=1,
        dry_run=True,
    )
    delivery_dir = workspace / ".v8" / "creative-media" / "delivery"
    assert preview["status"] == "ready"
    assert preview["dryRun"] is True
    assert not delivery_dir.exists()
    assert database.get_runtime_artifact(preview["manifestArtifactId"]) is None

    delivered = service.create_delivery_manifest(
        session_id="session-a",
        output_version_id="canvas-output-2",
        expected_review_revision=1,
    )
    repeated = service.create_delivery_manifest(
        session_id="session-a",
        output_version_id="canvas-output-2",
        expected_review_revision=1,
    )

    assert delivered["manifestArtifactId"] == preview["manifestArtifactId"]
    assert repeated["manifestArtifactId"] == delivered["manifestArtifactId"]
    delivery_paths = list(delivery_dir.glob("canvas-output-2-*.json"))
    assert len(delivery_paths) == 1
    assert json.loads(delivery_paths[0].read_text(encoding="utf-8")) == delivered["manifest"]
    assert delivered["review"]["deliveredAt"]
    with database.get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_artifacts WHERE id = ?",
            (delivered["manifestArtifactId"],),
        ).fetchone()[0] == 1
    serialized = json.dumps(delivered)
    for forbidden in ("providerHandle", "sourcePath", "externalUrl", "rawResponse", "secret.example"):
        assert forbidden not in serialized
    with pytest.raises(CreativeCanvasGraphConflict, match="immutable"):
        service.review_output(
            session_id="session-a",
            output_version_id="canvas-output-2",
            decision="rejected",
            note="Too late",
            selected_for_delivery=False,
            expected_revision=1,
        )


def test_delivery_receipt_recovers_after_artifact_registration_failure(
    output_review_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, database, workspace = output_review_service
    service.review_output(
        session_id="session-a",
        output_version_id="canvas-output-1",
        decision="approved",
        note="Recoverable delivery",
        selected_for_delivery=True,
        expected_revision=0,
    )
    store = graph_module.artifact_store
    original_record = store.record_local_file

    def fail_registration(**_kwargs):
        raise OSError("simulated artifact registration failure")

    monkeypatch.setattr(store, "record_local_file", fail_registration)
    with pytest.raises(OSError, match="simulated artifact registration failure"):
        service.create_delivery_manifest(
            session_id="session-a",
            output_version_id="canvas-output-1",
            expected_review_revision=1,
        )

    graph = service.get_graph(session_id="session-a")
    review = graph["runtime"]["outputs"]["result-node"][1]["review"]
    claimed_artifact_id = review["delivery"]["manifestArtifactId"]
    assert claimed_artifact_id
    assert review["deliveredAt"] is None
    assert review["delivery"]["status"] == "failed"
    assert review["delivery"]["attempt"] == 1
    assert review["delivery"]["errorDetailCode"] == "delivery_artifact_registration_failed"
    assert database.get_runtime_artifact(claimed_artifact_id) is None
    assert len(list((workspace / ".v8" / "creative-media" / "delivery").glob("canvas-output-1-*.json"))) == 1

    monkeypatch.setattr(store, "record_local_file", original_record)
    recovered = service.create_delivery_manifest(
        session_id="session-a",
        output_version_id="canvas-output-1",
        expected_review_revision=1,
    )

    assert recovered["manifestArtifactId"] == claimed_artifact_id
    assert recovered["review"]["deliveredAt"]
    assert recovered["delivery"]["attempt"] == 2
    assert database.get_runtime_artifact(claimed_artifact_id)


def test_get_graph_fails_closed_after_workspace_rebind(output_review_service, monkeypatch) -> None:
    service, _database, workspace = output_review_service
    rebound = workspace.parent / "rebound-workspace"
    rebound.mkdir()
    resolver = lambda **_kwargs: _authority(rebound)
    monkeypatch.setattr(graph_module.workspace_authority_service, "resolve", resolver)
    monkeypatch.setattr(graph_module.creative_media_resource_authority._authority_service, "resolve", resolver)

    with pytest.raises(CreativeCanvasGraphError, match="unavailable") as error:
        service.get_graph(session_id="session-a")

    serialized = str(error.value)
    assert "canvas-graph-review" not in serialized
    assert "artifact-review" not in serialized
    assert "provider-" not in serialized


def test_record_output_rejects_empty_or_foreign_artifact_without_output_side_effects(
    output_review_service,
) -> None:
    service, database, workspace = output_review_service
    foreign_path = workspace / ".v8" / "outputs" / "producer-foreign.png"
    foreign_path.write_bytes(b"foreign")
    database.add_runtime_artifact(
        "artifact-producer-foreign",
        "image",
        "image/png",
        session_id="session-b",
        source_path=str(foreign_path),
        workspace_path=".v8/outputs/producer-foreign.png",
        metadata={
            "workspaceId": "workspace-a",
            "projectId": "project-a",
            "workspaceRoot": str(workspace),
            "workspaceRelativePath": ".v8/outputs/producer-foreign.png",
        },
    )
    with database.get_connection() as conn:
        conn.execute("UPDATE creative_canvas_graph_runs SET status = 'running' WHERE graph_run_id = 'canvas-run-review'")
        before = conn.execute("SELECT COUNT(*) FROM creative_canvas_node_outputs").fetchone()[0]
        conn.commit()
    entry = {
        "resultNodeId": "result-node",
        "actionNodeId": "action-node",
        "actionDefinitionId": "image.edit",
        "outputMediaType": "image",
        "outputSlot": "image_derivative",
    }
    for artifact in ({}, {"artifactId": "artifact-producer-foreign"}):
        with pytest.raises(CreativeCanvasGraphError):
            service._record_output(
                graph_run_id="canvas-run-review",
                graph_id="canvas-graph-review",
                session_id="session-a",
                entry=entry,
                job={"status": "succeeded"},
                artifact=artifact,
            )
    with database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM creative_canvas_node_outputs").fetchone()[0] == before


def test_result_selection_epoch_allows_only_one_concurrent_winner(output_review_service) -> None:
    service, database, _workspace = output_review_service

    def select(output_id: str):
        return service.review_output(
            session_id="session-a",
            output_version_id=output_id,
            decision="approved",
            note="race",
            selected_for_delivery=True,
            expected_revision=0,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(select, output_id) for output_id in ("canvas-output-1", "canvas-output-2")]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except CreativeCanvasGraphConflict:
            outcomes.append(None)
    assert sum(item is not None for item in outcomes) == 1
    with database.get_connection() as conn:
        assert conn.execute(
            "SELECT revision FROM creative_canvas_output_review_heads WHERE graph_id = 'canvas-graph-review' "
            "AND result_node_id = 'result-node'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM creative_canvas_output_reviews WHERE selected_for_delivery = 1"
        ).fetchone()[0] == 1
    events = [event for event in database.get_runtime_events("session-a") if event["topic"] == graph_module.OUTPUT_REVIEW_STATE_TOPIC]
    assert len(events) == 1


def test_output_proof_redacts_url_path_and_credential_like_values(output_review_service) -> None:
    service, database, _workspace = output_review_service
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT metadata_json FROM creative_canvas_node_outputs WHERE output_version_id = 'canvas-output-1'"
        ).fetchone()
        metadata = json.loads(row[0])
        metadata["proof"].update({
            "provider": "https://provider.example/private",
            "model": r"C:\\private\\model.bin",
            "recipeId": "token=very-secret-value",
            "operationKind": "sk-1234567890abcdefghijkl",
        })
        conn.execute(
            "UPDATE creative_canvas_node_outputs SET metadata_json = ? WHERE output_version_id = 'canvas-output-1'",
            (json.dumps(metadata),),
        )
        conn.commit()

    proof = service.get_graph(session_id="session-a")["runtime"]["outputs"]["result-node"][1]["proof"]
    for key in ("provider", "model", "recipeId", "operationKind"):
        assert key not in proof
    serialized = json.dumps(proof)
    for secret in ("provider.example", "private", "very-secret", "sk-123"):
        assert secret not in serialized


def test_delivery_lease_single_claim_and_ack_failure_is_retryable(
    output_review_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, database, _workspace = output_review_service
    service.review_output(
        session_id="session-a",
        output_version_id="canvas-output-2",
        decision="approved",
        note="lease",
        selected_for_delivery=True,
        expected_revision=0,
    )
    started = Event()
    release = Event()
    original_record = graph_module.artifact_store.record_local_file

    def delayed_record(**kwargs):
        started.set()
        assert release.wait(timeout=5)
        return original_record(**kwargs)

    monkeypatch.setattr(graph_module.artifact_store, "record_local_file", delayed_record)
    with ThreadPoolExecutor(max_workers=1) as executor:
        claimed = executor.submit(
            service.create_delivery_manifest,
            session_id="session-a",
            output_version_id="canvas-output-2",
            expected_review_revision=1,
        )
        assert started.wait(timeout=5)
        with pytest.raises(CreativeCanvasGraphConflict, match="in progress"):
            service.create_delivery_manifest(
                session_id="session-a",
                output_version_id="canvas-output-2",
                expected_review_revision=1,
            )
        release.set()
        delivered = claimed.result(timeout=5)
    assert delivered["delivery"]["status"] == "delivered"
    assert delivered["delivery"]["attempt"] == 1

    # A different output proves acknowledgement rollback and receipt recovery.
    with database.get_connection() as conn:
        conn.execute("DELETE FROM creative_canvas_output_reviews")
        conn.execute("DELETE FROM creative_canvas_output_review_heads")
        conn.commit()
    service.review_output(
        session_id="session-a",
        output_version_id="canvas-output-1",
        decision="approved",
        note="ack retry",
        selected_for_delivery=True,
        expected_revision=0,
    )
    original_append = service._append_output_state_event

    def fail_delivered_event(conn, *, row, topic, payload, event_ts):
        if topic == graph_module.OUTPUT_DELIVERY_STATE_TOPIC and payload.get("delivery", {}).get("status") == "delivered":
            raise RuntimeError("simulated delivery ack event failure")
        return original_append(conn, row=row, topic=topic, payload=payload, event_ts=event_ts)

    monkeypatch.setattr(service, "_append_output_state_event", fail_delivered_event)
    with pytest.raises(RuntimeError, match="ack event"):
        service.create_delivery_manifest(
            session_id="session-a",
            output_version_id="canvas-output-1",
            expected_review_revision=1,
        )
    failed_review = service.get_graph(session_id="session-a")["runtime"]["outputs"]["result-node"][1]["review"]
    assert failed_review["delivery"]["status"] == "failed"
    assert failed_review["delivery"]["errorDetailCode"] == "delivery_ack_failed"
    monkeypatch.setattr(service, "_append_output_state_event", original_append)
    recovered = service.create_delivery_manifest(
        session_id="session-a",
        output_version_id="canvas-output-1",
        expected_review_revision=1,
    )
    assert recovered["delivery"]["status"] == "delivered"
    assert recovered["delivery"]["attempt"] == 2


def test_review_delivery_events_match_live_history_and_reload_without_sensitive_fields(
    output_review_service,
) -> None:
    service, database, _workspace = output_review_service
    service.review_output(
        session_id="session-a",
        output_version_id="canvas-output-2",
        decision="approved",
        note="must stay out of events",
        selected_for_delivery=True,
        expected_revision=0,
    )
    delivered = service.create_delivery_manifest(
        session_id="session-a",
        output_version_id="canvas-output-2",
        expected_review_revision=1,
    )
    events = database.get_runtime_events("session-a")
    output_events = [event for event in events if event["topic"].startswith("canvas.graph.output.")]
    assert [event["topic"] for event in output_events] == [
        graph_module.OUTPUT_REVIEW_STATE_TOPIC,
        graph_module.OUTPUT_DELIVERY_STATE_TOPIC,
        graph_module.OUTPUT_DELIVERY_STATE_TOPIC,
    ]
    timeline = project_runtime_timeline_from_events(events)
    projected = [entry for entry in timeline if entry["topic"].startswith("canvas.graph.output.")]
    assert [entry["status"] for entry in projected] == ["approved", "pending", "delivered"]
    ledger = build_session_history_ledger_entries(session_id="session-a", runtime_events=events)
    serialized = json.dumps({"events": output_events, "timeline": projected, "ledger": ledger})
    for forbidden in ("must stay out", "sourcePath", "externalUrl", "providerHandle", "secret.example"):
        assert forbidden not in serialized
    reload_review = service.get_graph(session_id="session-a")["runtime"]["outputs"]["result-node"][0]["review"]
    assert reload_review["delivery"]["status"] == delivered["delivery"]["status"] == "delivered"


@pytest.mark.parametrize("invalid_revision", [True, False, "0", 0.0, None])
def test_review_and_delivery_require_strict_integer_revision(output_review_service, invalid_revision) -> None:
    service, _database, _workspace = output_review_service
    with pytest.raises(CreativeCanvasGraphError, match="revision"):
        service.review_output(
            session_id="session-a",
            output_version_id="canvas-output-1",
            decision="approved",
            note="strict",
            selected_for_delivery=True,
            expected_revision=invalid_revision,
        )
