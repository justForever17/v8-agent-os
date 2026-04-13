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
    ) -> str:
        return memory_store.build_session_context(
            user_query=user_query,
            scope=scope,
            scope_chain=scope_chain,
        )

    def get_recent_logs(self, *, days: int = 2, scope_chain: Optional[List[str]] = None) -> str:
        return memory_store.get_recent_logs(days=days, scope_chain=scope_chain)

    def read_memory_summary(self, *, tier: str, date_str: Optional[str] = None) -> str:
        return memory_store.read_memory_summary(tier=tier, date_str=date_str)

    def save_periodic_summary(self, *, tier: str, content: str, dt: Optional[datetime] = None) -> None:
        memory_store.save_periodic_summary(tier=tier, content=content, dt=dt)

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
