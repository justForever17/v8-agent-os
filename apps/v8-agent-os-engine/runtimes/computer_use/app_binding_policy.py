from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


PROFILE_BINDING_CONFIDENCE_THRESHOLD = 0.86


@dataclass(slots=True)
class AppBindingDecision:
    requested_app_id: str | None
    resolved_app_id: str | None
    binding_mode: str
    binding_confidence: float
    binding_evidence: Dict[str, Any]
    profile_eligible: bool
    catalog_entry: Dict[str, Any] | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "requestedAppId": self.requested_app_id,
            "resolvedAppId": self.resolved_app_id,
            "bindingMode": self.binding_mode,
            "bindingConfidence": round(float(self.binding_confidence or 0.0), 3),
            "bindingEvidence": dict(self.binding_evidence or {}),
            "profileEligible": bool(self.profile_eligible),
        }


def resolve_app_binding(
    *,
    app_profiles: Any,
    app_catalog: Any,
    explicit_app_id: str | None = None,
    window_title: str | None = None,
    class_name: str | None = None,
    app_name: str | None = None,
    include_running: bool = True,
) -> AppBindingDecision:
    requested_app_id = str(explicit_app_id or "").strip() or None
    normalized_title = str(window_title or "").strip() or None
    normalized_class = str(class_name or "").strip() or None
    normalized_name = str(app_name or "").strip() or None

    if requested_app_id:
        catalog_entry = app_catalog.resolve_app(
            explicit_app_id=requested_app_id,
            app_name=normalized_name,
            window_title=normalized_title,
            class_name=normalized_class,
            include_running=include_running,
        )
        resolved_app_id = requested_app_id
        if catalog_entry is not None:
            resolved_app_id = str(catalog_entry.get("appId") or "").strip() or requested_app_id
        return AppBindingDecision(
            requested_app_id=requested_app_id,
            resolved_app_id=resolved_app_id,
            binding_mode="explicit",
            binding_confidence=1.0,
            binding_evidence={
                "source": "explicit_app_id",
                "windowTitle": normalized_title,
                "className": normalized_class,
                "appName": normalized_name,
            },
            profile_eligible=True,
            catalog_entry=dict(catalog_entry or {}) if catalog_entry else None,
        )

    profile_id = app_profiles.infer(
        explicit_app_id=None,
        window_title=normalized_title,
        class_name=normalized_class,
        app_name=normalized_name,
    )
    if profile_id:
        catalog_entry = app_catalog.resolve_app(
            explicit_app_id=profile_id,
            app_name=normalized_name,
            window_title=normalized_title,
            class_name=normalized_class,
            include_running=include_running,
        )
        resolved_app_id = str(profile_id or "").strip() or None
        return AppBindingDecision(
            requested_app_id=None,
            resolved_app_id=resolved_app_id,
            binding_mode="heuristic",
            binding_confidence=0.9,
            binding_evidence={
                "source": "app_profile_infer",
                "windowTitle": normalized_title,
                "className": normalized_class,
                "appName": normalized_name,
            },
            profile_eligible=True,
            catalog_entry=dict(catalog_entry or {}) if catalog_entry else None,
        )

    catalog_entry = app_catalog.resolve_app(
        explicit_app_id=None,
        app_name=normalized_name,
        window_title=normalized_title,
        class_name=normalized_class,
        include_running=include_running,
    )
    if catalog_entry is not None:
        resolved_app_id = str(catalog_entry.get("appId") or "").strip() or None
        confidence = 0.74 if normalized_name else 0.68
        profile_eligible = confidence >= PROFILE_BINDING_CONFIDENCE_THRESHOLD
        return AppBindingDecision(
            requested_app_id=None,
            resolved_app_id=resolved_app_id,
            binding_mode="heuristic",
            binding_confidence=confidence,
            binding_evidence={
                "source": "app_catalog_resolve",
                "windowTitle": normalized_title,
                "className": normalized_class,
                "appName": normalized_name,
                "catalogDisplayName": catalog_entry.get("displayName"),
            },
            profile_eligible=profile_eligible,
            catalog_entry=dict(catalog_entry),
        )

    return AppBindingDecision(
        requested_app_id=requested_app_id,
        resolved_app_id=None,
        binding_mode="none",
        binding_confidence=0.0,
        binding_evidence={
            "source": "unresolved",
            "windowTitle": normalized_title,
            "className": normalized_class,
            "appName": normalized_name,
        },
        profile_eligible=False,
        catalog_entry=None,
    )
