from __future__ import annotations

from types import SimpleNamespace

from core.background_model_output import (
    parse_background_json_object,
    sanitize_background_model_output,
)


def test_sanitizer_keeps_visible_text_and_strips_reasoning_fields() -> None:
    response = SimpleNamespace(
        content=[
            {"type": "reasoning", "text": '{"selected":["secret"]}'},
            {"type": "text", "text": '{"selected":["visible"]}'},
        ],
        additional_kwargs={"reasoning_content": "hidden chain"},
    )

    sanitized = sanitize_background_model_output(response)

    assert sanitized.text == '{"selected":["visible"]}'
    assert sanitized.reasoning_stripped is True
    assert "reasoning_content" in sanitized.stripped_keys
    assert "secret" not in sanitized.text


def test_sanitizer_strips_think_tags_from_string_content() -> None:
    response = SimpleNamespace(content='<think>{"unsafe": true}</think>\n{"ok": true}')

    parsed, sanitized, error = parse_background_json_object(response)

    assert error is None
    assert parsed == {"ok": True}
    assert sanitized.reasoning_stripped is True
    assert "unsafe" not in sanitized.text


def test_json_in_reasoning_only_is_not_accepted() -> None:
    response = SimpleNamespace(content=[{"type": "reasoning", "text": '{"decision":"allow"}'}])

    parsed, sanitized, error = parse_background_json_object(response)

    assert parsed is None
    assert error == "background_output_no_visible_text"
    assert sanitized.text == ""
    assert sanitized.reasoning_stripped is True


def test_no_think_visible_json_without_reasoning_is_accepted() -> None:
    response = SimpleNamespace(content=[{"type": "text", "text": '{"decision":"allow"}'}])

    parsed, sanitized, error = parse_background_json_object(response)

    assert error is None
    assert parsed == {"decision": "allow"}
    assert sanitized.text == '{"decision":"allow"}'
    assert sanitized.reasoning_stripped is False
    assert sanitized.reasoning_chars == 0
    assert sanitized.stripped_keys == ()
