from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SESSION_REALTIME_SRC = REPO_ROOT / "packages" / "session-realtime" / "src"


def test_planner_subagent_and_engineering_lanes_do_not_enter_message_lifecycle():
    lifecycle_source = (SESSION_REALTIME_SRC / "message-lifecycle.ts").read_text(encoding="utf-8")

    assert 'event.runtimeId === "planner_lane"' in lifecycle_source
    assert 'event.runtimeId === "subagent_swarm"' in lifecycle_source
    assert 'event.runtimeId === "engineering"' in lifecycle_source
    assert 'event.runtimeId === "engineering_lane"' in lifecycle_source
    assert 'String(event.topic || "").startsWith("planner.")' in lifecycle_source
    assert 'String(event.topic || "").startsWith("subagent.")' in lifecycle_source
    assert 'String(event.topic || "").startsWith("engineering.")' in lifecycle_source
    assert 'String(event.topic || "").startsWith("engineering_lane.")' in lifecycle_source
    assert 'String(event.topic || "").startsWith("chat.planner_mode.")' in lifecycle_source
    assert 'String(event.topic || "").startsWith("chat.task_planning_mode.")' in lifecycle_source


def test_runtime_taxonomy_keeps_planner_swarm_and_engineering_on_separate_lanes():
    taxonomy_source = (SESSION_REALTIME_SRC / "event-taxonomy.ts").read_text(encoding="utf-8")
    contract_source = (SESSION_REALTIME_SRC / "contract.ts").read_text(encoding="utf-8")

    assert 'key: "planner.lifecycle", topicPattern: "planner.", runtimeId: "planner_lane"' in taxonomy_source
    assert 'key: "subagent.lifecycle", topicPattern: "subagent.", runtimeId: "subagent_swarm"' in taxonomy_source
    assert 'key: "subagent.delegation", topicPattern: "delegation.", runtimeId: "subagent_swarm"' in taxonomy_source
    assert 'key: "engineering.trigger", topicPattern: "engineering_lane.", runtimeId: "engineering"' in taxonomy_source
    assert 'key: "engineering.lifecycle", topicPattern: "engineering.", runtimeId: "engineering"' in taxonomy_source
    assert 'targets: ["runtime_card", "runtime_timeline", "hud", "artifact"]' in taxonomy_source
    assert '"engineering",' in contract_source
    assert '"engineering_lane",' in contract_source

