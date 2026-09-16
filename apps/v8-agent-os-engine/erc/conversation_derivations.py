"""Recoverable invalidation of Memory projections after a transcript commit."""
from __future__ import annotations


def ensure_conversation_derivations_current(database, session_id: str) -> None:
    state = database.get_chat_transcript_state(session_id)
    if state["derived_context_epoch"] == state["context_epoch"]:
        return
    # This is deliberately replayable. A crash between databases leaves the
    # state epoch pending and the next execution retries before reading Memory.
    from core.knowledge_db import knowledge_db
    knowledge_db.mark_stale_for_conversation_revision(session_id=session_id, revised_at=state["updated_at"])
    with database.get_connection() as conn:
        conn.execute("UPDATE chat_session_transcript_state SET derived_context_epoch=? WHERE session_id=? AND context_epoch=?",
                     (state["context_epoch"], session_id, state["context_epoch"]))
        conn.commit()
