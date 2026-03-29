from __future__ import annotations

from typing import List

from core.database import db
from runtimes.memory.models import ScopeResolutionEvent


class ScopeResolutionRepository:
    def append_event(self, event: ScopeResolutionEvent) -> ScopeResolutionEvent:
        db.add_scope_resolution_event(event.model_dump(exclude_none=True))
        return event

    def list_events(self, session_id: str) -> List[ScopeResolutionEvent]:
        rows = db.get_scope_resolution_events(session_id)
        return [ScopeResolutionEvent.model_validate(item) for item in rows]
