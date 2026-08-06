from __future__ import annotations

from typing import Any, Dict, List, Optional

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

    def list_preference_history(
        self,
        *,
        scope: Optional[str] = None,
        key: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return memory_store.list_preference_history(scope=scope, key=key, limit=limit)

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

    def update_preference(
        self,
        *,
        key: str,
        value: str,
        scope: str = "global",
        source: str = "human_admin",
        reason: str = "explicit_update",
        evidence_refs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return memory_store.update_preference(
            key=key,
            value=value,
            scope=scope,
            source=source,
            reason=reason,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    def delete_preference(
        self,
        *,
        key: str,
        scope: str = "global",
        source: str = "human_admin",
        reason: str = "explicit_delete",
        evidence_refs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return memory_store.delete_preference(
            key=key,
            scope=scope,
            source=source,
            reason=reason,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    def clear_supervisor_identity(
        self,
        *,
        key: str,
        source: str = "human_admin",
        reason: str = "explicit_identity_revoke",
        evidence_refs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return memory_store.clear_supervisor_identity(
            key=key,
            source=source,
            reason=reason,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    def update_supervisor_identity(
        self,
        *,
        assistant_name: Optional[str] = None,
        user_call_name: Optional[str] = None,
        source: str = "human_admin",
        reason: str = "explicit_identity_update",
        evidence_refs: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return memory_store.update_supervisor_identity(
            assistant_name=assistant_name,
            user_call_name=user_call_name,
            source=source,
            reason=reason,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    def migrate_scoped_identity_to_global(
        self,
        *,
        scope: str,
        source: str = "memory_migration",
        reason: str = "legacy_scoped_identity_migration",
        evidence_refs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return memory_store.migrate_scoped_identity_to_global(
            scope=scope,
            source=source,
            reason=reason,
            evidence_refs=evidence_refs,
        )

    def list_global_preference_quarantine(self) -> List[Dict[str, object]]:
        return memory_store.load_global_preference_quarantine()

    def restore_global_preference_quarantine(self, *, record_id: str) -> Optional[Dict[str, object]]:
        return memory_store.restore_global_preference_quarantine(record_id)

    def delete_global_preference_quarantine(self, *, record_id: str) -> bool:
        return memory_store.delete_global_preference_quarantine(record_id)


profile_service = ProfileService()
