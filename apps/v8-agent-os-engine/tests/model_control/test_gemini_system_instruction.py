from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from core.llm_chat_adapter import create_provider_surface
from core.llm_factory import LLMFactory


def test_gemini_surface_preserves_langchain_system_messages() -> None:
    messages = [
        SystemMessage(content="native system"),
        HumanMessage(content="hello"),
    ]

    normalized = create_provider_surface("gemini").normalize_messages(messages)

    assert normalized == messages
    assert isinstance(normalized[0], SystemMessage)
    assert "[System Instructions]" not in str(normalized[1].content)


@pytest.mark.parametrize(
    ("messages", "expected_system_parts", "expected_human_text"),
    [
        (
            [SystemMessage(content="native system"), HumanMessage(content="hello")],
            ["native system"],
            "hello",
        ),
        (
            [
                SystemMessage(content="first instruction"),
                SystemMessage(content="second instruction"),
                HumanMessage(content="hello"),
            ],
            ["first instruction", "second instruction"],
            "hello",
        ),
        (
            [
                SystemMessage(content=[{"type": "text", "text": "structured instruction"}]),
                HumanMessage(content=[{"type": "text", "text": "structured input"}]),
            ],
            ["structured instruction"],
            "structured input",
        ),
    ],
)
def test_langchain_google_genai_projects_system_messages_to_native_system_instruction(
    messages,
    expected_system_parts,
    expected_human_text,
) -> None:
    chat_models = pytest.importorskip("langchain_google_genai.chat_models")

    system_instruction, history = chat_models._parse_chat_history(
        messages,
        convert_system_message_to_human=False,
        model="gemini-3.5-flash",
    )

    assert system_instruction is not None
    assert [part.text for part in system_instruction.parts or []] == expected_system_parts
    assert [item.role for item in history] == ["user"]
    assert history[0].parts[0].text == expected_human_text


def test_gemini_custom_provider_base_url_is_preserved_exactly() -> None:
    kwargs = LLMFactory._build_gemini_kwargs(
        "gemini-3.5-flash-low",
        {
            "api_key": "test-key",
            "base_url": "https://provider.example.test/v1/",
        },
    )

    assert kwargs["client_options"] == "https://provider.example.test/v1"
    assert kwargs["api_version"] == ""


def test_gemini_channel_api_version_is_only_applied_when_explicit() -> None:
    kwargs = LLMFactory._build_gemini_kwargs(
        "gemini-3.5-flash-low",
        {
            "api_key": "test-key",
            "base_url": "https://provider.example.test/gemini",
            "api_version": "v1beta",
        },
    )

    assert kwargs["client_options"] == "https://provider.example.test/gemini"
    assert kwargs["api_version"] == "v1beta"
