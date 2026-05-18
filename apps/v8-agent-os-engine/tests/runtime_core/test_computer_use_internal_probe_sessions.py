from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from core.database import DatabaseManager
from runtimes.computer_use.runtime import ComputerUseRuntime


def test_internal_observe_probe_uses_hidden_stable_session_metadata() -> None:
    runtime = object.__new__(ComputerUseRuntime)

    with patch("runtimes.computer_use.runtime.db.get_session", return_value=None), patch(
        "runtimes.computer_use.runtime.db.create_or_update_session"
    ) as create_session, patch(
        "runtimes.computer_use.runtime.erc_kernel.submit_run",
        return_value=SimpleNamespace(run_id="run-computer-probe", session_id="computer_use:probe:test"),
    ) as submit_run:
        runtime.begin_run(goal="observe_desktop", trigger_source="computer_use_api")

    create_session.assert_called_once()
    session_kwargs = create_session.call_args.kwargs
    assert session_kwargs["session_id"].startswith("computer_use:probe:")
    assert session_kwargs["metadata"]["hiddenFromHistory"] is True
    assert session_kwargs["metadata"]["internalProbe"] is True
    assert session_kwargs["metadata"]["ephemeral"] is True
    submit_run.assert_called_once()
    assert submit_run.call_args.kwargs["session_id"] == session_kwargs["session_id"]


def test_explicit_computer_use_session_is_not_marked_internal_probe() -> None:
    runtime = object.__new__(ComputerUseRuntime)

    with patch("runtimes.computer_use.runtime.db.get_session", return_value=None), patch(
        "runtimes.computer_use.runtime.db.create_or_update_session"
    ) as create_session, patch(
        "runtimes.computer_use.runtime.erc_kernel.submit_run",
        return_value=SimpleNamespace(run_id="run-computer-chat", session_id="chat-session"),
    ):
        runtime.begin_run(
            session_id="chat-session",
            goal="observe_desktop",
            trigger_source="computer_use_api",
        )

    metadata = create_session.call_args.kwargs["metadata"]
    assert "hiddenFromHistory" not in metadata
    assert "internalProbe" not in metadata


def test_backfill_hides_legacy_computer_use_observer_sessions() -> None:
    with TemporaryDirectory() as tmp:
        manager = DatabaseManager(Path(tmp) / "state.db")
        session_id = "computer_use:legacy-observe"
        manager.create_or_update_session(
            session_id,
            "Computer Use · observe_desktop",
            metadata={
                "runtime": "computer_use",
                "goal": "observe_desktop",
                "trigger_source": "computer_use_api",
            },
        )
        with manager.get_connection() as conn:
            manager._backfill_internal_computer_use_probe_sessions(conn)
            conn.commit()

        session = manager.get_session(session_id)
        assert session is not None
        assert session["metadata"]["hiddenFromHistory"] is True
        assert session["metadata"]["internalProbe"] is True
        assert session["metadata"]["ephemeral"] is True
