from core.audio import routes as audio_routes


def test_audio_input_status_prefers_configured_stt(monkeypatch):
    monkeypatch.setattr(
        audio_routes.AudioConfigManager,
        "get_config",
        staticmethod(lambda: {
            "stt": {
                "active_provider": "custom",
                "providers": {
                    "custom": {"endpoint": "https://stt.example.test/transcribe"},
                },
            },
        }),
    )
    monkeypatch.setattr(
        audio_routes.model_control_plane,
        "resolve_model_for_role",
        lambda role: {
            "resolvedModelId": "plain-vision-model",
            "resolvedModel": {"capabilities": {"vision": True}},
            "resolvedProviderId": "custom",
        },
    )

    status = audio_routes.build_audio_input_status()

    assert status["route"] == "stt"
    assert status["stt"]["usable"] is True


def test_audio_input_status_matches_audio_model_by_model_id_under_custom_provider(monkeypatch):
    monkeypatch.setattr(
        audio_routes.AudioConfigManager,
        "get_config",
        staticmethod(lambda: {
            "stt": {
                "active_provider": "baidu",
                "providers": {
                    "baidu": {"api_key": "", "secret_key": ""},
                },
            },
        }),
    )
    monkeypatch.setattr(
        audio_routes.model_control_plane,
        "resolve_model_for_role",
        lambda role: {
            "resolvedModelId": "doubao-seed-2-0-lite-260428",
            "resolvedModelRef": "volcengine-coding::doubao-seed-2-0-lite-260428",
            "resolvedProviderId": "volcengine-coding",
            "resolvedModel": {"capabilities": {"chat": True, "vision": True}},
        },
    )

    status = audio_routes.build_audio_input_status()

    assert status["route"] == "vision_audio"
    assert status["visionAudio"]["usable"] is True
    assert status["visionAudio"]["providerId"] == "volcengine-coding"


def test_audio_input_status_matches_compact_doubao_audio_model_name(monkeypatch):
    monkeypatch.setattr(
        audio_routes.AudioConfigManager,
        "get_config",
        staticmethod(lambda: {
            "stt": {
                "active_provider": "baidu",
                "providers": {
                    "baidu": {"api_key": "", "secret_key": ""},
                },
            },
        }),
    )
    monkeypatch.setattr(
        audio_routes.model_control_plane,
        "resolve_model_for_role",
        lambda role: {
            "resolvedModelId": "doubao-seed2.0-lite",
            "resolvedProviderId": "volcengine-coding",
            "resolvedModel": {"capabilities": {"chat": True, "vision": True}},
        },
    )

    status = audio_routes.build_audio_input_status()

    assert status["route"] == "vision_audio"
    assert status["visionAudio"]["usable"] is True


def test_audio_input_status_reports_unavailable_without_stt_or_audio_model(monkeypatch):
    monkeypatch.setattr(
        audio_routes.AudioConfigManager,
        "get_config",
        staticmethod(lambda: {
            "stt": {
                "active_provider": "model_ref",
                "providers": {},
            },
        }),
    )
    monkeypatch.setattr(
        audio_routes.model_control_plane,
        "resolve_model_for_role",
        lambda role: {
            "resolvedModelId": "plain-vision-model",
            "resolvedModelRef": "custom::plain-vision-model",
            "resolvedProviderId": "custom",
            "resolvedModel": {"capabilities": {"vision": True}},
        },
    )

    status = audio_routes.build_audio_input_status()

    assert status["route"] == "unavailable"
    assert status["stt"]["reason"] == "model_ref_stt_adapter_not_enabled"
    assert status["visionAudio"]["usable"] is False
