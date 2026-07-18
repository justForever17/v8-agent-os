from __future__ import annotations

import json
from pathlib import Path


PROFILE_PATH = Path(__file__).resolve().parents[2] / "core" / "model_catalog" / "model_thinking_profiles.json"
KNOWN_LEVELS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}


def test_model_thinking_profile_catalog_has_stable_unique_contracts() -> None:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profiles = payload["profiles"]
    profile_ids = [str(profile["id"]) for profile in profiles]

    assert payload["schemaVersion"] == 1
    assert len(profile_ids) == len(set(profile_ids))
    assert set(payload["levelOrder"]) == KNOWN_LEVELS

    for profile in profiles:
        assert profile.get("modelPatterns")
        effort = profile.get("effort")
        if not effort:
            continue
        levels = effort.get("levels") or []
        assert levels
        assert set(levels).issubset(KNOWN_LEVELS)
        assert effort.get("defaultLevel") in levels
        assert profile.get("sourceRefs")
        assert all(str(source).startswith("https://") for source in profile["sourceRefs"])


def test_no_think_profiles_never_imply_support_when_explicitly_disabled() -> None:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    disabled_profiles = [
        profile
        for profile in payload["profiles"]
        if (profile.get("noThink") or {}).get("supported") is False
    ]

    assert {profile["id"] for profile in disabled_profiles} >= {
        "anthropic-fable-5",
        "anthropic-mythos-5",
        "gemini-3.5-flash",
    }
