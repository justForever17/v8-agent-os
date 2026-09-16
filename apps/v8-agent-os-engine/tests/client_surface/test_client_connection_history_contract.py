from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]


def _read_repo_file(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_phone_pairing_uses_manifest_and_ordered_server_profiles() -> None:
    phone_api = _read_repo_file("apps/v8-agent-os-phone/src/lib/phone-api.ts")
    profiles = _read_repo_file("apps/v8-agent-os-phone/src/lib/admin-connection-profiles.ts")
    admin_ticket_route = _read_repo_file("apps/v8-agent-os-admin/src/app/api/client/pairing/tickets/route.ts")

    assert "v8_device_pairing_manifest" in admin_ticket_route
    assert "adminUrls" in admin_ticket_route
    assert "endpoints" in admin_ticket_route
    assert "cloudflareUrls" in admin_ticket_route
    assert "manifest: JSON.stringify(pairingManifest)" in admin_ticket_route
    assert "...(linkManifest.profiles || [])" not in admin_ticket_route

    assert "parseManifestText" in phone_api
    assert "pairing.adminUrls" in phone_api
    assert "for (const adminBaseUrl of pairing.adminUrls)" in phone_api

    assert "orderAdminBaseUrlCandidates" in profiles
    assert "isTailscaleHost" in profiles
    assert "lan_ipv6" in profiles
    assert "cloudflare_tunnel" in profiles
    assert "endpointRank" in profiles


def test_cloudflare_phone_remote_link_is_verified_and_separate_from_network_supervisor() -> None:
    verify_route = _read_repo_file("apps/v8-agent-os-admin/src/app/api/client/link/verify-cloudflare/route.ts")
    pairing_route = _read_repo_file("apps/v8-agent-os-admin/src/app/api/client/pairing/tickets/route.ts")
    phone_api = _read_repo_file("apps/v8-agent-os-phone/src/lib/phone-api.ts")
    phone_profiles = _read_repo_file("apps/v8-agent-os-phone/src/lib/admin-connection-profiles.ts")
    phone_session = _read_repo_file("apps/v8-agent-os-phone/src/providers/app-session.tsx")

    assert 'resolveClientUser(req)' in verify_route
    assert 'owner.role !== "ADMIN"' in verify_route
    assert 'parsed.protocol !== "https:"' in verify_route
    assert 'hostname.endsWith(".trycloudflare.com")' in verify_route
    assert '`${origin}/api/client/instance`' in verify_route
    assert '"phone_remote_link_verification"' in verify_route
    for source in (verify_route, pairing_route, phone_api, phone_profiles, phone_session):
        assert "network-supervisor" not in source
        assert "network_supervisor" not in source
        assert "peerToken" not in source
        assert "wakeSupervisor" not in source
    # Stable endpoint enforcement is owned and exercised by Engine's identity
    # manifest tests; an obsolete Admin helper is not an authorization oracle.


def test_phone_connection_failure_keeps_cached_identity_readable() -> None:
    _run_phone_behavior("offline cached identity")


def test_phone_connection_candidates_are_typed_and_return_to_verified_lan() -> None:
    _run_phone_behavior("endpoint identity|LAN recovery|body backpressure")


def _run_phone_behavior(pattern: str, filename: str = "phone-transport-boundary.test.cjs") -> None:
    """Execute production TS modules; variable names/timer syntax are not oracles.

    The Phone CI matrix installs these dependencies and runs every *.test.cjs.
    A Python-only shard skips this cross-runtime check explicitly, never reports
    that the Phone behavior passed without running it.
    """
    phone = REPO_ROOT / "apps/v8-agent-os-phone"
    node = shutil.which("node")
    if not node or not (phone / "node_modules/typescript/lib/typescript.js").is_file():
        pytest.skip("Phone runtime dependencies absent; behavior gate must run in the Phone CI matrix")
    result = subprocess.run(
        [node, "--test", "--test-reporter=tap", f"--test-name-pattern={pattern}", str(phone / "tests" / filename)],
        cwd=phone, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    executed = re.search(r"^# pass (\d+)\s*$", result.stdout, re.MULTILINE)
    # Node may count the file itself as one pass when its selected test plan is empty.
    names = re.findall(r"^# Subtest: (.+)$", result.stdout, re.MULTILINE)
    assert executed and int(executed.group(1)) > 0 and any(re.search(pattern, name) for name in names), (
        f"No Phone behavior matched {pattern!r}: {result.stdout}"
    )


def test_local_trusted_client_boundary_is_documented() -> None:
    doc = _read_repo_file("docs/V8_AGENT_OS_API_REFERENCE_ZH.md")

    assert "Shell 通过本机可信会话和受控通道编排这些入口" in doc
    assert "不得直连 Engine 数据库" in doc
    assert "Phone 通过 Engine 的受鉴权客户端网关访问产品能力；Admin 不参与会话传输" in doc
    assert "CyberCore companion" not in doc


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
