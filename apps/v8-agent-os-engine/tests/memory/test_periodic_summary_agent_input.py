from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

from agents import memory_agent


def test_unchanged_periodic_source_refreshes_coverage_without_model_call():
    prepared = {
        "content": "[2026-04-14] Ref: memory://day/2026-04-14\nSummary: stable",
        "model_content": "must not be sent",
        "blocks": [{"ref": "memory://day/2026-04-14"}],
        "source_digest": "digest-stable",
        "source_range_start": "2026-04-13",
        "source_range_end": "2026-04-19",
        "source_refs": ["memory://day/2026-04-14"],
        "source_evidence": ["memory://day/2026-04-14|abc"],
        "changed_source_refs": [],
        "removed_source_refs": [],
        "semantic_changed": False,
        "existing_verified": True,
        "existing_summary": "Stable weekly continuity.",
        "existing_body": "## Continuity\n\n- Stable.",
    }

    with (
        patch.object(memory_agent.memory_runtime, "prepare_periodic_summary_input", return_value=prepared),
        patch.object(memory_agent.memory_runtime, "save_periodic_summary") as save_summary,
        patch.object(memory_agent, "_synthesize_periodic_summary_payload", new_callable=AsyncMock) as synthesize,
        patch.object(memory_agent.audit_logger, "log"),
    ):
        result = asyncio.run(
            memory_agent.generate_periodic_summary(
                tier="week",
                target_date=datetime(2026, 4, 19),
                trigger_source="CRON",
            )
        )

    synthesize.assert_not_awaited()
    assert result["status"] == "completed"
    assert result["model_invoked"] is False
    assert result["reason"] == "source_content_unchanged"
    saved_payload = save_summary.call_args.kwargs["payload"]
    assert saved_payload["summary"] == "Stable weekly continuity."
    assert saved_payload["sourceMetadata"]["sourceDigest"] == "digest-stable"
