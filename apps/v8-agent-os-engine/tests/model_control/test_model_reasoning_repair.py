from __future__ import annotations

from copy import deepcopy

from core.model_reasoning_repair import ModelReasoningRepairService


class FakeControlPlane:
    def __init__(self, config):
        self.config = deepcopy(config)
        self.saved = None

    def get_config(self):
        return deepcopy(self.config if self.saved is None else self.saved)

    def save_config(self, config):
        self.saved = deepcopy(config)

    def get_model_record(self, model_id, provider_id=""):
        provider = (self.config.get("providers") or {}).get(provider_id) or {}
        parsed_model_id = str(model_id).split("::", 1)[-1]
        model = (provider.get("models") or {}).get(parsed_model_id)
        if not model:
            return None
        return {"provider": provider.get("provider") or {}, "model": model}


def _base_config(reasoning_surface=None):
    return {
        "version": 2,
        "providers": {
            "demo": {
                "provider": {"name": "Demo", "api_standard": "openai"},
                "models": {
                    "demo-reasoner": {
                        "type": "TEXT",
                        "capabilityClass": "chat",
                        "reasoningSurface": reasoning_surface or {
                            "mode": "hidden",
                            "trust": "unknown",
                            "requestStyle": "none",
                            "responseFields": [],
                            "displayKind": "hidden",
                        },
                    }
                },
            }
        },
        "roles": {},
    }


def _service(tmp_path, config):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    service = ModelReasoningRepairService(control_plane=FakeControlPlane(config), config_path=config_path)
    service._resolve_metadata = lambda model_id, provider_id="": {  # type: ignore[method-assign]
        "is_found": True,
        "model_id": "demo-reasoner",
        "model_ref": "demo::demo-reasoner",
        "provider_id": "demo",
        "provider_name": "Demo",
        "api_standard": "openai",
        "capability_class": "chat",
        "provider_record": {"name": "Demo", "api_standard": "openai"},
        "model_record": deepcopy(((config.get("providers") or {})["demo"]["models"])["demo-reasoner"]),
    }
    return service


def test_reasoning_repair_registers_observed_adapter_field(tmp_path):
    service = _service(tmp_path, _base_config())
    service._run_probe = lambda runtime_model_id: {  # type: ignore[method-assign]
        "elapsedMs": 12,
        "streamingUsed": True,
        "reasoningTokens": 0,
        "payloads": [{"additional_kwargs": {"reasoning_content": "short reasoning"}, "content": "OK"}],
    }

    result = service.repair_reasoning_surface(model_id="demo-reasoner", provider_id="demo")

    assert result["ok"] is True
    assert result["saveStatus"] == "saved"
    assert result["matchedField"] == "additional_kwargs.reasoning_content"
    assert result["newReasoningSurface"]["mode"] == "provider_reasoning"
    assert result["newReasoningSurface"]["trust"] == "adapter_verified"
    assert result["backupPath"]


def test_reasoning_repair_does_not_fabricate_surface_for_token_only_signal(tmp_path):
    service = _service(tmp_path, _base_config())
    service._run_probe = lambda runtime_model_id: {  # type: ignore[method-assign]
        "elapsedMs": 12,
        "streamingUsed": True,
        "reasoningTokens": 8,
        "payloads": [{"response_metadata": {"token_usage": {"completion_tokens_details": {"reasoning_tokens": 8}}}, "content": "OK"}],
    }

    result = service.repair_reasoning_surface(model_id="demo-reasoner", provider_id="demo")

    assert result["ok"] is True
    assert result["status"] == "no_visible_reasoning_field"
    assert result["saveStatus"] == "no_change"
    assert service.control_plane.saved is None


def test_reasoning_repair_prefers_known_official_contract(tmp_path):
    official_surface = {
        "mode": "reasoning_summary",
        "trust": "official",
        "requestStyle": "openai_reasoning",
        "responseFields": ["additional_kwargs.reasoning"],
        "displayKind": "summary",
    }
    service = _service(tmp_path, _base_config(official_surface))
    service._run_probe = lambda runtime_model_id: {  # type: ignore[method-assign]
        "elapsedMs": 12,
        "streamingUsed": True,
        "reasoningTokens": 0,
        "payloads": [{"additional_kwargs": {"reasoning": "summary"}, "content": "OK"}],
    }

    result = service.repair_reasoning_surface(model_id="demo-reasoner", provider_id="demo")

    assert result["ok"] is True
    assert result["saveStatus"] == "saved"
    assert result["newReasoningSurface"]["mode"] == "reasoning_summary"
    assert result["newReasoningSurface"]["trust"] == "official"
    assert result["matchedField"] == "additional_kwargs.reasoning"
