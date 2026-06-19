from __future__ import annotations

from runtimes.extensions.runtime import _merge_keepalive_skills, _merge_recent_keepalive_skills


def test_recent_keepalive_skills_do_not_displace_current_query_hits():
    current_hit = {"skillId": "current", "name": "huashu-nuwa"}
    recent_keepalive = {"skillId": "recent", "name": "unrelated-recent-skill"}

    merged = _merge_recent_keepalive_skills([current_hit], [recent_keepalive], limit=1)

    assert [item["skillId"] for item in merged] == ["current"]


def test_theme_keepalive_still_can_prepend_advisory_fallbacks():
    current_hit = {"skillId": "current", "name": "elon-musk-perspective"}
    theme_fallback = {"skillId": "fallback", "name": "huashu-nuwa"}

    merged = _merge_keepalive_skills([current_hit], [theme_fallback], limit=1)

    assert [item["skillId"] for item in merged] == ["fallback"]
