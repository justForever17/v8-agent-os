from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from core.client_identity.service import ClientIdentityService
from core.database import DatabaseManager
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from core.v8_link import normalize_remote_link_config


@pytest.fixture
def phone_config(tmp_path, monkeypatch):
    from api import config_registry_routes as registry
    from core import client_identity
    import core.config_broker_service as broker_module
    import core.storage as storage_module

    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    monkeypatch.setattr(broker_module, "db", DatabaseManager(tmp_path / "state.db"))
    broker_module.storage.save_system_base_config(broker_module.storage.get_system_base_config())
    broker = broker_module.ConfigBrokerService()
    identity = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()),
                                    config_reader=broker_module.storage.get_system_base_config)
    identity.owners.bootstrap(login="owner", name="Owner", now=1800000000)
    monkeypatch.setattr(client_identity, "_service", identity)
    monkeypatch.setattr(registry, "_get_config_broker_service", lambda: broker)
    monkeypatch.setattr(registry, "_resolve_system_base_environment", lambda *_args, **_kwargs: ({}, {"status": "ready"}))
    return registry, broker_module.storage, broker, identity


def test_admin_save_uses_gateway_transaction_and_canonical_phone_manifest(phone_config):
    registry, storage, broker, identity = phone_config
    result = registry._save_system_base_domain({"data": {"remoteLink": {
        "activeProfileId": "cloudflare-tunnel",
        "phoneGateway": {"enabled": True, "port": 19532, "publicBaseUrl": "https://gateway.example.invalid"},
        "transportProfiles": [{"id": "cloudflare-tunnel", "kind": "cloudflare_tunnel",
                               "adminBaseUrl": "https://retired-admin.example.invalid",
                               "phoneBaseUrl": "https://phone.example.invalid/api/"}],
    }}})
    persisted = storage.get_system_base_config()["remoteLink"]
    assert persisted["phoneGateway"] == {"enabled": True, "port": 19532, "publicBaseUrl": "https://gateway.example.invalid"}
    normalized = normalize_remote_link_config(persisted)
    assert normalized["phoneGateway"] == persisted["phoneGateway"]
    phone_profile = next(p for p in normalized["transportProfiles"] if p["id"] == "cloudflare-tunnel")
    assert phone_profile["phoneBaseUrl"] == "https://phone.example.invalid"

    manifest = result["data"]["remoteLinkManifest"]
    assert manifest["kind"] == "v8_client_link_manifest" and manifest["clientGateway"] == "engine"
    urls = [endpoint["baseUrl"] for endpoint in manifest["endpoints"]]
    assert "https://phone.example.invalid" in urls
    assert "https://gateway.example.invalid" in urls
    assert all("retired-admin" not in url and ":9528" not in url for url in urls)
    ticket = identity.create_ticket(base_url=phone_profile["phoneBaseUrl"])
    paired = identity.consume_ticket(code=ticket["pairingCode"], instance_id=ticket["instanceId"])
    assert paired["adminBaseUrl"] == "https://phone.example.invalid"
    assert identity.verify_access(paired["accessToken"]).device_kind == "human_phone"
    with broker_module_database(broker) as db:
        row = db.execute("SELECT COUNT(*) FROM config_broker_transactions WHERE target_kind='client_gateway' AND state='committed'").fetchone()
    assert row[0] == 1


def broker_module_database(broker):
    # The broker owns persistence; inspect its canonical transaction table.
    import core.config_broker_service as module
    return module.db.get_connection()


@pytest.mark.parametrize("remote", [
    {"transportProfiles": [{"id": "lan", "phoneBaseUrl": "http://192.168.1.8:9532"}]},
    {"transportProfiles": [{"id": "cloudflare-tunnel", "phoneBaseUrl": "https://user:secret@example.invalid"}]},
    {"phoneGateway": {"port": 0}},
])
def test_invalid_phone_configuration_is_rejected_before_system_base_write(phone_config, remote):
    registry, storage, _broker, _identity = phone_config
    before = json.dumps(storage.get_system_base_config(), sort_keys=True)
    with pytest.raises(HTTPException):
        registry._save_system_base_domain({"data": {"bridge": {"adminBaseUrl": "http://changed.invalid"}, "remoteLink": remote}})
    assert json.dumps(storage.get_system_base_config(), sort_keys=True) == before
