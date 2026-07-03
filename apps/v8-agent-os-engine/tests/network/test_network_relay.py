from __future__ import annotations

from unittest.mock import patch

from runtimes.network_supervisor.models import NetworkSupervisorRuntimeConfig
from runtimes.network_supervisor.service import network_supervisor_service


def _identity() -> dict[str, str]:
    return {
        "peerId": "peer_local",
        "displayName": "Main Device",
        "publicKeyFingerprint": "local-fp",
        "localPeerTokenFingerprint": "token-fp",
    }


def test_relay_status_exposes_self_hostable_protocol_with_cloudflare_adapter():
    config = NetworkSupervisorRuntimeConfig.model_validate(
        {
            "enabled": True,
            "relay": {
                "enabled": True,
                "activeAdapterId": "cloudflare",
                "adapters": [
                    {
                        "id": "cloudflare",
                        "kind": "cloudflare",
                        "displayName": "Cloudflare Relay",
                        "baseUrl": "https://relay.example.com",
                        "cloudflareWorkerName": "v8-relay",
                        "cloudflareQueueName": "v8-relay-mailbox",
                        "cloudflareDurableObjectNamespace": "V8RelayRoom",
                    }
                ],
            },
        }
    )

    with patch.object(network_supervisor_service, "get_config_model", return_value=config), patch.object(
        network_supervisor_service,
        "_local_identity",
        return_value=_identity(),
    ):
        status = network_supervisor_service.relay_status_payload()

    assert status["available"] is True
    assert status["protocol"]["version"] == "v8-relay.v1"
    assert status["protocol"]["selfHostable"] is True
    assert status["protocol"]["cloudflareAdapter"] == "optional"
    assert status["activeAdapter"]["kind"] == "cloudflare"
    assert status["activeAdapter"]["endpoints"]["mailbox"] == "https://relay.example.com/v1/relay/mailbox"
    assert status["activeAdapter"]["endpoints"]["websocket"] == "wss://relay.example.com/v1/relay/ws"


def test_relay_config_update_does_not_change_compat_api_branch():
    config = NetworkSupervisorRuntimeConfig()
    config.enabled = True
    config.openai_compat.enabled = False

    with patch.object(network_supervisor_service, "get_config_model", return_value=config), patch.object(
        network_supervisor_service,
        "save_config_model",
        return_value=config,
    ), patch.object(network_supervisor_service, "_local_identity", return_value=_identity()):
        result = network_supervisor_service.save_relay_config(
            {
                "enabled": True,
                "activeAdapterId": "self-hosted",
                "adapters": [
                    {
                        "id": "self-hosted",
                        "kind": "self_hosted",
                        "displayName": "Local Relay",
                        "baseUrl": "https://relay.local.test",
                    }
                ],
            }
        )

    assert result["ok"] is True
    assert result["status"]["available"] is True
    assert config.relay.enabled is True
    assert config.openai_compat.enabled is False


def test_relay_config_patch_preserves_existing_adapter_details():
    config = NetworkSupervisorRuntimeConfig.model_validate(
        {
            "enabled": True,
            "relay": {
                "enabled": False,
                "activeAdapterId": "cloudflare",
                "adapters": [
                    {
                        "id": "cloudflare",
                        "kind": "cloudflare",
                        "displayName": "Cloudflare Relay",
                        "baseUrl": "https://old.example.com",
                        "cloudflareWorkerName": "v8-relay",
                    }
                ],
            },
        }
    )

    with patch.object(network_supervisor_service, "get_config_model", return_value=config), patch.object(
        network_supervisor_service,
        "save_config_model",
        return_value=config,
    ), patch.object(network_supervisor_service, "_local_identity", return_value=_identity()):
        result = network_supervisor_service.save_relay_config({"enabled": True})

    assert result["relay"]["enabled"] is True
    assert result["relay"]["activeAdapterId"] == "cloudflare"
    assert result["relay"]["adapters"][0]["baseUrl"] == "https://old.example.com"
    assert result["relay"]["adapters"][0]["cloudflareWorkerName"] == "v8-relay"
