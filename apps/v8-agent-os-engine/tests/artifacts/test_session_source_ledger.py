from __future__ import annotations

from core.database import DatabaseManager
from erc.chat_canonical_transcript import format_canonical_message


def _seed_run(database: DatabaseManager) -> None:
    database.create_or_update_session("session-1", "Source ledger", user_id="user-1")
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO run_records (id, session_id, run_type, status) VALUES (?, ?, ?, ?)",
            ("run-1", "session-1", "chat", "running"),
        )
        conn.commit()


def test_source_ledger_binds_upload_to_user_message(tmp_path):
    database = DatabaseManager(tmp_path / "state.db")
    _seed_run(database)
    database.add_session_source(
        source_id="src-1",
        session_id="session-1",
        source_kind="phone_voice",
        mime_type="audio/m4a",
        title="voice.m4a",
        workspace_path=".v8/uploads/voice.m4a",
        external_url="/api/client/workspace/voice.m4a",
        resource_ref={"adminPath": "/api/client/workspace/voice.m4a"},
    )
    assert database.list_session_sources(session_id="session-1") == []
    assert database.list_session_sources(session_id="session-1", include_unbound=True)[0]["sourceId"] == "src-1"
    database.add_message("message-1", "session-1", "user", "")

    assert database.bind_session_sources_to_message(
        session_id="session-1",
        source_ids=["src-1"],
        message_id="message-1",
    ) == 1
    source = database.list_session_sources(session_id="session-1")[0]
    assert source["sourceId"] == "src-1"
    assert source["messageId"] == "message-1"
    assert source["resourceRef"]["adminPath"].endswith("voice.m4a")
    assert database.get_session_source(session_id="session-1", source_id="src-1")["sourceId"] == "src-1"
    assert database.get_session_source(session_id="session-other", source_id="src-1") is None


def test_message_attachment_only_claims_true_auto_attach_artifacts(tmp_path):
    database = DatabaseManager(tmp_path / "state.db")
    _seed_run(database)
    for artifact_id, role, auto_attach in (
        ("art-visible", "artifact", True),
        ("art-hidden", "artifact", False),
        ("art-source", "source_derivative", False),
    ):
        database.add_runtime_artifact(
            artifact_id=artifact_id,
            artifact_kind="audio",
            mime_type="audio/mpeg",
            session_id="session-1",
            run_id="run-1",
            resource_role=role,
            source_id="src-1" if role == "source_derivative" else None,
            auto_attach_to_message=auto_attach,
            title=f"{artifact_id}.mp3",
        )
    database.add_message("assistant-1", "session-1", "assistant", "done")

    assert database.attach_runtime_artifacts_to_message(
        session_id="session-1",
        run_id="run-1",
        message_id="assistant-1",
    ) == 1
    with database.get_connection() as conn:
        rows = {
            row["id"]: row["message_id"]
            for row in conn.execute("SELECT id, message_id FROM runtime_artifacts").fetchall()
        }
    assert rows == {
        "art-visible": "assistant-1",
        "art-hidden": None,
        "art-source": None,
    }
    assert {item["artifactId"] for item in database.list_runtime_artifacts(session_id="session-1")} == {"art-hidden", "art-visible"}


def test_legacy_uploaded_input_is_reprojected_out_of_assistant_artifacts_without_mutation():
    row = {
        "id": "assistant-1",
        "role": "assistant",
        "run_id": "run-1",
        "state": "completed",
        "version": 1,
        "content_text": "done",
        "reasoning_text": "",
        "metadata": {"timestamp": 1},
        "nodes": [{"id": "narrative", "kind": "narrative", "role": "assistant", "content": "done"}],
        "artifacts": [
            {"id": "legacy-upload", "kind": "image", "workspacePath": ".v8/uploads/input.png", "metadata": {"source": "os_web_upload"}},
            {"id": "real-output", "kind": "image", "workspacePath": "out/result.png", "metadata": {"origin": "provider_result"}},
        ],
    }

    projected = format_canonical_message(row)

    assert [artifact["id"] for artifact in projected["artifacts"]] == ["real-output"]
    assert row["artifacts"][0]["id"] == "legacy-upload"
