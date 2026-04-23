from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from core.memory.store import memory_store


class InjectionService:
    """封装上下文注入、摘要与近期日志能力。"""

    def build_session_context(
        self,
        *,
        user_query: str,
        scope: str = "global",
        scope_chain: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        suppress_daily_memory: bool = False,
        suppress_memory_map: bool = False,
    ) -> str:
        return memory_store.build_session_context(
            user_query=user_query,
            scope=scope,
            scope_chain=scope_chain,
            session_id=session_id,
            run_id=run_id,
            suppress_daily_memory=suppress_daily_memory,
            suppress_memory_map=suppress_memory_map,
        )

    def get_recent_logs(self, *, days: int = 2, scope_chain: Optional[List[str]] = None) -> str:
        return memory_store.get_recent_logs(days=days, scope_chain=scope_chain)

    def read_memory_summary(self, *, tier: str, date_str: Optional[str] = None) -> str:
        return memory_store.read_memory_summary(tier=tier, date_str=date_str)

    def build_memory_map(self, *, anchor_date: Optional[str] = None) -> dict:
        return memory_store.build_memory_map(anchor_date=anchor_date)

    def expand_memory_map(self, *, memory_ref: str) -> dict:
        return memory_store.expand_memory_map(memory_ref)

    def read_memory_day(self, *, memory_ref_or_date: str) -> str:
        return memory_store.read_memory_day(memory_ref_or_date)

    def get_memory_map_health(self) -> dict:
        return memory_store.get_memory_map_health()

    def list_summary_targets(self, *, states: Optional[List[str]] = None) -> list[dict]:
        return memory_store.list_summary_targets(states=states)

    def get_logs_for_period(self, *, tier: str, dt: Optional[datetime] = None, scope_chain: Optional[List[str]] = None) -> str:
        return memory_store.get_logs_for_period(tier=tier, dt=dt, scope_chain=scope_chain)

    def save_periodic_summary(self, *, tier: str, payload: dict, dt: Optional[datetime] = None) -> None:
        memory_store.save_periodic_summary(tier=tier, payload=payload, dt=dt)

    def backfill_periodic_summaries(self) -> dict:
        return memory_store.backfill_periodic_summaries()

    def append_daily_log(self, *, content: str, tags: Optional[List[str]] = None) -> None:
        memory_store.append_daily_log(content=content, tags=tags)

    def append_daily_log_with_yaml(
        self,
        *,
        content: str,
        session_summary: str,
        session_tags: List[str],
        entry_metadata: Optional[dict] = None,
    ) -> None:
        memory_store.append_daily_log_with_yaml(
            content=content,
            session_summary=session_summary,
            session_tags=session_tags,
            entry_metadata=entry_metadata,
        )


injection_service = InjectionService()
