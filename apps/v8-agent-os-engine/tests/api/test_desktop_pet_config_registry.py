from api import config_registry_routes
from core.storage import STRUCTURED_CONFIG_DEFAULTS


def test_desktop_pet_defaults_use_structured_events():
    rules = STRUCTURED_CONFIG_DEFAULTS["desktopPet"]["actionTable"]

    assert rules
    assert all(rule.get("event") for rule in rules)
    assert all("match" not in rule for rule in rules)
    assert {rule["event"] for rule in rules} >= {
        "tool.started",
        "tool.finished",
        "ask_user.requested",
        "approval.requested",
        "run.completed",
        "run.failed",
    }


def test_desktop_pet_domain_preserves_structured_event_rules(monkeypatch):
    saved = {}

    monkeypatch.setattr(config_registry_routes.storage, "save_desktop_pet_config", lambda data: saved.update(data))
    monkeypatch.setattr(config_registry_routes.storage, "get_desktop_pet_config", lambda: saved)

    payload = config_registry_routes._save_desktop_pet_domain({
        "data": {
            "actionTable": [
                {"id": "tool-start", "event": "tool.started", "emotion": "tool_calling", "spectrum": "blue"},
            ],
        },
    })

    assert saved["actionTable"][0]["event"] == "tool.started"
    assert payload["data"] == saved
