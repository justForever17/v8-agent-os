from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def test_web_and_phone_thinking_cards_are_reached_from_canonical_reasoning_nodes():
    web = (ROOT / "apps/v8-agent-os-web/src/components/chat/ContentDispatcher.tsx").read_text(encoding="utf-8")
    phone = (ROOT / "apps/v8-agent-os-phone/src/components/chat/ContentDispatcher.tsx").read_text(encoding="utf-8")
    assert "node.executionType === 'reasoning'" in web
    assert "executionNode.executionType === \"reasoning\"" in phone
    assert "type: \"thinking\"" in phone


def test_client_thinking_surface_never_reads_provider_opaque_payload_keys():
    files = [
        ROOT / "apps/v8-agent-os-web/src/components/chat/ContentDispatcher.tsx",
        ROOT / "apps/v8-agent-os-web/src/components/chat/ThinkingCard.tsx",
        ROOT / "apps/v8-agent-os-phone/src/components/chat/ContentDispatcher.tsx",
        ROOT / "apps/v8-agent-os-phone/src/components/chat/MessageBlockItem.tsx",
        ROOT / "apps/v8-agent-os-phone/src/components/chat/ThinkingCard.tsx",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for forbidden in ("encrypted_content", "_v8_provider_continuation", "thoughtSignature", "thought_signature"):
        assert forbidden not in source
