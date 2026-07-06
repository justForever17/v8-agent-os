from __future__ import annotations

import re
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


def test_local_trusted_client_boundary_is_documented() -> None:
    doc = _read_repo_file("docs/V8OS/V8OS_BINARY_CLI_WORKSPACE_AND_CLIENT_CONNECT_ZH.md")

    assert "Web shell / CyberCore companion 可以直连 Engine WebSocket" in doc
    assert "不能直连 DB" in doc or "不得直连数据库" in doc
    assert "Phone 不应该直连 Engine" in doc
    assert "Web shell / CyberCore companion 都不应该直连 Engine" not in doc


def test_web_history_load_uses_server_turn_paging_not_local_message_cache() -> None:
    chat_client = _read_repo_file("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx")
    cache = _read_repo_file("apps/v8-agent-os-web/src/lib/web-conversation-cache.ts")
    detail_route = _read_repo_file("apps/v8-agent-os-admin/src/app/api/client/conversations/[id]/route.ts")
    turns_route = _read_repo_file("apps/v8-agent-os-admin/src/app/api/client/conversations/[id]/turns/route.ts")
    conversations_route = _read_repo_file("apps/v8-agent-os-admin/src/app/api/conversations/route.ts")
    engine_routes = _read_repo_file("apps/v8-agent-os-engine/api/session_workflow_routes.py")

    assert "?omitMessages=1" in chat_client
    assert "/turns?" in chat_client
    assert "loadConversationTurnPage" in chat_client
    assert "loadOlderConversationTurn" in chat_client
    assert "hasOlderTurns" in chat_client
    assert "readWebConversationCache" not in chat_client
    assert "writeWebConversationCache" not in chat_client
    assert "mergeWebConversationSync" not in chat_client
    assert "/sync?since=" not in chat_client
    assert "const authoritativeMessages" not in chat_client

    assert "clearLegacyWebConversationCache" in cache
    assert "indexedDB.deleteDatabase" in cache
    assert "v8-agent-os.webConversation." in cache
    assert "indexedDB.open" not in cache
    assert "conversationCache" not in cache

    assert "stripMessagesForProjection" in detail_route
    assert "projection: projectionData" in detail_route
    assert re.search(r"const detailMessages = omitMessages\s*\?\s*\[\]", detail_route)
    assert "/turns?" in turns_route
    assert "normalizeMessageForRealtimeSurface" in turns_route
    assert "/sessions/quick-index" in conversations_route
    assert "web_session_index.json" in engine_routes
    assert '@router.get("/sessions/{session_id}/turns")' in engine_routes


def test_shared_message_bound_execution_contract_exists_for_phone_web_renderers() -> None:
    index = _read_repo_file("packages/session-realtime/src/index.ts")
    contract = _read_repo_file("packages/session-realtime/src/message-bound-execution-node.ts")
    web_message = _read_repo_file("apps/v8-agent-os-web/src/components/chat/ChatMessage.tsx")
    phone_message = _read_repo_file("apps/v8-agent-os-phone/src/components/chat/MessageBubble.tsx")

    assert "message-bound-execution-node" in index
    assert "MessageBoundExecutionNode" in contract
    assert "buildMessageBoundExecutionNodes" in contract
    assert "buildCollaborationMicroStagesFromMessageBoundNodes" in contract
    assert "messageId" in contract
    assert "detailRef" in contract
    assert "buildMessageBoundExecutionNodes" in web_message
    assert "buildCollaborationMicroStagesFromMessageBoundNodes" in web_message
    assert "buildMessageBoundExecutionNodes" in phone_message
    assert "buildCollaborationMicroStagesFromMessageBoundNodes" in phone_message
