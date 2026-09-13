"""Explicit synthetic data preparation using the production Database owner.

Creates canonical history and pending queue rows for two sessions previously
created through the real authenticated Web/Admin API. Does not create a run,
submit chat, call a provider, monkeypatch APIs or write SQL directly.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

EXPECTED_STATE = Path("E:/Projects/v8chat/.codex-tmp/release-20260913-1/preview-state-1").resolve()
EXPECTED_REPO = Path("E:/Projects/v8chat/.codex-worktrees/9131-integration").resolve()
OWN = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--allow-side-effects", action="store_true", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    state, repo, manifest = args.state_root.resolve(), args.repo.resolve(), args.manifest.resolve()
    assert state == EXPECTED_STATE and state.is_dir(), "Only the explicitly handed-off Preview fixture state is permitted"
    assert repo == EXPECTED_REPO
    assert manifest.is_relative_to(OWN / "tmp/experience-9131")
    records = json.loads(manifest.read_text(encoding="utf-8"))
    assert set(records) == {"A", "B"}
    assert records["A"]["id"] != records["B"]["id"]
    os.environ["V8_AGENT_OS_HOME"] = str(state)
    sys.path.insert(0, str(repo / "apps/v8-agent-os-engine"))
    from core.v8_agent_os_paths import STATE_DB_PATH
    assert Path(STATE_DB_PATH).resolve().is_relative_to(state)
    from core.database import db
    assert db.db_path.resolve() == Path(STATE_DB_PATH).resolve()
    # Validate all exact targets before the first mutation.
    for label, record in records.items():
        session = db.get_session(record["id"])
        assert session and session["title"] == f"Independent Shell {label}"
        assert session["user_id"] == record["userId"]
        # The Web /conversations proxy does not forward arbitrary metadata.
        # Exact API-returned UUID + public fixture owner + title + empty state
        # bind this setup to the two sessions created by the current UI run.
        assert not db.get_chat_canonical_messages(record["id"]), "Refuse to overwrite existing history"
        assert not db.list_chat_user_message_queue(session_id=record["id"], limit=100), "Refuse to overwrite an existing queue"
    results = []
    for label, record in records.items():
        session_id = record["id"]
        history = "\n\n".join(f"{label} paragraph {index:03d} — Synthetic history for scrolling and selection. 这段内容只用于真实桌面保留测试。" for index in range(1, 81 if label == "A" else 9))
        messages = [("user", f"Independent Shell {label} synthetic history request"), ("assistant", history)]
        for ordinal, (role, content) in enumerate(messages, 1):
            message_id = f"experience-{session_id}-{ordinal}"
            db.create_chat_canonical_message(message_id=message_id, session_id=session_id,
                run_id=None, ordinal=ordinal, role=role, state="completed",
                nodes=[{"id": message_id + "-text", "kind": "narrative", "role": role,
                        "content": content, "timestamp": 1789257600000, "finalized": True}],
                content_text=content, metadata={"experienceFixture": "9131-shell", "synthetic": True})
        queues = []
        for index in range(1, 3 if label == "A" else 2):
            queue_id = f"experience-queue-{session_id}-{index}"
            row = db.add_chat_user_message_queue_item(queue_id=queue_id, session_id=session_id,
                run_id=None, client_message_id=f"experience-client-{session_id}-{index}",
                content=f"Independent {label} pending queue {index}", request_payload={},
                metadata={"experienceFixture": "9131-shell", "synthetic": True, "executionNotRequested": True})
            assert row["state"] == "pending"
            queues.append(queue_id)
        results.append({"label": label, "sessionId": session_id, "messages": 2,
            "historySha256": hashlib.sha256(history.encode()).hexdigest(), "queueIds": queues,
            "runCreated": False, "providerCalled": False})
    output = manifest.with_name("seed-result.json")
    output.write_text(json.dumps({"stateRoot": str(state), "level": "synthetic rows via production Database owner", "results": results}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"seededSessions": len(results), "canonicalMessages": 4, "pendingQueueRows": 3,
                      "providerCalled": False, "report": str(output)}))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
