from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from erc.canonical_model_events import LangChainCanonicalModelEventAdapter  # noqa: E402


def _event_types(events):
    return [event.event_type for event in events]


def assert_text_reasoning_classification() -> dict:
    adapter = LangChainCanonicalModelEventAdapter()

    text_snapshots: dict[str, str] = {}
    reasoning_snapshots: dict[str, str] = {}

    untrusted = adapter.normalize_chat_model_stream(
        {
            "event": "on_chat_model_stream",
            "run_id": "model_untrusted_reasoning",
            "data": {"chunk": {"additional_kwargs": {"reasoning_content": "我先检查工作区。"}}},
        },
        text_snapshots=text_snapshots,
        reasoning_snapshots=reasoning_snapshots,
    )
    assert _event_types(untrusted) == ["reasoning_suppressed"], _event_types(untrusted)
    assert untrusted[0].diagnostics.get("reasoningSuppressed") is True
    assert untrusted[0].diagnostics.get("surface") == "hidden"
    assert untrusted[0].snapshot == "我先检查工作区。"

    explicit = adapter.normalize_chat_model_stream(
        {
            "event": "on_chat_model_stream",
            "run_id": "model_explicit_reasoning",
            "metadata": {},
            "data": {"chunk": {"content": [{"type": "thinking", "text": "这是可信思考。"}]}},
        },
        text_snapshots={},
        reasoning_snapshots={},
        reasoning_surface={
            "mode": "typed_thinking",
            "trust": "official",
            "requestStyle": "anthropic_thinking",
            "responseFields": ["content[type=thinking]"],
            "displayKind": "raw_thinking",
        },
    )
    assert _event_types(explicit) == ["reasoning_delta"], _event_types(explicit)
    assert explicit[0].diagnostics.get("trustedReasoning") is True
    assert explicit[0].diagnostics.get("reasoningKind") == "raw_thinking"
    assert explicit[0].snapshot == "这是可信思考。"

    suppressed = adapter.normalize_chat_model_stream(
        {
            "event": "on_chat_model_stream",
            "run_id": "tool_internal_model",
            "metadata": {"v8_model_scope": "tool_internal"},
            "data": {
                "chunk": {
                    "content": "内部正文",
                    "additional_kwargs": {"reasoning_content": "内部思考"},
                }
            },
        },
        text_snapshots={},
        reasoning_snapshots={},
    )
    assert suppressed == []

    return {
        "untrustedReasoning": "suppressed",
        "explicitThinking": "reasoning_delta",
        "toolInternal": "suppressed",
    }


def assert_shared_timeline_projection() -> dict:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    result = subprocess.run(
        [npm, "run", "verify:timeline"],
        cwd=REPO_ROOT / "packages" / "session-realtime",
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "command": "npm run verify:timeline",
        "stdout": result.stdout.strip(),
    }


def main() -> int:
    report = {
        "ok": True,
        "classification": assert_text_reasoning_classification(),
        "timelineProjection": assert_shared_timeline_projection(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
