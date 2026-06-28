from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def _read_repo_file(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_phone_pairing_uses_manifest_and_ordered_server_profiles() -> None:
    phone_api = _read_repo_file("apps/v8-agent-os-phone/src/lib/phone-api.ts")
    profiles = _read_repo_file("apps/v8-agent-os-phone/src/lib/admin-connection-profiles.ts")
    admin_ticket_route = _read_repo_file("apps/v8-agent-os-admin/src/app/api/client/pairing/tickets/route.ts")

    assert "v8_device_pairing_manifest" in admin_ticket_route
    assert "adminUrls" in admin_ticket_route
    assert "manifest: JSON.stringify(pairingManifest)" in admin_ticket_route

    assert "parseManifestText" in phone_api
    assert "pairing.adminUrls" in phone_api
    assert "for (const adminBaseUrl of pairing.adminUrls)" in phone_api

    assert "orderAdminBaseUrlCandidates" in profiles
    assert "isTailscaleHost" in profiles
    assert "return [...tailscale, ...lan, ...manual]" in profiles


def test_phone_connection_failure_keeps_cached_identity_readable() -> None:
    source = _read_repo_file("apps/v8-agent-os-phone/src/providers/app-session.tsx")

    assert 'setStatus(parsedStoredUser ? "authenticated" : "anonymous")' in source
    assert "if (parsedStoredUser) {" in source
    assert 'setStatus("authenticated");' in source

    refresh_failure_block_start = source.index("const refreshed = await refreshSession();")
    refresh_failure_block = source[refresh_failure_block_start: refresh_failure_block_start + 250]
    assert "signOut()" not in refresh_failure_block


def test_web_history_load_uses_omit_messages_and_local_delta_cache() -> None:
    chat_client = _read_repo_file("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx")
    cache = _read_repo_file("apps/v8-agent-os-web/src/lib/web-conversation-cache.ts")

    assert "?omitMessages=1" in chat_client
    assert "readWebConversationCache(conversationId)" in chat_client
    assert "/sync?since=" in chat_client
    assert "writeWebConversationCache(activeConversationId" in chat_client

    assert "MAX_CACHED_MESSAGES" in cache
    assert "deletedIds.has(message.id)" in cache
    assert "mergeWebConversationSync" in cache
