from __future__ import annotations

from typing import Dict, List, Optional

from core.memory.store import memory_store


class ProfileService:
    """封装 MEMORY.md 偏好读写与注入格式化能力。"""

    def list_preferences(self) -> Dict[str, Dict[str, str]]:
        return memory_store._load_raw_preferences()

    def list_scopes(self) -> List[str]:
        return list(self.list_preferences().keys())

    def get_preference_count(self) -> int:
        return sum(len(values) for values in self.list_preferences().values())

    def get_global_profile_schema(self) -> Dict[str, object]:
        return memory_store.get_global_profile_schema()

    def load_preferences(
        self,
        *,
        scope: str = "global",
        scope_chain: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        return memory_store.load_preferences(scope=scope, scope_chain=scope_chain)

    def format_preferences_for_injection(
        self,
        *,
        scope: str = "global",
        scope_chain: Optional[List[str]] = None,
    ) -> str:
        return memory_store.format_preferences_for_injection(scope=scope, scope_chain=scope_chain)

    def update_preference(self, *, key: str, value: str, scope: str = "global", source: str = "human_admin") -> None:
        memory_store.update_preference(key=key, value=value, scope=scope, source=source)

    def delete_preference(self, *, key: str, scope: str = "global") -> bool:
        return memory_store.delete_preference(key=key, scope=scope)

    def list_global_preference_quarantine(self) -> List[Dict[str, object]]:
        return memory_store.load_global_preference_quarantine()

    def restore_global_preference_quarantine(self, *, record_id: str) -> Optional[Dict[str, object]]:
        return memory_store.restore_global_preference_quarantine(record_id)

    def delete_global_preference_quarantine(self, *, record_id: str) -> bool:
        return memory_store.delete_global_preference_quarantine(record_id)


profile_service = ProfileService()
