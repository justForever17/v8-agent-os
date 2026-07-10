from api import config_registry_routes


def test_ui_domain_defaults_to_system(monkeypatch):
    monkeypatch.setattr(config_registry_routes.storage, "get_ui_config", lambda: {"theme": "system"})

    payload = config_registry_routes._build_ui_domain()

    assert payload["domain"] == "ui"
    assert payload["data"] == {"theme": "system"}
    assert payload["reloadRequired"] is False


def test_ui_domain_save_uses_canonical_storage(monkeypatch):
    saved = {}

    def save_ui_config(data):
        saved.update(data)

    monkeypatch.setattr(config_registry_routes.storage, "save_ui_config", save_ui_config)
    monkeypatch.setattr(config_registry_routes.storage, "get_ui_config", lambda: {"theme": saved.get("theme", "system")})

    payload = config_registry_routes._save_ui_domain({"data": {"theme": "dark"}})

    assert saved == {"theme": "dark"}
    assert payload["data"] == {"theme": "dark"}


def test_ui_domain_is_registered():
    assert "ui" in config_registry_routes.DOMAIN_REGISTRY
