from __future__ import annotations

import json

from agents.memory_agent import _repair_memory_extraction_payload


def test_memory_extraction_repair_drops_empty_entity():
    payload = {
        "summary": "用户在项目中调试 AI 狼人杀应用。",
        "tags": ["ai-werewolf", "project:test2"],
        "preferences": [],
        "knowledge": [],
        "entities": [
            {"name": "next.js", "type": "technology"},
            {},
        ],
        "relations": [],
        "workflow_episodes": [],
    }

    repaired = _repair_memory_extraction_payload(json.dumps(payload, ensure_ascii=False))

    assert len(repaired.entities) == 1
    assert repaired.entities[0].name == "next.js"
