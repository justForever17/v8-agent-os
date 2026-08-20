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


def test_memory_extraction_repair_coerces_string_list_fields_without_truncating_workflow():
    payload = {
        "summary": "document workflow",
        "tags": "document-ingestion",
        "preferences": [],
        "knowledge": [],
        "entities": [],
        "relations": [],
        "workflow_episodes": [
            {
                "task_family": "read a document",
                "canonical_trigger_patterns": "read docx",
                "ordered_actions": "install the governed capability pack",
                "verification_steps": "the extracted text contains the expected heading",
            }
        ],
    }

    repaired = _repair_memory_extraction_payload(json.dumps(payload, ensure_ascii=False))

    assert repaired.tags == ["document-ingestion"]
    assert repaired.workflow_episodes[0].canonical_trigger_patterns == ["read docx"]
    assert repaired.workflow_episodes[0].verification_steps == ["the extracted text contains the expected heading"]
