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


def test_direct_image_preferences_save_to_their_existing_owners(monkeypatch):
    import pytest
    from fastapi import HTTPException
    supervisor = {"profile": {"name": "fixture"}, "compressedDirectImages": False}
    desktop = {"compressedFrameInput": False}
    monkeypatch.setattr(config_registry_routes.storage, "get_supervisor_config", lambda: supervisor.copy())
    monkeypatch.setattr(config_registry_routes.storage, "save_supervisor_config", lambda value: supervisor.update(value))
    monkeypatch.setattr(config_registry_routes, "_build_supervisor_domain", lambda: supervisor.copy())
    monkeypatch.setattr(config_registry_routes.storage, "get_computer_use_config", lambda: desktop.copy())
    monkeypatch.setattr(config_registry_routes.storage, "save_computer_use_config", lambda value: desktop.update(value))
    monkeypatch.setattr(config_registry_routes, "_build_computer_use_domain", lambda: desktop.copy())
    monkeypatch.setattr(config_registry_routes, "_update_role_bindings", lambda _: None)
    for enabled in (True, False):
        assert config_registry_routes._save_supervisor_domain({"data": {"compressedDirectImages": enabled}})["compressedDirectImages"] is enabled
        assert config_registry_routes._save_computer_use_domain({"data": {"compressedFrameInput": enabled}})["compressedFrameInput"] is enabled
    assert supervisor["profile"]["name"] == "fixture"
    for save, key in [(config_registry_routes._save_supervisor_domain, "compressedDirectImages"),
                      (config_registry_routes._save_computer_use_domain, "compressedFrameInput")]:
        with pytest.raises(HTTPException):
            save({"data": {key: "false"}})
