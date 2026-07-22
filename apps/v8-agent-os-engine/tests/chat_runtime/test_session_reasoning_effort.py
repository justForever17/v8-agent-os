from api.models import ChatRequest, ChatRequestData, EngineConfig
from runtimes.chat import runtime as chat_runtime_module
from runtimes.chat.runtime import ChatRuntime


def _web_shaped_request(*, session_id: str, data: ChatRequestData | None = None) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "local intercepted dry-run"}],
            "session_id": session_id,
            "conversationId": session_id,
            "user_id": "anonymous",
            "workspacePath": "E:/Projects/v8chat",
            "workspaceId": "v8chat",
            "projectId": "v8chat",
            "scopeMode": "explicit",
            "config": {"provider": "test-provider", "model_name": "test-model"},
            "data": data.model_dump(by_alias=True, exclude_none=True) if data else None,
        }
    )


def test_web_request_without_per_message_field_uses_session_reasoning_override(monkeypatch):
    session_id = "session-reasoning-web"
    monkeypatch.setattr(
        chat_runtime_module.db,
        "get_session",
        lambda value: {
            "id": value,
            "user_id": "anonymous",
            "metadata": {"supervisorReasoningEffortOverride": "high"},
        },
    )
    monkeypatch.setattr(ChatRuntime, "_resolve_engine_config", lambda _self, _request: None)
    request = _web_shaped_request(session_id=session_id)

    ChatRuntime().prepare_request(request)

    assert request.config.supervisor_reasoning_effort == "high"


def test_legacy_explicit_message_field_wins_without_creating_duplicate_config(monkeypatch):
    session_id = "session-reasoning-legacy"
    monkeypatch.setattr(
        chat_runtime_module.db,
        "get_session",
        lambda value: {
            "id": value,
            "user_id": "anonymous",
            "metadata": {"supervisorReasoningEffortOverride": "high"},
        },
    )
    monkeypatch.setattr(ChatRuntime, "_resolve_engine_config", lambda _self, _request: None)
    request = _web_shaped_request(
        session_id=session_id,
        data=ChatRequestData(supervisorReasoningEffort="low"),
    )

    ChatRuntime().prepare_request(request)

    assert request.config == EngineConfig(
        provider="test-provider",
        model_name="test-model",
        supervisorReasoningEffort="low",
    )
