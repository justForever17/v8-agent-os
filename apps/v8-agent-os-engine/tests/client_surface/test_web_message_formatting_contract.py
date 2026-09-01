from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_web_user_messages_preserve_loose_numbered_lists_without_rewriting_storage() -> None:
    source = _read("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx")

    assert "normalizeHumanAuthoredListMarkers(message.content" in source
    assert 'message.role === "user"' in source
    assert 'content.split("\\n")' in source
    assert '.join("\\n")' in source


def test_runtime_activity_detail_preserves_source_line_breaks() -> None:
    source = _read("apps/v8-agent-os-web/src/components/workbench/RuntimeActivityRenderer.tsx")

    assert source.count("whitespace-pre-wrap break-words") >= 2
