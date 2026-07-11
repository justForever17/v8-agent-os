from api.models import ChatRequest
from runtimes.chat.runtime import ChatRuntime


def _request(data=None) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "data": data or {},
        }
    )


def test_no_plugin_reference_produces_no_plugin_mention() -> None:
    mentions = ChatRuntime()._normalize_context_mentions(_request(), skill_references=[])
    assert all(item["kind"] != "plugin" for item in mentions)


def test_plugin_reference_is_normalized_as_explicit_task_grant() -> None:
    mentions = ChatRuntime()._normalize_context_mentions(
        _request({"pluginReferences": [{"pluginId": "figma", "name": "Figma"}]}),
        skill_references=[],
    )
    assert mentions == [
        {
            "kind": "plugin",
            "id": "figma",
            "name": "Figma",
            "label": "Figma",
            "description": "",
            "path": "",
            "familyId": "",
            "sourceType": "plugin_reference",
            "grantScope": "task",
            "componentIds": [],
        }
    ]


def test_plugin_reference_can_explicitly_request_session_scope() -> None:
    mentions = ChatRuntime()._normalize_context_mentions(
        _request({"pluginReferences": [{"pluginId": "figma", "scope": "session"}]}),
        skill_references=[],
    )
    assert mentions[0]["grantScope"] == "session"


def test_plugin_shaped_context_mention_is_not_an_authorization_input() -> None:
    mentions = ChatRuntime()._normalize_context_mentions(
        _request(
            {
                "contextMentions": [
                    {"kind": "plugin", "id": "figma", "grantScope": "session"},
                ]
            }
        ),
        skill_references=[],
    )
    assert all(item["kind"] != "plugin" for item in mentions)


def test_plugin_reference_snapshot_is_deduplicated_and_component_scoped() -> None:
    request = _request(
        {
            "pluginReferences": [
                {"pluginId": "Figma", "scope": "task", "componentIds": ["mcp", "mcp", "ui"]},
                {"pluginId": "figma", "scope": "task", "componentIds": ["ui", "mcp"]},
            ]
        }
    )
    assert ChatRuntime._normalize_plugin_references(request) == [
        {
            "pluginId": "figma",
            "name": "figma",
            "scope": "task",
            "componentIds": ["mcp", "ui"],
        }
    ]
