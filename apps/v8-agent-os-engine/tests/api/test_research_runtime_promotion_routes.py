from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from api import platform_routes


def test_promote_research_evidence_distinguishes_missing_from_not_promotable(monkeypatch) -> None:
    monkeypatch.setattr(platform_routes, "get_evidence_bundle", lambda _bundle_id: None)

    with pytest.raises(HTTPException) as missing:
        asyncio.run(platform_routes.promote_research_runtime_experience({"evidenceBundleId": "missing"}))

    assert missing.value.status_code == 404
    assert missing.value.detail == "evidence_bundle_not_found"

    monkeypatch.setattr(
        platform_routes,
        "get_evidence_bundle",
        lambda _bundle_id: {"evidenceBundleId": "bundle-retry", "promotable": False},
    )
    monkeypatch.setattr(platform_routes, "promote_experience_pack", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as blocked:
        asyncio.run(platform_routes.promote_research_runtime_experience({"evidenceBundleId": "bundle-retry"}))

    assert blocked.value.status_code == 409
    assert blocked.value.detail == "evidence_bundle_not_promotable"


def test_promote_research_evidence_returns_pack_when_quality_gate_passes(monkeypatch) -> None:
    monkeypatch.setattr(
        platform_routes,
        "get_evidence_bundle",
        lambda _bundle_id: {"evidenceBundleId": "bundle-ready", "promotable": True},
    )
    monkeypatch.setattr(
        platform_routes,
        "promote_experience_pack",
        lambda *_args, **_kwargs: {"experiencePackId": "pack-ready"},
    )

    result = asyncio.run(
        platform_routes.promote_research_runtime_experience({"evidenceBundleId": "bundle-ready"})
    )

    assert result == {"ok": True, "item": {"experiencePackId": "pack-ready"}}
