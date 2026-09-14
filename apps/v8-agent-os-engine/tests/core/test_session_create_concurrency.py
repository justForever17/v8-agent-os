from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import uuid

import pytest

from core.database import db


@pytest.mark.parametrize("existing", [False, True])
def test_concurrent_session_admission_preserves_both_metadata_updates(monkeypatch, existing):
    session_id = "concurrent-admission-" + uuid.uuid4().hex
    if existing:
        db.create_or_update_session(session_id, "User title", user_id="fixture-owner",
                                    metadata={"manualTitle": True, "preserved": "original"})
    original_connection = db.get_connection
    first_read = threading.Event()
    second_read = threading.Event()
    local = threading.local()

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor
            self.session_read = False

        def execute(self, sql, parameters=()):
            self.session_read = sql == "SELECT id, metadata FROM sessions WHERE id = ?"
            self.cursor.execute(sql, parameters)
            return self

        def fetchone(self):
            row = self.cursor.fetchone()
            if self.session_read:
                if local.actor == "first":
                    first_read.set()
                    # Old SELECT -> INSERT/UPDATE permits the other reader to
                    # see the same preimage. A transaction serializes it until
                    # this real SQLite writer commits after the bounded wait.
                    second_read.wait(1)
                else:
                    second_read.set()
            return row

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def cursor(self):
            return Cursor(self.connection.cursor())

        def __getattr__(self, name):
            return getattr(self.connection, name)

    @contextmanager
    def observed_connection():
        with original_connection() as connection:
            yield Connection(connection)

    monkeypatch.setattr(db, "get_connection", observed_connection)

    def admit(actor):
        local.actor = actor
        db.create_or_update_session(session_id, "Agent title", user_id="fixture-owner", metadata={actor: True})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(admit, "first")
        assert first_read.wait(5)
        second = pool.submit(admit, "second")
        first.result(timeout=10)
        second.result(timeout=10)
    monkeypatch.setattr(db, "get_connection", original_connection)
    session = db.get_session(session_id)
    assert session["metadata"]["first"] is True
    assert session["metadata"]["second"] is True
    assert session["user_id"] == "fixture-owner"
    if existing:
        assert session["title"] == "User title"
        assert session["metadata"]["preserved"] == "original"
