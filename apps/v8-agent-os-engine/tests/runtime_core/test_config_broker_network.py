from __future__ import annotations

import json

import pytest

from core.database import DatabaseManager


@pytest.fixture
def broker(tmp_path, monkeypatch):
    import core.config_broker_service as module
    import core.storage as storage_module
    from core.tools.native import mcp
    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    monkeypatch.setattr(module, "db", DatabaseManager(tmp_path / "state.db"))
    service = module.ConfigBrokerService()
    monkeypatch.setattr(mcp, "config_broker_service", service)
    monkeypatch.setattr(mcp, "_runtime_identity", lambda: ("network-test", "", "test-run"))
    return module, service, mcp.config_broker


def invoke(tool, **args):
    return json.loads(tool.invoke(args))


def test_network_tool_commit_rollback_preserves_peer_identity_and_other_domains(broker):
    module, service, tool = broker
    module.storage.mutate_config_domain("networkSupervisorRuntime", lambda _: {"node": {"peerId": "trusted-local"}, "trust": {"enrollmentMode": "manual"}})
    module.storage.mutate_config_domain("supervisor", lambda _: {"nickname": "keep-me"})
    prepared = invoke(tool, mode="network_prepare", network_settings={"enabled": True, "openaiCompat": {"enabled": True}, "node": {"advertisedBaseUrl": "http://127.0.0.1:19531"}})
    assert prepared["ok"]
    committed = invoke(tool, mode="commit", transaction_id=prepared["transactionId"], plan_digest=prepared["planDigest"])
    assert committed["ok"] and committed["result"]["settings"]["openaiCompat"]["enabled"]
    assert module.storage.get_network_supervisor_runtime_config()["node"]["peerId"] == "trusted-local"
    assert "peerId" not in json.dumps(service.get_transaction(prepared["transactionId"], owner_id="network-test", include_private=True))
    restored = invoke(tool, mode="rollback", transaction_id=prepared["transactionId"])
    assert restored["ok"]
    assert not module.storage.get_network_supervisor_runtime_config()["openaiCompat"]["enabled"]
    assert module.storage.get_config_domain("supervisor")["nickname"] == "keep-me"


@pytest.mark.parametrize("patch", [
    {"trust": {"enrollmentMode": "open"}}, {"node": {"peerId": "forged"}},
    {"openaiCompat": {"token": "SECRET-CANARY"}}, {"node": {"advertisedBaseUrl": "http://u:SECRET-CANARY@localhost/"}},
    {"relay": {"adapters": [{"id": "test", "secret": "SECRET-CANARY"}]}},
    {"openaiCompat": {"maxConcurrentRequestsPerKey": -1}}, {"discovery": {"multicastPort": 99999}},
    {"discovery": {"wanBootstrapPeers": ["https://u:SECRET-CANARY@example.test"]}},
])
def test_network_settings_reject_identity_secrets_typos_and_invalid_limits(broker, patch):
    _, _, tool = broker
    result = invoke(tool, mode="network_prepare", network_settings=patch)
    assert result["ok"] is False
    assert "SECRET-CANARY" not in json.dumps(result)


def test_network_prepare_stale_and_rollback_conflict(broker):
    module, _, tool = broker
    prepared = invoke(tool, mode="network_prepare", network_settings={"openaiCompat": {"enabled": True}})
    module.storage.mutate_config_domain("networkSupervisorRuntime", lambda c: {**c, "enabled": True})
    result = invoke(tool, mode="commit", transaction_id=prepared["transactionId"], plan_digest=prepared["planDigest"])
    assert result["ok"] is False and result["error"]["code"] == "config_transaction_stale"
    prepared = invoke(tool, mode="network_prepare", network_settings={"openaiCompat": {"enabled": True}})
    assert invoke(tool, mode="commit", transaction_id=prepared["transactionId"], plan_digest=prepared["planDigest"])["ok"]
    module.storage.mutate_config_domain("networkSupervisorRuntime", lambda c: {**c, "enabled": False})
    assert invoke(tool, mode="rollback", transaction_id=prepared["transactionId"])["ok"] is False
    assert module.storage.get_network_supervisor_runtime_config()["enabled"] is False


def test_bootstrap_accepts_peer_ids_without_inventing_url_discovery(broker):
    _, _, tool = broker
    plan = invoke(tool, mode="network_prepare", network_settings={"discovery": {"wanBootstrapPeers": ["peer_fixture-node"]}})
    assert plan["ok"]
