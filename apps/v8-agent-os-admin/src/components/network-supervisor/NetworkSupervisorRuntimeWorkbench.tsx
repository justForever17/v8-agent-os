"use client";
import * as React from "react";
import Link from "next/link";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DocumentationGuideDialog } from "@/components/admin-shell/DocumentationGuideDialog";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { getAdminOptions, resolveAdminLabel } from "@/lib/admin-labels";
import type { CanonicalConfigDiagnostics, LegacyPortNotice } from "@/lib/server/bridge-config";
type PeerItem = {
    peerId: string;
    displayName: string;
    baseUrl: string;
    wsUrl: string;
    publicKey: string;
    publicKeyFingerprint: string;
    trusted: boolean;
    discovered: boolean;
    online: boolean;
    lastSeenAt?: string | null;
    allowedScopes: string[];
    allowedWorkspaces: string[];
    tokenFingerprint: string;
    source: string;
    address?: string;
    transportProfileId?: string;
    peerBaseUrl?: string;
};
type MeshCandidate = {
    id?: string;
    source?: string;
    transportProfileId?: string;
    hostName?: string;
    dnsName?: string;
    ips?: string[];
    os?: string;
    online?: boolean;
    lastSeen?: string;
    peerBaseUrl?: string;
    deviceClass?: string;
    requiresApproval?: boolean;
    approvalReason?: string;
};
type PeersPayload = {
    items: PeerItem[];
    trustedItems: PeerItem[];
    discoveredItems: PeerItem[];
    meshCandidates: MeshCandidate[];
};
type RelayAdapterConfig = {
    id: string;
    kind: "self_hosted" | "cloudflare";
    displayName: string;
    enabled: boolean;
    baseUrl: string;
    websocketUrl?: string | null;
    rendezvousPath?: string;
    mailboxPath?: string;
    websocketPath?: string;
    cloudflareAccountHint?: string;
    cloudflareWorkerName?: string;
    cloudflareQueueName?: string;
    cloudflareDurableObjectNamespace?: string;
};
type RelayStatusAdapter = RelayAdapterConfig & {
    configured?: boolean;
    status?: string;
    endpoints?: {
        wellKnown?: string;
        rendezvous?: string;
        mailbox?: string;
        websocket?: string;
    };
    warnings?: string[];
    cloudflare?: {
        accountHint?: string;
        workerName?: string;
        queueName?: string;
        durableObjectNamespace?: string;
    } | null;
};
type RelayConfig = {
    enabled: boolean;
    activeAdapterId: string;
    protocolVersion: string;
    endToEndEnvelopeRequired: boolean;
    storeAndForwardRequired: boolean;
    defaultTtlSeconds: number;
    maxPayloadBytes: number;
    adapters: RelayAdapterConfig[];
};
type RelayStatus = {
    enabled: boolean;
    available: boolean;
    reasons: string[];
    activeAdapterId: string;
    activeAdapter?: RelayStatusAdapter;
    adapters: RelayStatusAdapter[];
    protocol?: {
        version?: string;
        selfHostable?: boolean;
        cloudflareAdapter?: string;
        endToEndEnvelopeRequired?: boolean;
        storeAndForwardRequired?: boolean;
        defaultTtlSeconds?: number;
        maxPayloadBytes?: number;
    };
};
type RuntimeConfig = {
    enabled: boolean;
    node: {
        displayName: string;
        peerId: string;
        advertisedBaseUrl: string;
        advertisedWsUrl: string;
    };
    discovery: {
        lanEnabled: boolean;
        multicastGroup: string;
        multicastPort: number;
        announceIntervalSeconds: number;
        peerExpirySeconds: number;
        wanBootstrapPeers: string[];
    };
    trust: {
        enrollmentMode: "manual" | "open";
        allowedScopes: string[];
        trustedPeers: unknown[];
    };
    wake: {
        enabled: boolean;
        ackTimeoutSeconds: number;
    };
    delegation: {
        enabled: boolean;
        maxConcurrent: number;
        defaultTimeoutSeconds: number;
    };
    relay: RelayConfig;
    openaiCompat: {
        enabled: boolean;
        modelAliases: string[];
        adminRelayOnly: boolean;
        allowWorkspaceHeaders: boolean;
        allowRawWorkspacePath: boolean;
        v8MainChainModeEnabled: boolean;
        maxExternalTools: number;
        defaultScopeMode: string;
    };
};
type Availability = {
    available: boolean;
    reasons: string[];
};
type RuntimeStatus = {
    enabled: boolean;
    started: boolean;
    node: {
        peerId: string;
        displayName: string;
        advertisedBaseUrl: string;
        advertisedWsUrl: string;
        publicKeyFingerprint: string;
        localPeerTokenFingerprint: string;
    };
    discovery: {
        lanEnabled: boolean;
        wanBootstrapPeers: string[];
        lastAnnounceAt?: string | null;
        onlinePeerCount: number;
        discoveredPeerCount: number;
    };
    delegation: {
        enabled: boolean;
        maxConcurrent: number;
        activeInbound: number;
        trackedCount: number;
    };
    relay?: RelayStatus;
    openaiCompat?: {
        enabled: boolean;
        adminRelayOnly: boolean;
        available: boolean;
        tokenCount: number;
        modelAliases: string[];
        baseUrlHint: string;
        chatCompletionsPath: string;
        modelsPath: string;
        maxExternalTools: number;
        allowWorkspaceHeaders: boolean;
        allowRawWorkspacePath: boolean;
        v8MainChainModeEnabled: boolean;
        defaultScopeMode: string;
    };
    anthropicCompat?: {
        enabled: boolean;
        available: boolean;
        tokenCount: number;
        modelAliases: string[];
        baseUrlHint: string;
        messagesPath: string;
        modelsPath: string;
    };
    compatIngress?: {
        maxExternalPayloadTokens: number;
        recent: Array<Record<string, unknown>>;
    };
    pendingExternalTools?: {
        waitingCount: number;
        recent: Array<Record<string, unknown>>;
    };
    delegationAvailability: Availability;
    toolAvailability?: {
        delegate_network_task?: Availability;
    };
};
type PeerForm = {
    peerId: string;
    displayName: string;
    baseUrl: string;
    wsUrl: string;
    publicKey: string;
    allowedScopes: string;
    allowedWorkspaces: string;
    peerToken: string;
    transportProfileId: string;
    peerBaseUrl: string;
    deviceClass: string;
    requiresApproval: boolean;
};
type DiagState = {
    peerId: string;
    note: string;
    task: string;
    result: string;
};
type NeighborLink = {
    linkId: string;
    peerId: string;
    displayName?: string;
    localNickname?: string;
    remoteNickname?: string;
    localRole?: "primary" | "companion";
    remoteRole?: "primary" | "companion";
    online?: boolean;
    lastSeenAt?: string | null;
    workspaceBinding?: Record<string, unknown>;
    capabilityTags?: string[];
    description?: string;
};
type NeighborStatus = {
    enabled: boolean;
    started: boolean;
    node?: { peerId?: string; displayName?: string };
    discovery?: { lanEnabled?: boolean; candidateCount?: number; connectedCount?: number; lastAnnounceAt?: string | null };
    links?: NeighborLink[];
};
type NeighborCandidate = {
    peerId: string;
    displayName?: string;
    online?: boolean;
    lastSeenAt?: string | null;
    source?: string;
    baseUrl?: string;
    address?: string;
};
type NeighborMessage = {
    messageId: string;
    seq: number;
    direction: "inbound" | "outbound";
    fromNickname?: string;
    role?: string;
    preview?: string;
    body?: string;
    status?: string;
    receivedAt?: string;
};
type NeighborTaskSettings = {
    resultWakePolicy: "inbox" | "per_result";
};
type NeighborTaskAssignment = {
    assignmentId: string;
    peerId?: string;
    status?: string;
    delivery?: { status?: string };
};
type NeighborTaskResult = {
    resultId: string;
    status?: string;
    summary?: string;
    body?: string;
};
type NeighborTaskItem = {
    taskId: string;
    title?: string;
    status?: string;
    wakePolicy?: string;
    requiredCapabilities?: string[];
    assignments?: NeighborTaskAssignment[];
    results?: NeighborTaskResult[];
};
type OpenAICompatToken = {
    id: string;
    label: string;
    token: string;
    fingerprint: string;
    createdAt?: string | null;
    source?: string;
};
type NetworkSupervisorRuntimeWorkbenchProps = {
    bridgeDiagnostics?: CanonicalConfigDiagnostics;
};
const DEFAULT_RELAY_ADAPTERS: RelayAdapterConfig[] = [
    {
        id: "self-hosted",
        kind: "self_hosted",
        displayName: "Self-hosted V8 Relay",
        enabled: true,
        baseUrl: "",
        websocketUrl: "",
        rendezvousPath: "/v1/relay/rendezvous",
        mailboxPath: "/v1/relay/mailbox",
        websocketPath: "/v1/relay/ws",
    },
    {
        id: "cloudflare",
        kind: "cloudflare",
        displayName: "Cloudflare Workers Relay",
        enabled: true,
        baseUrl: "",
        websocketUrl: "",
        rendezvousPath: "/v1/relay/rendezvous",
        mailboxPath: "/v1/relay/mailbox",
        websocketPath: "/v1/relay/ws",
        cloudflareAccountHint: "",
        cloudflareWorkerName: "",
        cloudflareQueueName: "",
        cloudflareDurableObjectNamespace: "",
    },
];
const DEFAULT_CONFIG: RuntimeConfig = {
    enabled: false,
    node: {
        displayName: "V8 Node",
        peerId: "",
        advertisedBaseUrl: "http://127.0.0.1:9530",
        advertisedWsUrl: "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws",
    },
    discovery: {
        lanEnabled: true,
        multicastGroup: "239.255.42.17",
        multicastPort: 9531,
        announceIntervalSeconds: 5,
        peerExpirySeconds: 30,
        wanBootstrapPeers: [],
    },
    trust: { enrollmentMode: "manual", allowedScopes: [], trustedPeers: [] },
    wake: { enabled: true, ackTimeoutSeconds: 10 },
    delegation: { enabled: true, maxConcurrent: 2, defaultTimeoutSeconds: 120 },
    relay: {
        enabled: false,
        activeAdapterId: "self-hosted",
        protocolVersion: "v8-relay.v1",
        endToEndEnvelopeRequired: true,
        storeAndForwardRequired: true,
        defaultTtlSeconds: 300,
        maxPayloadBytes: 262144,
        adapters: DEFAULT_RELAY_ADAPTERS,
    },
    openaiCompat: {
        enabled: false,
        modelAliases: ["v8os"],
        adminRelayOnly: true,
        allowWorkspaceHeaders: false,
        allowRawWorkspacePath: false,
        v8MainChainModeEnabled: false,
        maxExternalTools: 256,
        defaultScopeMode: "explicit",
    },
};
const EMPTY_STATUS: RuntimeStatus = {
    enabled: false,
    started: false,
    node: {
        peerId: "",
        displayName: "V8 Node",
        advertisedBaseUrl: "http://127.0.0.1:9530",
        advertisedWsUrl: "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws",
        publicKeyFingerprint: "",
        localPeerTokenFingerprint: "",
    },
    discovery: { lanEnabled: false, wanBootstrapPeers: [], lastAnnounceAt: null, onlinePeerCount: 0, discoveredPeerCount: 0 },
    delegation: { enabled: false, maxConcurrent: 0, activeInbound: 0, trackedCount: 0 },
    relay: {
        enabled: false,
        available: false,
        reasons: ["relay_disabled"],
        activeAdapterId: "self-hosted",
        activeAdapter: undefined,
        adapters: [],
        protocol: {
            version: "v8-relay.v1",
            selfHostable: true,
            cloudflareAdapter: "optional",
            endToEndEnvelopeRequired: true,
            storeAndForwardRequired: true,
            defaultTtlSeconds: 300,
            maxPayloadBytes: 262144,
        },
    },
    openaiCompat: {
        enabled: false,
        adminRelayOnly: true,
        available: false,
        tokenCount: 0,
        modelAliases: ["v8os"],
        baseUrlHint: "http://localhost:9528/api/network-supervisor/openai/v1",
        chatCompletionsPath: "/chat/completions",
        modelsPath: "/models",
        maxExternalTools: 256,
        allowWorkspaceHeaders: false,
        allowRawWorkspacePath: false,
        v8MainChainModeEnabled: false,
        defaultScopeMode: "explicit",
    },
    anthropicCompat: {
        enabled: false,
        available: false,
        tokenCount: 0,
        modelAliases: ["v8os"],
        baseUrlHint: "http://localhost:9528/api/network-supervisor/anthropic",
        messagesPath: "/v1/messages",
        modelsPath: "/v1/models",
    },
    compatIngress: {
        maxExternalPayloadTokens: 0,
        recent: [],
    },
    pendingExternalTools: {
        waitingCount: 0,
        recent: [],
    },
    delegationAvailability: { available: false, reasons: [] },
    toolAvailability: {},
};
const EMPTY_PEERS: PeersPayload = { items: [], trustedItems: [], discoveredItems: [], meshCandidates: [] };
const EMPTY_PEER_FORM: PeerForm = { peerId: "", displayName: "", baseUrl: "", wsUrl: "", publicKey: "", allowedScopes: "", allowedWorkspaces: "", peerToken: "", transportProfileId: "", peerBaseUrl: "", deviceClass: "", requiresApproval: false };
const EMPTY_DIAG: DiagState = { peerId: "", note: "", task: "", result: "" };
const EMPTY_NEIGHBOR_STATUS: NeighborStatus = { enabled: false, started: false, links: [], discovery: { candidateCount: 0, connectedCount: 0 } };
const EMPTY_NEIGHBOR_TASK_SETTINGS: NeighborTaskSettings = { resultWakePolicy: "inbox" };
const csv = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
const lines = (value: string) => value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
const joinCsv = (value?: string[]) => Array.isArray(value) ? value.join(", ") : "";
const joinLines = (value?: string[]) => Array.isArray(value) ? value.join("\n") : "";
function normalizeRelayAdapters(value?: RelayAdapterConfig[]): RelayAdapterConfig[] {
    const byId = new Map(DEFAULT_RELAY_ADAPTERS.map((item) => [item.id, { ...item }]));
    for (const item of Array.isArray(value) ? value : []) {
        const id = String(item?.id || "").trim();
        if (!id)
            continue;
        const fallback = byId.get(id) || {};
        byId.set(id, { ...fallback, ...item, id } as RelayAdapterConfig);
    }
    return Array.from(byId.values());
}
function mergeRelayConfig(value?: Partial<RelayConfig>): RelayConfig {
    const payload = value || {};
    return {
        ...DEFAULT_CONFIG.relay,
        ...payload,
        adapters: normalizeRelayAdapters(payload.adapters as RelayAdapterConfig[] | undefined),
    };
}
const detail = (value: unknown, fallback: string) => {
    if (!value || typeof value !== "object")
        return fallback;
    const payload = value as Record<string, unknown>;
    return String(payload.detail || payload.error || fallback);
};
function mergeConfig(value?: Partial<RuntimeConfig>): RuntimeConfig {
    const payload = value || {};
    return {
        ...DEFAULT_CONFIG,
        ...payload,
        node: { ...DEFAULT_CONFIG.node, ...(payload.node || {}) },
        discovery: { ...DEFAULT_CONFIG.discovery, ...(payload.discovery || {}) },
        trust: { ...DEFAULT_CONFIG.trust, ...(payload.trust || {}) },
        wake: { ...DEFAULT_CONFIG.wake, ...(payload.wake || {}) },
        delegation: { ...DEFAULT_CONFIG.delegation, ...(payload.delegation || {}) },
        relay: mergeRelayConfig(payload.relay),
        openaiCompat: { ...DEFAULT_CONFIG.openaiCompat, ...(payload.openaiCompat || {}) },
    };
}
function normalizeStatus(value: unknown): RuntimeStatus {
    const payload = (value && typeof value === "object") ? value as Partial<RuntimeStatus> : {};
    const openaiCompat = (payload.openaiCompat || {}) as NonNullable<RuntimeStatus["openaiCompat"]>;
    const anthropicCompat = (payload.anthropicCompat || {}) as NonNullable<RuntimeStatus["anthropicCompat"]>;
    const compatIngress = (payload.compatIngress || {}) as NonNullable<RuntimeStatus["compatIngress"]>;
    const pendingExternalTools = (payload.pendingExternalTools || {}) as NonNullable<RuntimeStatus["pendingExternalTools"]>;
    const relay = (payload.relay || {}) as NonNullable<RuntimeStatus["relay"]>;
    return {
        ...EMPTY_STATUS,
        ...payload,
        node: { ...EMPTY_STATUS.node, ...(payload.node || {}) },
        discovery: { ...EMPTY_STATUS.discovery, ...(payload.discovery || {}) },
        delegation: { ...EMPTY_STATUS.delegation, ...(payload.delegation || {}) },
        relay: {
            ...EMPTY_STATUS.relay,
            ...relay,
            activeAdapterId: String(relay.activeAdapterId || EMPTY_STATUS.relay?.activeAdapterId || "self-hosted"),
            activeAdapter: relay.activeAdapter,
            adapters: Array.isArray(relay.adapters) ? relay.adapters : [],
            reasons: Array.isArray(relay.reasons) ? relay.reasons : [],
            protocol: { ...EMPTY_STATUS.relay?.protocol, ...(relay.protocol || {}) },
        },
        openaiCompat: {
            enabled: Boolean(openaiCompat.enabled),
            adminRelayOnly: openaiCompat.adminRelayOnly !== false,
            available: Boolean(openaiCompat.available),
            tokenCount: Number(openaiCompat.tokenCount || 0),
            modelAliases: Array.isArray(openaiCompat.modelAliases) && openaiCompat.modelAliases.length ? openaiCompat.modelAliases.map((item) => String(item)).filter(Boolean) : ["v8os"],
            baseUrlHint: String(openaiCompat.baseUrlHint || EMPTY_STATUS.openaiCompat?.baseUrlHint || ""),
            chatCompletionsPath: String(openaiCompat.chatCompletionsPath || EMPTY_STATUS.openaiCompat?.chatCompletionsPath || ""),
            modelsPath: String(openaiCompat.modelsPath || EMPTY_STATUS.openaiCompat?.modelsPath || "/models"),
            maxExternalTools: Number(openaiCompat.maxExternalTools || EMPTY_STATUS.openaiCompat?.maxExternalTools || 0),
            allowWorkspaceHeaders: Boolean(openaiCompat.allowWorkspaceHeaders),
            allowRawWorkspacePath: Boolean(openaiCompat.allowRawWorkspacePath),
            v8MainChainModeEnabled: Boolean(openaiCompat.v8MainChainModeEnabled),
            defaultScopeMode: String(openaiCompat.defaultScopeMode || EMPTY_STATUS.openaiCompat?.defaultScopeMode || "explicit"),
        },
        anthropicCompat: {
            enabled: Boolean(anthropicCompat.enabled),
            available: Boolean(anthropicCompat.available),
            tokenCount: Number(anthropicCompat.tokenCount || 0),
            modelAliases: Array.isArray(anthropicCompat.modelAliases) && anthropicCompat.modelAliases.length ? anthropicCompat.modelAliases.map((item) => String(item)).filter(Boolean) : ["v8os"],
            baseUrlHint: String(anthropicCompat.baseUrlHint || EMPTY_STATUS.anthropicCompat?.baseUrlHint || ""),
            messagesPath: String(anthropicCompat.messagesPath || EMPTY_STATUS.anthropicCompat?.messagesPath || "/v1/messages"),
            modelsPath: String(anthropicCompat.modelsPath || EMPTY_STATUS.anthropicCompat?.modelsPath || "/v1/models"),
        },
        compatIngress: {
            maxExternalPayloadTokens: Number(compatIngress.maxExternalPayloadTokens || 0),
            recent: Array.isArray(compatIngress.recent) ? compatIngress.recent.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object")) : [],
        },
        pendingExternalTools: {
            waitingCount: Number(pendingExternalTools.waitingCount || 0),
            recent: Array.isArray(pendingExternalTools.recent) ? pendingExternalTools.recent.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object")) : [],
        },
        delegationAvailability: {
            ...EMPTY_STATUS.delegationAvailability,
            ...(payload.delegationAvailability || {}),
            reasons: Array.isArray(payload.delegationAvailability?.reasons) ? payload.delegationAvailability.reasons : [],
        },
        toolAvailability: payload.toolAvailability || {},
    };
}
function normalizePeers(value: unknown): PeersPayload {
    const payload = (value && typeof value === "object") ? value as Partial<PeersPayload> : {};
    return {
        items: Array.isArray(payload.items) ? payload.items : [],
        trustedItems: Array.isArray(payload.trustedItems) ? payload.trustedItems : [],
        discoveredItems: Array.isArray(payload.discoveredItems) ? payload.discoveredItems : [],
        meshCandidates: Array.isArray((payload as { meshCandidates?: unknown }).meshCandidates) ? (payload as { meshCandidates: MeshCandidate[] }).meshCandidates : [],
    };
}
export function NetworkSupervisorRuntimeWorkbench({ bridgeDiagnostics }: NetworkSupervisorRuntimeWorkbenchProps) {
    const t = useT();
    const { locale } = useLocale();
    const { toast } = useToast();
    const [config, setConfig] = React.useState<RuntimeConfig>(DEFAULT_CONFIG);
    const [status, setStatus] = React.useState<RuntimeStatus>(EMPTY_STATUS);
    const [peers, setPeers] = React.useState<PeersPayload>(EMPTY_PEERS);
    const [neighborStatus, setNeighborStatus] = React.useState<NeighborStatus>(EMPTY_NEIGHBOR_STATUS);
    const [neighborCandidates, setNeighborCandidates] = React.useState<NeighborCandidate[]>([]);
    const [neighborLinks, setNeighborLinks] = React.useState<NeighborLink[]>([]);
    const [selectedNeighborLinkId, setSelectedNeighborLinkId] = React.useState("");
    const [pairingInvite, setPairingInvite] = React.useState<{ code: string; expiresAt: string; inviteId: string } | null>(null);
    const [pairingPeerId, setPairingPeerId] = React.useState("");
    const [pairingCode, setPairingCode] = React.useState("");
    const [neighborTimeline, setNeighborTimeline] = React.useState<NeighborMessage[]>([]);
    const [neighborMessage, setNeighborMessage] = React.useState("");
    const [neighborBusy, setNeighborBusy] = React.useState("");
    const [neighborTaskSettings, setNeighborTaskSettings] = React.useState<NeighborTaskSettings>(EMPTY_NEIGHBOR_TASK_SETTINGS);
    const [neighborTasks, setNeighborTasks] = React.useState<NeighborTaskItem[]>([]);
    const [neighborTaskBody, setNeighborTaskBody] = React.useState("");
    const [neighborTaskCapabilities, setNeighborTaskCapabilities] = React.useState("");
    const [linkDraft, setLinkDraft] = React.useState<{ localNickname: string; remoteNickname: string; localRole: "primary" | "companion"; capabilityTags: string; description: string }>({ localNickname: "", remoteNickname: "", localRole: "primary", capabilityTags: "", description: "" });
    const [peerForm, setPeerForm] = React.useState<PeerForm>(EMPTY_PEER_FORM);
    const [diag, setDiag] = React.useState<DiagState>(EMPTY_DIAG);
    const [loading, setLoading] = React.useState(true);
    const [loadError, setLoadError] = React.useState<string | null>(null);
    const [guideOpen, setGuideOpen] = React.useState(false);
    const [docContent, setDocContent] = React.useState("");
    const [savingConfig, setSavingConfig] = React.useState(false);
    const [savingPeer, setSavingPeer] = React.useState(false);
    const [tokens, setTokens] = React.useState<OpenAICompatToken[]>([]);
    const [tokenLabel, setTokenLabel] = React.useState("");
    const [tokenBusy, setTokenBusy] = React.useState(false);
    const [adminOrigin, setAdminOrigin] = React.useState("http://localhost:9528");
    const [running, setRunning] = React.useState<"" | "challenge" | "wake" | "delegate">("");
    const docsUrl = locale === "zh-CN"
        ? "https://github.com/justForever17/v8-agent-os/blob/main/docs/NETWORK_SUPERVISOR_RUNTIME_IMPLEMENTATION_PLAN_ZH.md"
        : "https://github.com/justForever17/v8-agent-os/blob/main/docs/NETWORK_SUPERVISOR_RUNTIME_IMPLEMENTATION_PLAN.md";
    const availability = status.toolAvailability?.delegate_network_task || status.delegationAvailability;
    const compatBaseUrl = `${adminOrigin}/api/network-supervisor/openai/v1`;
    const compatChatUrl = `${compatBaseUrl}/chat/completions`;
    const compatModelsUrl = `${compatBaseUrl}/models`;
    const anthropicCompatBaseUrl = `${adminOrigin}/api/network-supervisor/anthropic`;
    const anthropicCompatMessagesUrl = `${anthropicCompatBaseUrl}/v1/messages`;
    const anthropicCompatModelsUrl = `${anthropicCompatBaseUrl}/v1/models`;
    const primaryModelAlias = (config.openaiCompat.modelAliases || ["v8os"]).find((item) => String(item || "").trim()) || "v8os";
    const primaryToken = tokens[0]?.token || "";
    const primaryApiKey = primaryToken || "<API_KEY>";
    const scopeModeOptions = getAdminOptions("networkScopeMode");
    const enrollmentModeOptions = getAdminOptions("networkEnrollmentMode");
    const hasCurrentScopeModeOption = scopeModeOptions.some((option) => option.value === config.openaiCompat.defaultScopeMode);
    const hasCurrentEnrollmentModeOption = enrollmentModeOptions.some((option) => option.value === config.trust.enrollmentMode);
    const curlExample = `curl ${compatChatUrl} \\
  -H "Authorization: Bearer ${primaryApiKey}" \\
  -H "Content-Type: application/json" \\
  -d "{\\"model\\":\\"${primaryModelAlias}\\",\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"ping\\"}]}"`;
    const sdkExample = `from openai import OpenAI

client = OpenAI(
    base_url="${compatBaseUrl}",
    api_key="${primaryApiKey}",
)

response = client.chat.completions.create(
    model="${primaryModelAlias}",
    messages=[{"role": "user", "content": "ping"}],
)`;
    const anthropicCurlExample = `curl ${anthropicCompatMessagesUrl} \\
  -H "x-api-key: ${primaryApiKey}" \\
  -H "anthropic-version: 2023-06-01" \\
  -H "Content-Type: application/json" \\
  -d "{\\"model\\":\\"${primaryModelAlias}\\",\\"max_tokens\\":256,\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"ping\\"}]}"`;
    const claudeCodeExample = `ANTHROPIC_BASE_URL=${anthropicCompatBaseUrl}
ANTHROPIC_AUTH_TOKEN=${primaryApiKey}
ANTHROPIC_MODEL=${primaryModelAlias}`;
    const acpCommand = "v8os acp";
    const acpSourceCommand = "python apps/v8-agent-os-engine/scripts/v8os_acp_agent.py";
    const thirdPartyConnectionCards = [
        {
            title: "OpenAI-compatible",
            purpose: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.thirdParty.openaiPurpose"),
            endpointLabel: "baseURL",
            endpoint: compatBaseUrl,
            credentialLabel: "apiKey",
            credential: primaryApiKey,
            modelLabel: "modelName",
            model: primaryModelAlias,
            example: `baseURL: ${compatBaseUrl}\napiKey: ${primaryApiKey}\nmodelName: ${primaryModelAlias}`,
            canonicalId: "network_supervisor.openai_compat",
        },
        {
            title: "Anthropic-compatible",
            purpose: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.thirdParty.anthropicPurpose"),
            endpointLabel: "baseURL",
            endpoint: anthropicCompatBaseUrl,
            credentialLabel: "apiKey",
            credential: primaryApiKey,
            modelLabel: "modelName",
            model: primaryModelAlias,
            example: `baseURL: ${anthropicCompatBaseUrl}\napiKey: ${primaryApiKey}\nmodelName: ${primaryModelAlias}`,
            canonicalId: "network_supervisor.anthropic_compat",
        },
        {
            title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.thirdParty.acpTitle"),
            purpose: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.thirdParty.acpPurpose"),
            endpointLabel: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.thirdParty.command"),
            endpoint: acpCommand,
            credentialLabel: "V8OS_CLIENT_TOKEN",
            credential: primaryApiKey,
            modelLabel: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.thirdParty.agent"),
            model: "V8OS Agent",
            example: `command: ${acpCommand}
source checkout: ${acpSourceCommand}
env:
  V8OS_ADMIN_URL=${adminOrigin}
  V8OS_CLIENT_TOKEN=${primaryApiKey}`,
            canonicalId: "acp_bridge",
        },
    ];
    const portNotices = bridgeDiagnostics?.notices || [];
    const neighborCopy = React.useMemo(() => ({
        title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.title"),
        description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.description"),
        enabled: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.enabled"),
        disabled: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.disabled"),
        switchOn: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.switchOn"),
        switchOff: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.switchOff"),
        createCode: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.createCode"),
        codeHint: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.codeHint"),
        candidates: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.candidates"),
        connected: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.connected"),
        noCandidates: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.noCandidates"),
        noLinks: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.noLinks"),
        connect: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.connect"),
        consumeCode: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.consumeCode"),
        localNickname: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.localNickname"),
        remoteNickname: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.remoteNickname"),
        role: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.role"),
        capabilityTags: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.capabilityTags"),
        capabilityPlaceholder: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.capabilityPlaceholder"),
        deviceDescription: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.deviceDescription"),
        descriptionPlaceholder: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.descriptionPlaceholder"),
        primary: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.primary"),
        companion: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.companion"),
        save: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.save"),
        revoke: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.revoke"),
        timeline: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.timeline"),
        send: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.send"),
        sendWake: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.sendWake"),
        messagePlaceholder: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.messagePlaceholder"),
        tasks: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.tasks"),
        taskHint: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.taskHint"),
        resultPolicy: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.resultPolicy"),
        inboxOnly: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.inboxOnly"),
        wakeEach: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.wakeEach"),
        taskPlaceholder: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.taskPlaceholder"),
        taskCapabilities: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.taskCapabilities"),
        sendTask: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.sendTask"),
        sendTaskAll: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.sendTaskAll"),
        noTasks: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.noTasks"),
        advanced: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.advanced"),
        advancedHint: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.advancedHint"),
        errorStatusRead: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorStatusRead"),
        errorCandidatesRead: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorCandidatesRead"),
        errorLinksRead: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorLinksRead"),
        errorTaskSettingsRead: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorTaskSettingsRead"),
        errorTasksRead: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorTasksRead"),
        errorTimelineRead: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorTimelineRead"),
        errorSwitch: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorSwitch"),
        errorInvite: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorInvite"),
        errorMissingPairing: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorMissingPairing"),
        errorPair: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorPair"),
        errorSaveLink: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorSaveLink"),
        errorRevoke: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorRevoke"),
        errorSendMessage: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorSendMessage"),
        errorTaskSettingsSave: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorTaskSettingsSave"),
        errorDispatchTask: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.neighbor.errorDispatchTask"),
    }), [t]);
    const relayCopy = React.useMemo(() => ({
        title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.title"),
        description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.description"),
        enabled: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.enabled"),
        disabled: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.disabled"),
        ready: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.ready"),
        needsConfig: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.needsConfig"),
        selfHosted: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.selfHosted"),
        selfHostedHint: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.selfHostedHint"),
        cloudflare: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.cloudflare"),
        cloudflareHint: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.cloudflareHint"),
        baseUrl: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.baseUrl"),
        websocketUrl: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.websocketUrl"),
        endpointHint: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.endpointHint"),
        workerName: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.workerName"),
        queueName: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.queueName"),
        durableObject: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.durableObject"),
        accountHint: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.accountHint"),
        ttl: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.ttl"),
        maxPayload: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.maxPayload"),
        e2e: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.e2e"),
        storeForward: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.storeForward"),
        save: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.save"),
        guide: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.guide"),
        protocol: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.protocol"),
        active: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.relay.active"),
    }), [t]);
    const selectedNeighborLink = neighborLinks.find((item) => item.linkId === selectedNeighborLinkId) || neighborLinks[0] || null;
    const relayAdapters = normalizeRelayAdapters(config.relay.adapters);
    const selectedRelayAdapter = relayAdapters.find((item) => item.id === config.relay.activeAdapterId) || relayAdapters[0];
    const relayStatusAdapter = status.relay?.activeAdapter;
    const relayAvailable = Boolean(status.relay?.available);
    const availabilityReason = React.useCallback((reason: string) => {
        switch (reason) {
            case "runtime_disabled":
                return t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kc3470d8c");
            case "delegation_disabled":
                return t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k8bba51bf");
            case "no_trusted_peers":
                return t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k9091ec19");
            case "no_online_trusted_peers":
                return t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k226ad4b8");
            default:
                return reason;
        }
    }, [t]);
    const renderPortNotice = React.useCallback((notice: LegacyPortNotice) => {
        switch (notice.code) {
            case "config_migrated":
                return t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k8e78c39c", {
                    notice_path: notice.path
                });
            case "admin_env_legacy_ports":
                return t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k1cefa135", {
                    notice_path: notice.path
                });
            case "web_env_legacy_ports":
                return t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k2e55f716", {
                    notice_path: notice.path
                });
            default:
                return notice.path;
        }
    }, [t]);
    React.useEffect(() => {
        if (typeof window !== "undefined" && window.location?.origin) {
            setAdminOrigin(window.location.origin);
        }
    }, []);
    const loadDocumentation = React.useCallback(async () => {
        try {
            const response = await fetch(locale.startsWith("en") ? "/NETWORK_RUNTIME.en.md" : "/NETWORK_RUNTIME.zh-CN.md", { cache: "no-store" });
            const fallback = await fetch("/NETWORK_RUNTIME.zh-CN.md", { cache: "no-store" });
            const text = response.ok ? await response.text() : await fallback.text();
            setDocContent(text);
            setGuideOpen(true);
        }
        catch (error) {
            console.error("Failed to load Network Runtime guide:", error);
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.guideLoadFailed"),
                description: error instanceof Error ? error.message : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.guideLoadFailedDescription"),
            });
        }
    }, [locale, t, toast]);
    const loadAll = React.useCallback(async () => {
        setLoading(true);
        setLoadError(null);
        try {
            const [configRes, statusRes, tokenRes, neighborStatusRes, neighborCandidatesRes, neighborLinksRes, neighborTaskSettingsRes, neighborTasksRes] = await Promise.all([
                fetch("/api/config-registry/network-supervisor-runtime", { cache: "no-store" }),
                fetch("/api/network-supervisor/status", { cache: "no-store" }),
                fetch("/api/network-supervisor/openai/tokens", { cache: "no-store" }),
                fetch("/api/network-supervisor/neighbors/status", { cache: "no-store" }),
                fetch("/api/network-supervisor/neighbors/candidates", { cache: "no-store" }),
                fetch("/api/network-supervisor/neighbors/links", { cache: "no-store" }),
                fetch("/api/network-supervisor/neighbors/task-settings", { cache: "no-store" }),
                fetch("/api/network-supervisor/neighbors/tasks?limit=20", { cache: "no-store" }),
            ]);
            const [configData, statusData, tokenData, neighborStatusData, neighborCandidatesData, neighborLinksData, neighborTaskSettingsData, neighborTasksData] = await Promise.all([
                configRes.json().catch(() => ({})),
                statusRes.json().catch(() => ({})),
                tokenRes.json().catch(() => ({})),
                neighborStatusRes.json().catch(() => ({})),
                neighborCandidatesRes.json().catch(() => ({})),
                neighborLinksRes.json().catch(() => ({})),
                neighborTaskSettingsRes.json().catch(() => ({})),
                neighborTasksRes.json().catch(() => ({})),
            ]);
            if (!configRes.ok)
                throw new Error(detail(configData, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k8d2fb12f")));
            if (!statusRes.ok)
                throw new Error(detail(statusData, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k5dcce62e")));
            if (!tokenRes.ok)
                throw new Error(detail(tokenData, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatTokensReadFailed")));
            if (!neighborStatusRes.ok)
                throw new Error(detail(neighborStatusData, neighborCopy.errorStatusRead));
            if (!neighborCandidatesRes.ok)
                throw new Error(detail(neighborCandidatesData, neighborCopy.errorCandidatesRead));
            if (!neighborLinksRes.ok)
                throw new Error(detail(neighborLinksData, neighborCopy.errorLinksRead));
            if (!neighborTaskSettingsRes.ok)
                throw new Error(detail(neighborTaskSettingsData, neighborCopy.errorTaskSettingsRead));
            if (!neighborTasksRes.ok)
                throw new Error(detail(neighborTasksData, neighborCopy.errorTasksRead));
            setConfig(mergeConfig((configData as {
                data?: Partial<RuntimeConfig>;
            }).data));
            setStatus(normalizeStatus(statusData));
            setTokens(Array.isArray((tokenData as { items?: OpenAICompatToken[] }).items) ? (tokenData as { items: OpenAICompatToken[] }).items : []);
            setNeighborStatus((neighborStatusData && typeof neighborStatusData === "object" ? neighborStatusData : EMPTY_NEIGHBOR_STATUS) as NeighborStatus);
            setNeighborCandidates(Array.isArray((neighborCandidatesData as { items?: NeighborCandidate[] }).items) ? (neighborCandidatesData as { items: NeighborCandidate[] }).items : []);
            const nextLinks = Array.isArray((neighborLinksData as { items?: NeighborLink[] }).items) ? (neighborLinksData as { items: NeighborLink[] }).items : [];
            setNeighborLinks(nextLinks);
            setSelectedNeighborLinkId((prev) => prev && nextLinks.some((item) => item.linkId === prev) ? prev : (nextLinks[0]?.linkId || ""));
            const taskSettings = neighborTaskSettingsData as Partial<NeighborTaskSettings>;
            setNeighborTaskSettings({ resultWakePolicy: taskSettings.resultWakePolicy === "per_result" ? "per_result" : "inbox" });
            setNeighborTasks(Array.isArray((neighborTasksData as { items?: NeighborTaskItem[] }).items) ? (neighborTasksData as { items: NeighborTaskItem[] }).items : []);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k105089b2");
            setLoadError(message);
            toast({ variant: "destructive", title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k65ed1d75"), description: message });
        }
        finally {
            setLoading(false);
        }
    }, [neighborCopy, t, toast]);
    React.useEffect(() => {
        void loadAll();
    }, [loadAll]);
    const loadNeighborTimeline = React.useCallback(async (linkId: string) => {
        if (!linkId) {
            setNeighborTimeline([]);
            return;
        }
        try {
            const response = await fetch(`/api/network-supervisor/neighbors/${encodeURIComponent(linkId)}/timeline`, { cache: "no-store" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok)
                throw new Error(detail(payload, neighborCopy.errorTimelineRead));
            setNeighborTimeline(Array.isArray((payload as { items?: NeighborMessage[] }).items) ? (payload as { items: NeighborMessage[] }).items : []);
        } catch (error) {
            toast({ variant: "destructive", title: neighborCopy.errorTimelineRead, description: error instanceof Error ? error.message : String(error) });
        }
    }, [neighborCopy, toast]);
    React.useEffect(() => {
        if (!selectedNeighborLink) {
            setLinkDraft({ localNickname: "", remoteNickname: "", localRole: "primary", capabilityTags: "", description: "" });
            setNeighborTimeline([]);
            return;
        }
        setLinkDraft({
            localNickname: selectedNeighborLink.localNickname || "",
            remoteNickname: selectedNeighborLink.remoteNickname || selectedNeighborLink.displayName || "",
            localRole: selectedNeighborLink.localRole || "primary",
            capabilityTags: joinCsv(selectedNeighborLink.capabilityTags),
            description: selectedNeighborLink.description || "",
        });
        void loadNeighborTimeline(selectedNeighborLink.linkId);
    }, [loadNeighborTimeline, selectedNeighborLink]);
    const toggleNeighbors = React.useCallback(async () => {
        setNeighborBusy("switch");
        try {
            const response = await fetch("/api/network-supervisor/neighbors/switch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled: !neighborStatus.enabled }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok)
                throw new Error(detail(payload, neighborCopy.errorSwitch));
            await loadAll();
        } catch (error) {
            toast({ variant: "destructive", title: neighborCopy.errorSwitch, description: error instanceof Error ? error.message : String(error) });
        } finally {
            setNeighborBusy("");
        }
    }, [loadAll, neighborCopy, neighborStatus.enabled, toast]);
    const createPairingInvite = React.useCallback(async () => {
        setNeighborBusy("invite");
        try {
            const response = await fetch("/api/network-supervisor/neighbors/pairing/invitations", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ localRole: "primary", localNickname: neighborStatus.node?.displayName || "" }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok)
                throw new Error(detail(payload, neighborCopy.errorInvite));
            setPairingInvite(payload as { code: string; expiresAt: string; inviteId: string });
        } catch (error) {
            toast({ variant: "destructive", title: neighborCopy.errorInvite, description: error instanceof Error ? error.message : String(error) });
        } finally {
            setNeighborBusy("");
        }
    }, [neighborCopy, neighborStatus.node?.displayName, toast]);
    const consumePairingInvite = React.useCallback(async () => {
        const peerId = pairingPeerId.trim();
        if (!peerId || !pairingCode.trim()) {
            toast({ variant: "destructive", title: neighborCopy.errorMissingPairing });
            return;
        }
        setNeighborBusy("pair");
        try {
            const response = await fetch("/api/network-supervisor/neighbors/pairing/consume", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ peerId, code: pairingCode.trim(), localNickname: neighborStatus.node?.displayName || "" }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok)
                throw new Error(detail(payload, neighborCopy.errorPair));
            setPairingCode("");
            setPairingPeerId("");
            await loadAll();
        } catch (error) {
            toast({ variant: "destructive", title: neighborCopy.errorPair, description: error instanceof Error ? error.message : String(error) });
        } finally {
            setNeighborBusy("");
        }
    }, [loadAll, neighborCopy, neighborStatus.node?.displayName, pairingCode, pairingPeerId, toast]);
    const saveNeighborLink = React.useCallback(async () => {
        if (!selectedNeighborLink)
            return;
        setNeighborBusy("link");
        try {
            const response = await fetch(`/api/network-supervisor/neighbors/${encodeURIComponent(selectedNeighborLink.linkId)}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(linkDraft),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok)
                throw new Error(detail(payload, neighborCopy.errorSaveLink));
            await loadAll();
        } catch (error) {
            toast({ variant: "destructive", title: neighborCopy.errorSaveLink, description: error instanceof Error ? error.message : String(error) });
        } finally {
            setNeighborBusy("");
        }
    }, [linkDraft, loadAll, neighborCopy, selectedNeighborLink, toast]);
    const revokeNeighborLink = React.useCallback(async (linkId: string) => {
        setNeighborBusy("revoke");
        try {
            const response = await fetch(`/api/network-supervisor/neighbors/${encodeURIComponent(linkId)}`, { method: "DELETE" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok)
                throw new Error(detail(payload, neighborCopy.errorRevoke));
            await loadAll();
        } catch (error) {
            toast({ variant: "destructive", title: neighborCopy.errorRevoke, description: error instanceof Error ? error.message : String(error) });
        } finally {
            setNeighborBusy("");
        }
    }, [loadAll, neighborCopy, toast]);
    const sendNeighborMessage = React.useCallback(async (wakeSupervisor: boolean) => {
        if (!selectedNeighborLink || !neighborMessage.trim())
            return;
        setNeighborBusy(wakeSupervisor ? "sendWake" : "send");
        try {
            const response = await fetch(`/api/network-supervisor/neighbors/${encodeURIComponent(selectedNeighborLink.linkId)}/messages`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ body: neighborMessage.trim(), wakeSupervisor }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok)
                throw new Error(detail(payload, neighborCopy.errorSendMessage));
            setNeighborMessage("");
            await loadNeighborTimeline(selectedNeighborLink.linkId);
        } catch (error) {
            toast({ variant: "destructive", title: neighborCopy.errorSendMessage, description: error instanceof Error ? error.message : String(error) });
        } finally {
            setNeighborBusy("");
        }
    }, [loadNeighborTimeline, neighborCopy, neighborMessage, selectedNeighborLink, toast]);
    const saveNeighborTaskSettings = React.useCallback(async (resultWakePolicy: NeighborTaskSettings["resultWakePolicy"]) => {
        setNeighborTaskSettings({ resultWakePolicy });
        try {
            const response = await fetch("/api/network-supervisor/neighbors/task-settings", {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ resultWakePolicy }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok)
                throw new Error(detail(payload, neighborCopy.errorTaskSettingsSave));
            setNeighborTaskSettings({ resultWakePolicy: payload.resultWakePolicy === "per_result" ? "per_result" : "inbox" });
        } catch (error) {
            toast({ variant: "destructive", title: neighborCopy.errorTaskSettingsSave, description: error instanceof Error ? error.message : String(error) });
            await loadAll();
        }
    }, [loadAll, neighborCopy, toast]);
    const dispatchNeighborTask = React.useCallback(async (targetMode: "selected" | "all") => {
        if (!neighborTaskBody.trim())
            return;
        if (targetMode === "selected" && !selectedNeighborLink)
            return;
        setNeighborBusy(targetMode === "all" ? "taskAll" : "task");
        try {
            const response = await fetch("/api/network-supervisor/neighbors/tasks", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    body: neighborTaskBody.trim(),
                    target: targetMode === "all" ? "all" : undefined,
                    linkId: targetMode === "selected" ? selectedNeighborLink?.linkId : undefined,
                    requiredCapabilities: csv(neighborTaskCapabilities),
                    wakePolicy: neighborTaskSettings.resultWakePolicy,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok)
                throw new Error(detail(payload, neighborCopy.errorDispatchTask));
            setNeighborTaskBody("");
            const tasksResponse = await fetch("/api/network-supervisor/neighbors/tasks?limit=20", { cache: "no-store" });
            const tasksPayload = await tasksResponse.json().catch(() => ({}));
            setNeighborTasks(Array.isArray((tasksPayload as { items?: NeighborTaskItem[] }).items) ? (tasksPayload as { items: NeighborTaskItem[] }).items : []);
            if (selectedNeighborLink)
                await loadNeighborTimeline(selectedNeighborLink.linkId);
        } catch (error) {
            toast({ variant: "destructive", title: neighborCopy.errorDispatchTask, description: error instanceof Error ? error.message : String(error) });
        } finally {
            setNeighborBusy("");
        }
    }, [loadNeighborTimeline, neighborCopy, neighborTaskBody, neighborTaskCapabilities, neighborTaskSettings.resultWakePolicy, selectedNeighborLink, toast]);
    const setNode = React.useCallback((patch: Partial<RuntimeConfig["node"]>) => {
        setConfig((prev) => ({ ...prev, node: { ...prev.node, ...patch } }));
    }, []);
    const setDiscovery = React.useCallback((patch: Partial<RuntimeConfig["discovery"]>) => {
        setConfig((prev) => ({ ...prev, discovery: { ...prev.discovery, ...patch } }));
    }, []);
    const setTrust = React.useCallback((patch: Partial<RuntimeConfig["trust"]>) => {
        setConfig((prev) => ({ ...prev, trust: { ...prev.trust, ...patch } }));
    }, []);
    const setWake = React.useCallback((patch: Partial<RuntimeConfig["wake"]>) => {
        setConfig((prev) => ({ ...prev, wake: { ...prev.wake, ...patch } }));
    }, []);
    const setDelegation = React.useCallback((patch: Partial<RuntimeConfig["delegation"]>) => {
        setConfig((prev) => ({ ...prev, delegation: { ...prev.delegation, ...patch } }));
    }, []);
    const setRelay = React.useCallback((patch: Partial<RelayConfig>) => {
        setConfig((prev) => ({ ...prev, relay: mergeRelayConfig({ ...prev.relay, ...patch }) }));
    }, []);
    const selectRelayAdapter = React.useCallback((adapterId: string) => {
        setConfig((prev) => ({ ...prev, relay: mergeRelayConfig({ ...prev.relay, enabled: true, activeAdapterId: adapterId }) }));
    }, []);
    const setRelayAdapter = React.useCallback((adapterId: string, patch: Partial<RelayAdapterConfig>) => {
        setConfig((prev) => ({
            ...prev,
            relay: mergeRelayConfig({
                ...prev.relay,
                adapters: normalizeRelayAdapters(prev.relay.adapters).map((adapter) => adapter.id === adapterId ? { ...adapter, ...patch } : adapter),
            }),
        }));
    }, []);
    const setOpenAICompat = React.useCallback((patch: Partial<RuntimeConfig["openaiCompat"]>) => {
        setConfig((prev) => ({ ...prev, openaiCompat: { ...prev.openaiCompat, ...patch } }));
    }, []);
    const setOpenAICompatMainChainMode = React.useCallback((checked: boolean) => {
        if (checked && typeof window !== "undefined" && !window.confirm(t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatMainChainConfirm"))) {
            return;
        }
        setConfig((prev) => ({
            ...prev,
            openaiCompat: {
                ...prev.openaiCompat,
                v8MainChainModeEnabled: checked,
                allowWorkspaceHeaders: checked ? prev.openaiCompat.allowWorkspaceHeaders : false,
                allowRawWorkspacePath: checked ? prev.openaiCompat.allowRawWorkspacePath : false,
                defaultScopeMode: checked ? prev.openaiCompat.defaultScopeMode : "explicit",
            },
        }));
    }, [t]);
    const copyText = React.useCallback(async (value: string, label: string) => {
        if (!value) {
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopyFailed"),
                description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatMissingCopyValue"),
            });
            return;
        }
        try {
            await navigator.clipboard.writeText(value);
            toast({
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopied"),
                description: label,
            });
        } catch {
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopyFailed"),
                description: value,
            });
        }
    }, [t, toast]);
    const createCompatToken = React.useCallback(async () => {
        setTokenBusy(true);
        try {
            const response = await fetch("/api/network-supervisor/openai/tokens", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ label: tokenLabel.trim() || "OpenAI compat key" }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(detail(payload, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCreateFailed")));
            }
            setTokenLabel("");
            toast({
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCreated"),
                description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCreatedDescription"),
            });
            await loadAll();
        } catch (error) {
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCreateFailed"),
                description: error instanceof Error ? error.message : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatTokenMutationFailed"),
            });
        } finally {
            setTokenBusy(false);
        }
    }, [loadAll, t, toast, tokenLabel]);
    const deleteCompatToken = React.useCallback(async (tokenId: string) => {
        setTokenBusy(true);
        try {
            const response = await fetch(`/api/network-supervisor/openai/tokens/${encodeURIComponent(tokenId)}`, {
                method: "DELETE",
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(detail(payload, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatDeleteFailed")));
            }
            toast({
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatDeleted"),
                description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatDeletedDescription"),
            });
            await loadAll();
        } catch (error) {
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatDeleteFailed"),
                description: error instanceof Error ? error.message : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatTokenMutationFailed"),
            });
        } finally {
            setTokenBusy(false);
        }
    }, [loadAll, t, toast]);
    const fillPeerForm = React.useCallback((peer: PeerItem) => {
        setPeerForm({
            peerId: peer.peerId,
            displayName: peer.displayName,
            baseUrl: peer.baseUrl,
            wsUrl: peer.wsUrl,
            publicKey: peer.publicKey,
            allowedScopes: joinCsv(peer.allowedScopes),
            allowedWorkspaces: joinLines(peer.allowedWorkspaces),
            peerToken: "",
            transportProfileId: peer.transportProfileId || "",
            peerBaseUrl: peer.peerBaseUrl || "",
            deviceClass: "",
            requiresApproval: false,
        });
    }, []);
    const fillPeerFormFromMeshCandidate = React.useCallback((candidate: MeshCandidate) => {
        const displayName = candidate.hostName || candidate.dnsName || candidate.ips?.[0] || "";
        const peerBaseUrl = candidate.peerBaseUrl || (candidate.dnsName ? `http://${candidate.dnsName}:9530` : "");
        setPeerForm({
            ...EMPTY_PEER_FORM,
            peerId: String(candidate.id || displayName).replace(/^(tailscale|headscale):/i, ""),
            displayName,
            baseUrl: peerBaseUrl,
            transportProfileId: candidate.transportProfileId || "",
            peerBaseUrl,
            deviceClass: candidate.deviceClass || "",
            requiresApproval: Boolean(candidate.requiresApproval || candidate.deviceClass === "phone"),
        });
        if (candidate.deviceClass === "phone" || candidate.requiresApproval) {
            toast({
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.phonePeerApprovalTitle"),
                description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.phonePeerApprovalDescription"),
            });
        }
    }, [t, toast]);
    const chooseDiagPeer = React.useCallback((peerId: string) => {
        setDiag((prev) => ({ ...prev, peerId }));
    }, []);
    const saveConfig = React.useCallback(async () => {
        setSavingConfig(true);
        try {
            const response = await fetch("/api/config-registry/network-supervisor-runtime", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ data: config }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(detail(payload, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k6af6d443")));
            }
            setConfig(mergeConfig((payload as {
                data?: Partial<RuntimeConfig>;
            }).data));
            toast({
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k96aea0e5"),
                description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k059b20dc"),
            });
            await loadAll();
        }
        catch (error) {
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k12769ce1"),
                description: error instanceof Error ? error.message : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k65c24b3b"),
            });
        }
        finally {
            setSavingConfig(false);
        }
    }, [config, loadAll, t, toast]);
    const savePeer = React.useCallback(async () => {
        if (!peerForm.peerId.trim()) {
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k8d624164"),
                description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k7a77284c"),
            });
            return;
        }
        if (peerForm.requiresApproval) {
            if (!peerForm.publicKey.trim() || !peerForm.peerToken.trim()) {
                toast({
                    variant: "destructive",
                    title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.phonePeerApprovalTitle"),
                    description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.phonePeerCredentialsRequired"),
                });
                return;
            }
            if (typeof window !== "undefined" && !window.confirm(t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.phonePeerApprovalConfirm", { peer: peerForm.displayName || peerForm.peerId }))) {
                return;
            }
        }
        setSavingPeer(true);
        try {
            const response = await fetch("/api/network-supervisor/peers", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    peerId: peerForm.peerId.trim(),
                    displayName: peerForm.displayName.trim(),
                    baseUrl: peerForm.baseUrl.trim(),
                    wsUrl: peerForm.wsUrl.trim(),
                    publicKey: peerForm.publicKey.trim(),
                    allowedScopes: csv(peerForm.allowedScopes),
                    allowedWorkspaces: lines(peerForm.allowedWorkspaces),
                    peerToken: peerForm.peerToken.trim(),
                    transportProfileId: peerForm.transportProfileId.trim() || undefined,
                    peerBaseUrl: peerForm.peerBaseUrl.trim() || undefined,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(detail(payload, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kea6e165d")));
            }
            toast({
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k9079be43"),
                description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k3112734f"),
            });
            setPeerForm((prev) => ({ ...prev, peerToken: "" }));
            await loadAll();
        }
        catch (error) {
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k12769ce1"),
                description: error instanceof Error ? error.message : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k7ab1a5d4"),
            });
        }
        finally {
            setSavingPeer(false);
        }
    }, [loadAll, peerForm, t, toast]);
    const deletePeer = React.useCallback(async (peerId: string) => {
        try {
            const response = await fetch(`/api/network-supervisor/peers/${encodeURIComponent(peerId)}`, {
                method: "DELETE",
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(detail(payload, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kae4e2bf1")));
            }
            toast({
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kd70847b4"),
                description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k1901fc02"),
            });
            if (diag.peerId === peerId) {
                setDiag((prev) => ({ ...prev, peerId: "" }));
            }
            if (peerForm.peerId === peerId) {
                setPeerForm(EMPTY_PEER_FORM);
            }
            await loadAll();
        }
        catch (error) {
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k0915ccdf"),
                description: error instanceof Error ? error.message : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kc5c16e4f"),
            });
        }
    }, [diag.peerId, loadAll, peerForm.peerId, t, toast]);
    const runDiagnostic = React.useCallback(async (kind: "challenge" | "wake" | "delegate", peerId?: string) => {
        const targetPeerId = String(peerId || diag.peerId || "").trim();
        if (!targetPeerId) {
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k8836c6f2"),
                description: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kf9df9cc6"),
            });
            return;
        }
        setRunning(kind);
        try {
            const response = await fetch(kind === "delegate" ? "/api/network-supervisor/delegations" : `/api/network-supervisor/diagnostics/${kind}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    peerId: targetPeerId,
                    note: diag.note.trim(),
                    task: diag.task.trim(),
                    timeoutSeconds: 120,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(detail(payload, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k43c90b22")));
            }
            setDiag((prev) => ({ ...prev, peerId: targetPeerId, result: JSON.stringify(payload, null, 2) }));
            toast({
                title: t(kind === "challenge"
                    ? "components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k04fef58e"
                    : kind === "wake"
                        ? "components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kdb2f79cb"
                        : "components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k6d65e8f8"),
                description: t(kind === "delegate"
                    ? "components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k75ac8c0a"
                    : "components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k5c12cef1"),
            });
            await loadAll();
        }
        catch (error) {
            const message = error instanceof Error ? error.message : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kf64b0727");
            setDiag((prev) => ({ ...prev, result: message }));
            toast({
                variant: "destructive",
                title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kf7c06097"),
                description: message,
            });
        }
        finally {
            setRunning("");
        }
    }, [diag.note, diag.peerId, diag.task, loadAll, t, toast]);
    return (<AdminPageShell>
            <AdminPageHeader title={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k45a604ec"} description={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.description"} badges={["components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kcd4380d3", "components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k959f4745"]} actions={<>
                        <Button variant="outline" onClick={() => void loadAll()} disabled={loading}>
                            {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k876e8c06")}
                        </Button>
                        <Button variant="outline" onClick={() => void loadDocumentation()}>
                            {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.guideButton")}
                        </Button>
                        <Button asChild variant="outline">
                            <Link href={docsUrl} target="_blank">
                                {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kac8eaf7f")}
                            </Link>
                        </Button>
                    </>}/>

            {loadError ? (<ConfigCard title={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k65ed1d75"} description={loadError} className="border-red-200">
                    <div className="flex items-center justify-end">
                        <Button onClick={() => void loadAll()}>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k3a3e39b1")}</Button>
                    </div>
                </ConfigCard>) : null}

            {portNotices.length ? (<ConfigCard title={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k40de2d8c"} description={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k8cbba0cc"} variant="list" bodyHeight="auto">
                    <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-950">
                        <ul className="list-disc space-y-2 pl-5 leading-6">
                            {portNotices.map((notice, index) => (<li key={`${notice.code}-${notice.path}-${index}`}>{renderPortNotice(notice)}</li>))}
                        </ul>
                    </div>
                </ConfigCard>) : null}

            <ConfigCard title={neighborCopy.title} description={neighborCopy.description} variant="editor" bodyHeight="auto">
                <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
                    <div className="space-y-4">
                        <div className="rounded-3xl border border-slate-200 bg-gradient-to-br from-slate-950 to-slate-800 p-5 text-white shadow-sm">
                            <div className="flex items-start justify-between gap-4">
                                <div>
                                    <div className="text-sm text-slate-300">{neighborStatus.node?.displayName || "V8 Device"}</div>
                                    <div className="mt-2 text-2xl font-semibold">{neighborStatus.enabled ? neighborCopy.enabled : neighborCopy.disabled}</div>
                                    <div className="mt-2 text-xs text-slate-400">{neighborStatus.node?.peerId || "peer pending"}</div>
                                </div>
                                <Button variant={neighborStatus.enabled ? "secondary" : "default"} onClick={() => void toggleNeighbors()} disabled={neighborBusy === "switch"}>
                                    {neighborStatus.enabled ? neighborCopy.switchOff : neighborCopy.switchOn}
                                </Button>
                            </div>
                            <div className="mt-5 grid grid-cols-3 gap-2 text-center text-xs">
                                <div className="rounded-2xl bg-white/10 px-3 py-2">
                                    <div className="text-lg font-semibold">{neighborStatus.discovery?.candidateCount || neighborCandidates.length}</div>
                                    <div className="text-slate-300">{neighborCopy.candidates}</div>
                                </div>
                                <div className="rounded-2xl bg-white/10 px-3 py-2">
                                    <div className="text-lg font-semibold">{neighborStatus.discovery?.connectedCount || neighborLinks.length}</div>
                                    <div className="text-slate-300">{neighborCopy.connected}</div>
                                </div>
                                <div className="rounded-2xl bg-white/10 px-3 py-2">
                                    <div className="text-lg font-semibold">{neighborStatus.discovery?.lanEnabled ? "LAN" : "—"}</div>
                                    <div className="text-slate-300">Discovery</div>
                                </div>
                            </div>
                        </div>

                        <div className="rounded-3xl border border-slate-200 bg-white p-4">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <div className="text-sm font-semibold text-slate-900">{neighborCopy.createCode}</div>
                                    <div className="mt-1 text-xs text-slate-500">{neighborCopy.codeHint}</div>
                                </div>
                                <Button variant="outline" size="sm" onClick={() => void createPairingInvite()} disabled={neighborBusy === "invite"}>
                                    {neighborCopy.createCode}
                                </Button>
                            </div>
                            {pairingInvite ? (
                                <div className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3">
                                    <div className="font-mono text-2xl font-semibold tracking-[0.3em] text-emerald-950">{pairingInvite.code}</div>
                                    <div className="mt-1 text-xs text-emerald-700">{pairingInvite.expiresAt}</div>
                                </div>
                            ) : null}
                        </div>

                        <div className="rounded-3xl border border-slate-200 bg-white p-4">
                            <div className="mb-3 flex items-center justify-between">
                                <div className="text-sm font-semibold text-slate-900">{neighborCopy.candidates}</div>
                                <Badge variant="outline">{neighborCandidates.length}</Badge>
                            </div>
                            {neighborCandidates.length ? (
                                <div className="space-y-2">
                                    {neighborCandidates.map((candidate) => (
                                        <div key={candidate.peerId} className="flex items-center justify-between gap-3 rounded-2xl bg-slate-50 px-3 py-3">
                                            <div className="min-w-0">
                                                <div className="truncate text-sm font-medium text-slate-900">{candidate.displayName || candidate.peerId}</div>
                                                <div className="truncate text-xs text-slate-500">{candidate.baseUrl || candidate.address || candidate.source || candidate.peerId}</div>
                                            </div>
                                            <Button size="sm" variant="outline" onClick={() => setPairingPeerId(candidate.peerId)}>
                                                {neighborCopy.connect}
                                            </Button>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="rounded-2xl bg-slate-50 px-3 py-4 text-sm text-slate-500">{neighborCopy.noCandidates}</div>
                            )}
                            <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_0.7fr_auto]">
                                <Input value={pairingPeerId} onChange={(event) => setPairingPeerId(event.target.value)} placeholder="peer_xxx"/>
                                <Input value={pairingCode} onChange={(event) => setPairingCode(event.target.value.toUpperCase())} placeholder="CODE"/>
                                <Button onClick={() => void consumePairingInvite()} disabled={neighborBusy === "pair"}>
                                    {neighborCopy.consumeCode}
                                </Button>
                            </div>
                        </div>
                    </div>

                    <div className="space-y-4">
                        <div className="rounded-3xl border border-slate-200 bg-white p-4">
                            <div className="mb-3 flex items-center justify-between">
                                <div className="text-sm font-semibold text-slate-900">{neighborCopy.connected}</div>
                                <Badge variant="outline">{neighborLinks.length}</Badge>
                            </div>
                            {neighborLinks.length ? (
                                <div className="grid gap-2">
                                    {neighborLinks.map((link) => (
                                        <button key={link.linkId} type="button" onClick={() => setSelectedNeighborLinkId(link.linkId)} className={`rounded-2xl border px-4 py-3 text-left transition ${selectedNeighborLink?.linkId === link.linkId ? "border-slate-900 bg-slate-950 text-white" : "border-slate-200 bg-slate-50 text-slate-900 hover:border-slate-300"}`}>
                                            <div className="flex items-center justify-between gap-3">
                                                <div className="min-w-0">
                                                    <div className="truncate text-sm font-semibold">{link.remoteNickname || link.displayName || link.peerId}</div>
                                                    <div className={`truncate text-xs ${selectedNeighborLink?.linkId === link.linkId ? "text-slate-300" : "text-slate-500"}`}>{link.localRole === "primary" ? neighborCopy.primary : neighborCopy.companion} · {link.online ? "online" : "offline"}</div>
                                                    {link.capabilityTags?.length ? (
                                                        <div className={`mt-1 truncate text-[11px] ${selectedNeighborLink?.linkId === link.linkId ? "text-slate-300" : "text-slate-500"}`}>{link.capabilityTags.join(" · ")}</div>
                                                    ) : null}
                                                </div>
                                                <Badge variant={link.online ? "default" : "secondary"}>{link.online ? "●" : "○"}</Badge>
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            ) : (
                                <div className="rounded-2xl bg-slate-50 px-3 py-4 text-sm text-slate-500">{neighborCopy.noLinks}</div>
                            )}
                        </div>

                        {selectedNeighborLink ? (
                            <div className="rounded-3xl border border-slate-200 bg-white p-4">
                                <div className="grid gap-3 sm:grid-cols-3">
                                    <div className="grid gap-2">
                                        <Label>{neighborCopy.localNickname}</Label>
                                        <Input value={linkDraft.localNickname} onChange={(event) => setLinkDraft((prev) => ({ ...prev, localNickname: event.target.value }))}/>
                                    </div>
                                    <div className="grid gap-2">
                                        <Label>{neighborCopy.remoteNickname}</Label>
                                        <Input value={linkDraft.remoteNickname} onChange={(event) => setLinkDraft((prev) => ({ ...prev, remoteNickname: event.target.value }))}/>
                                    </div>
                                    <div className="grid gap-2">
                                        <Label>{neighborCopy.role}</Label>
                                        <Select value={linkDraft.localRole} onValueChange={(value) => setLinkDraft((prev) => ({ ...prev, localRole: value === "companion" ? "companion" : "primary" }))}>
                                            <SelectTrigger><SelectValue /></SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="primary">{neighborCopy.primary}</SelectItem>
                                                <SelectItem value="companion">{neighborCopy.companion}</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </div>
                                <div className="mt-3 grid gap-3 sm:grid-cols-[0.8fr_1.2fr]">
                                    <div className="grid gap-2">
                                        <Label>{neighborCopy.capabilityTags}</Label>
                                        <Input value={linkDraft.capabilityTags} onChange={(event) => setLinkDraft((prev) => ({ ...prev, capabilityTags: event.target.value }))} placeholder={neighborCopy.capabilityPlaceholder}/>
                                    </div>
                                    <div className="grid gap-2">
                                        <Label>{neighborCopy.deviceDescription}</Label>
                                        <Input value={linkDraft.description} onChange={(event) => setLinkDraft((prev) => ({ ...prev, description: event.target.value }))} placeholder={neighborCopy.descriptionPlaceholder}/>
                                    </div>
                                </div>
                                <div className="mt-3 flex justify-end gap-2">
                                    <Button variant="outline" onClick={() => void revokeNeighborLink(selectedNeighborLink.linkId)} disabled={neighborBusy === "revoke"}>{neighborCopy.revoke}</Button>
                                    <Button onClick={() => void saveNeighborLink()} disabled={neighborBusy === "link"}>{neighborCopy.save}</Button>
                                </div>
                            </div>
                        ) : null}

                        <div className="rounded-3xl border border-slate-200 bg-white p-4">
                            <div className="mb-3 text-sm font-semibold text-slate-900">{neighborCopy.timeline}</div>
                            <div className="max-h-72 space-y-2 overflow-auto rounded-2xl bg-slate-50 p-3">
                                {neighborTimeline.length ? neighborTimeline.map((message) => (
                                    <div key={message.messageId} className={`rounded-2xl px-3 py-2 text-sm ${message.direction === "outbound" ? "ml-8 bg-slate-900 text-white" : "mr-8 bg-white text-slate-900"}`}>
                                        <div className="text-xs opacity-70">{message.fromNickname || message.role || message.status}</div>
                                        <div className="mt-1 whitespace-pre-wrap break-words">{message.preview || message.body}</div>
                                    </div>
                                )) : (
                                    <div className="py-8 text-center text-sm text-slate-500">{neighborCopy.timeline}</div>
                                )}
                            </div>
                            <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto_auto]">
                                <Input value={neighborMessage} onChange={(event) => setNeighborMessage(event.target.value)} placeholder={neighborCopy.messagePlaceholder}/>
                                <Button variant="outline" onClick={() => void sendNeighborMessage(false)} disabled={!selectedNeighborLink || neighborBusy === "send"}>{neighborCopy.send}</Button>
                                <Button onClick={() => void sendNeighborMessage(true)} disabled={!selectedNeighborLink || neighborBusy === "sendWake"}>{neighborCopy.sendWake}</Button>
                            </div>
                        </div>

                        <div className="rounded-3xl border border-slate-200 bg-white p-4">
                            <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                                <div>
                                    <div className="text-sm font-semibold text-slate-900">{neighborCopy.tasks}</div>
                                    <div className="mt-1 text-xs text-slate-500">{neighborCopy.taskHint}</div>
                                </div>
                                <div className="grid min-w-40 gap-1">
                                    <Label className="text-xs">{neighborCopy.resultPolicy}</Label>
                                    <Select value={neighborTaskSettings.resultWakePolicy} onValueChange={(value) => void saveNeighborTaskSettings(value === "per_result" ? "per_result" : "inbox")}>
                                        <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="inbox">{neighborCopy.inboxOnly}</SelectItem>
                                            <SelectItem value="per_result">{neighborCopy.wakeEach}</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                            <div className="grid gap-2">
                                <Textarea value={neighborTaskBody} onChange={(event) => setNeighborTaskBody(event.target.value)} placeholder={neighborCopy.taskPlaceholder} rows={3}/>
                                <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
                                    <Input value={neighborTaskCapabilities} onChange={(event) => setNeighborTaskCapabilities(event.target.value)} placeholder={neighborCopy.taskCapabilities}/>
                                    <Button variant="outline" onClick={() => void dispatchNeighborTask("selected")} disabled={!selectedNeighborLink || neighborBusy === "task" || !neighborTaskBody.trim()}>{neighborCopy.sendTask}</Button>
                                    <Button onClick={() => void dispatchNeighborTask("all")} disabled={!neighborLinks.length || neighborBusy === "taskAll" || !neighborTaskBody.trim()}>{neighborCopy.sendTaskAll}</Button>
                                </div>
                            </div>
                            <div className="mt-4 max-h-64 space-y-2 overflow-auto rounded-2xl bg-slate-50 p-3">
                                {neighborTasks.length ? neighborTasks.map((task) => (
                                    <div key={task.taskId} className="rounded-2xl bg-white px-3 py-3 text-sm">
                                        <div className="flex items-center justify-between gap-3">
                                            <div className="min-w-0">
                                                <div className="truncate font-semibold text-slate-900">{task.title || task.taskId}</div>
                                                <div className="mt-1 truncate text-xs text-slate-500">{task.taskId} · {task.requiredCapabilities?.join(", ") || "auto"}</div>
                                            </div>
                                            <Badge variant={task.status === "completed" ? "default" : "secondary"}>{task.status || "queued"}</Badge>
                                        </div>
                                        <div className="mt-2 grid gap-2 text-xs text-slate-500 sm:grid-cols-2">
                                            <div>{task.assignments?.length || 0} assignments</div>
                                            <div>{task.results?.length || 0} results · {task.wakePolicy || "inbox"}</div>
                                        </div>
                                    </div>
                                )) : (
                                    <div className="py-8 text-center text-sm text-slate-500">{neighborCopy.noTasks}</div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            </ConfigCard>

            <ConfigCard title={relayCopy.title} description={relayCopy.description} variant="editor" bodyHeight="auto">
                <div className="space-y-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex flex-wrap items-center gap-2">
                            <Badge variant={config.relay.enabled ? "default" : "secondary"}>{config.relay.enabled ? relayCopy.enabled : relayCopy.disabled}</Badge>
                            <Badge variant={relayAvailable ? "default" : "secondary"}>{relayAvailable ? relayCopy.ready : relayCopy.needsConfig}</Badge>
                            <Badge variant="outline">{status.relay?.protocol?.version || config.relay.protocolVersion || "v8-relay.v1"}</Badge>
                            {relayStatusAdapter?.status ? <Badge variant="outline">{relayStatusAdapter.status}</Badge> : null}
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <Button type="button" variant="outline" onClick={() => void loadDocumentation()}>
                                {relayCopy.guide}
                            </Button>
                            <Button type="button" onClick={() => void saveConfig()} disabled={savingConfig}>
                                {savingConfig ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kc225e8a3") : relayCopy.save}
                            </Button>
                        </div>
                    </div>

                    <div className="grid gap-3 md:grid-cols-2">
                        {relayAdapters.map((adapter) => {
                            const active = adapter.id === config.relay.activeAdapterId;
                            const adapterStatus = status.relay?.adapters?.find((item) => item.id === adapter.id);
                            const title = adapter.kind === "cloudflare" ? relayCopy.cloudflare : relayCopy.selfHosted;
                            const hint = adapter.kind === "cloudflare" ? relayCopy.cloudflareHint : relayCopy.selfHostedHint;
                            return (
                                <button
                                    key={adapter.id}
                                    type="button"
                                    onClick={() => selectRelayAdapter(adapter.id)}
                                    className={`group rounded-3xl border p-4 text-left transition ${active ? "border-slate-950 bg-slate-950 text-white shadow-sm" : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"}`}
                                >
                                    <div className="flex items-start justify-between gap-3">
                                        <div>
                                            <div className={`text-sm font-semibold ${active ? "text-white" : "text-slate-950"}`}>{title}</div>
                                            <div className={`mt-1 text-xs leading-5 ${active ? "text-slate-300" : "text-slate-500"}`}>{hint}</div>
                                        </div>
                                        <Badge variant={adapterStatus?.configured ? "default" : "secondary"}>{active ? relayCopy.active : (adapterStatus?.configured ? relayCopy.ready : relayCopy.needsConfig)}</Badge>
                                    </div>
                                    <div className={`mt-4 truncate rounded-2xl px-3 py-2 font-mono text-xs ${active ? "bg-white/10 text-slate-200" : "bg-slate-50 text-slate-500"}`}>
                                        {adapter.baseUrl || adapterStatus?.baseUrl || "https://relay.example.com"}
                                    </div>
                                </button>
                            );
                        })}
                    </div>

                    {selectedRelayAdapter ? (
                        <div className="rounded-3xl border border-slate-200 bg-white p-4">
                            <div className="grid gap-4 lg:grid-cols-[1fr_0.9fr]">
                                <div className="space-y-4">
                                    <SettingToggleCard
                                        title={<span className="text-sm font-medium text-slate-900">{relayCopy.enabled}</span>}
                                        description={relayCopy.endpointHint}
                                        checked={config.relay.enabled}
                                        onCheckedChange={(checked) => {
                                            setConfig((prev) => ({ ...prev, enabled: checked ? true : prev.enabled, relay: mergeRelayConfig({ ...prev.relay, enabled: checked }) }));
                                        }}
                                        className="rounded-2xl border border-slate-200 px-4 py-3 bg-slate-50 shadow-none hover:bg-slate-50"
                                    />
                                    <div className="grid gap-3 sm:grid-cols-2">
                                        <div className="space-y-2">
                                            <Label htmlFor="relay-base-url">{relayCopy.baseUrl}</Label>
                                            <Input id="relay-base-url" value={selectedRelayAdapter.baseUrl || ""} onChange={(event) => setRelayAdapter(selectedRelayAdapter.id, { baseUrl: event.target.value })} placeholder="https://relay.example.com"/>
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="relay-websocket-url">{relayCopy.websocketUrl}</Label>
                                            <Input id="relay-websocket-url" value={selectedRelayAdapter.websocketUrl || ""} onChange={(event) => setRelayAdapter(selectedRelayAdapter.id, { websocketUrl: event.target.value })} placeholder="wss://relay.example.com/v1/relay/ws"/>
                                        </div>
                                    </div>
                                    {selectedRelayAdapter.kind === "cloudflare" ? (
                                        <div className="grid gap-3 sm:grid-cols-2">
                                            <div className="space-y-2">
                                                <Label htmlFor="relay-cf-worker">{relayCopy.workerName}</Label>
                                                <Input id="relay-cf-worker" value={selectedRelayAdapter.cloudflareWorkerName || ""} onChange={(event) => setRelayAdapter(selectedRelayAdapter.id, { cloudflareWorkerName: event.target.value })} placeholder="v8-relay"/>
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="relay-cf-queue">{relayCopy.queueName}</Label>
                                                <Input id="relay-cf-queue" value={selectedRelayAdapter.cloudflareQueueName || ""} onChange={(event) => setRelayAdapter(selectedRelayAdapter.id, { cloudflareQueueName: event.target.value })} placeholder="v8-relay-mailbox"/>
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="relay-cf-do">{relayCopy.durableObject}</Label>
                                                <Input id="relay-cf-do" value={selectedRelayAdapter.cloudflareDurableObjectNamespace || ""} onChange={(event) => setRelayAdapter(selectedRelayAdapter.id, { cloudflareDurableObjectNamespace: event.target.value })} placeholder="V8RelayRoom"/>
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="relay-cf-account">{relayCopy.accountHint}</Label>
                                                <Input id="relay-cf-account" value={selectedRelayAdapter.cloudflareAccountHint || ""} onChange={(event) => setRelayAdapter(selectedRelayAdapter.id, { cloudflareAccountHint: event.target.value })} placeholder="team@example.com"/>
                                            </div>
                                        </div>
                                    ) : null}
                                </div>

                                <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                                    <div className="flex items-center justify-between gap-3">
                                        <div className="text-sm font-semibold text-slate-900">{relayCopy.protocol}</div>
                                        <Badge variant="outline">{config.relay.protocolVersion}</Badge>
                                    </div>
                                    <div className="grid gap-3 sm:grid-cols-2">
                                        <div className="space-y-2">
                                            <Label htmlFor="relay-ttl">{relayCopy.ttl}</Label>
                                            <Input id="relay-ttl" type="number" min={60} value={String(config.relay.defaultTtlSeconds)} onChange={(event) => setRelay({ defaultTtlSeconds: Number(event.target.value || 300) })}/>
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="relay-max-payload">{relayCopy.maxPayload}</Label>
                                            <Input id="relay-max-payload" type="number" min={4096} value={String(config.relay.maxPayloadBytes)} onChange={(event) => setRelay({ maxPayloadBytes: Number(event.target.value || 262144) })}/>
                                        </div>
                                    </div>
                                    <div className="grid gap-3">
                                        <SettingToggleCard
                                            title={<span className="text-sm font-medium text-slate-900">{relayCopy.e2e}</span>}
                                            checked={config.relay.endToEndEnvelopeRequired}
                                            onCheckedChange={(checked) => setRelay({ endToEndEnvelopeRequired: checked })}
                                            className="border-none bg-transparent p-0 shadow-none hover:bg-transparent"
                                        />
                                        <SettingToggleCard
                                            title={<span className="text-sm font-medium text-slate-900">{relayCopy.storeForward}</span>}
                                            checked={config.relay.storeAndForwardRequired}
                                            onCheckedChange={(checked) => setRelay({ storeAndForwardRequired: checked })}
                                            className="border-none bg-transparent p-0 shadow-none hover:bg-transparent"
                                        />
                                    </div>
                                    <div className="space-y-2 text-xs leading-5 text-slate-500">
                                        <div className="truncate">well-known: {relayStatusAdapter?.endpoints?.wellKnown || "—"}</div>
                                        <div className="truncate">mailbox: {relayStatusAdapter?.endpoints?.mailbox || "—"}</div>
                                        <div className="truncate">websocket: {relayStatusAdapter?.endpoints?.websocket || "—"}</div>
                                        {status.relay?.reasons?.length ? <div>{status.relay.reasons.join(" · ")}</div> : null}
                                    </div>
                                </div>
                            </div>
                        </div>
                    ) : null}
                </div>
            </ConfigCard>

            <ConfigCard
                title={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.thirdParty.title")}
                description={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.thirdParty.description")}
                variant="editor"
                bodyHeight="auto"
            >
                <div className="grid gap-3 xl:grid-cols-3">
                    {thirdPartyConnectionCards.map((card) => (
                        <div key={card.canonicalId} className="rounded-2xl border border-slate-200 bg-white p-4">
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-950">
                                        {card.title}
                                        <AdminHoverInfo content={`canonical id: ${card.canonicalId}`}>
                                            <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-slate-100 text-[10px] text-slate-500">i</span>
                                        </AdminHoverInfo>
                                    </div>
                                    <p className="mt-1 text-xs leading-5 text-slate-500">{card.purpose}</p>
                                </div>
                            </div>
                            <div className="mt-4 space-y-2 text-xs">
                                <div className="flex items-center justify-between gap-3">
                                    <span className="text-slate-500">{card.endpointLabel}</span>
                                    <Button type="button" size="sm" variant="outline" onClick={() => void copyText(card.endpoint, card.endpointLabel)}>
                                        {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopy")}
                                    </Button>
                                </div>
                                <div className="truncate rounded-xl bg-slate-50 px-3 py-2 font-mono text-slate-700">{card.endpoint}</div>
                                <div className="grid grid-cols-[88px_1fr] gap-2 text-slate-600">
                                    <span className="text-slate-400">{card.credentialLabel}</span>
                                    <span className="truncate font-mono">{card.credential}</span>
                                    <span className="text-slate-400">{card.modelLabel}</span>
                                    <span className="truncate font-mono">{card.model}</span>
                                </div>
                                <div className="flex justify-end">
                                    <Button type="button" size="sm" variant="ghost" onClick={() => void copyText(card.example, `${card.title} example`)}>
                                        {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.thirdParty.copyExample")}
                                    </Button>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </ConfigCard>

            <details className="rounded-3xl border border-slate-200 bg-slate-50/70 p-4">
                <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900">
                    {neighborCopy.advanced}
                    <span className="ml-3 text-xs font-normal text-slate-500">{neighborCopy.advancedHint}</span>
                </summary>
                <div className="mt-4 space-y-4">

            <ConfigCard title={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatTitle"} description={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatDescription"} variant="editor" bodyHeight="auto">
                <div className="grid gap-6 xl:grid-cols-[1fr_0.9fr]">
                    <div className="space-y-4">
                        <div className="flex flex-wrap items-center gap-2">
                            <Badge variant={config.enabled && config.openaiCompat.enabled ? "default" : "secondary"}>
                                {config.enabled && config.openaiCompat.enabled ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatReady") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatDisabled")}
                            </Badge>
                            <Badge variant={tokens.length ? "default" : "secondary"}>
                                {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatTokenCount", { count: tokens.length })}
                            </Badge>
                            <Badge variant={config.openaiCompat.adminRelayOnly ? "default" : "secondary"}>
                                {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatAdminRelayOnly")}
                            </Badge>
                        </div>

                        <div className="grid gap-3 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                            <div className="grid gap-2">
                                <Label htmlFor="openai-compat-base-url">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatBaseUrl")}</Label>
                                <div className="flex gap-2">
                                    <Input id="openai-compat-base-url" readOnly value={compatBaseUrl}/>
                                    <Button type="button" variant="outline" onClick={() => void copyText(compatBaseUrl, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatBaseUrl"))}>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopy")}</Button>
                                </div>
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="openai-compat-chat-url">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatChatUrl")}</Label>
                                <div className="flex gap-2">
                                    <Input id="openai-compat-chat-url" readOnly value={compatChatUrl}/>
                                    <Button type="button" variant="outline" onClick={() => void copyText(compatChatUrl, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatChatUrl"))}>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopy")}</Button>
                                </div>
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="openai-compat-models-url">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatModelsUrl")}</Label>
                                <div className="flex gap-2">
                                    <Input id="openai-compat-models-url" readOnly value={compatModelsUrl}/>
                                    <Button type="button" variant="outline" onClick={() => void copyText(compatModelsUrl, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatModelsUrl"))}>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopy")}</Button>
                                </div>
                            </div>
                            <div className="mt-2 border-t border-slate-200 pt-4">
                                <div className="mb-3 text-sm font-semibold text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.anthropicCompatTitle")}</div>
                                <div className="grid gap-3">
                                    <div className="grid gap-2">
                                        <Label htmlFor="anthropic-compat-base-url">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.anthropicCompatBaseUrl")}</Label>
                                        <div className="flex gap-2">
                                            <Input id="anthropic-compat-base-url" readOnly value={anthropicCompatBaseUrl}/>
                                            <Button type="button" variant="outline" onClick={() => void copyText(anthropicCompatBaseUrl, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.anthropicCompatBaseUrl"))}>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopy")}</Button>
                                        </div>
                                    </div>
                                    <div className="grid gap-2">
                                        <Label htmlFor="anthropic-compat-messages-url">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.anthropicCompatMessagesUrl")}</Label>
                                        <div className="flex gap-2">
                                            <Input id="anthropic-compat-messages-url" readOnly value={anthropicCompatMessagesUrl}/>
                                            <Button type="button" variant="outline" onClick={() => void copyText(anthropicCompatMessagesUrl, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.anthropicCompatMessagesUrl"))}>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopy")}</Button>
                                        </div>
                                    </div>
                                    <div className="grid gap-2">
                                        <Label htmlFor="anthropic-compat-models-url">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.anthropicCompatModelsUrl")}</Label>
                                        <div className="flex gap-2">
                                            <Input id="anthropic-compat-models-url" readOnly value={anthropicCompatModelsUrl}/>
                                            <Button type="button" variant="outline" onClick={() => void copyText(anthropicCompatModelsUrl, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.anthropicCompatModelsUrl"))}>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopy")}</Button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="openai-compat-model-aliases">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatModelAliases")}</Label>
                                <Input
                                    id="openai-compat-model-aliases"
                                    value={(config.openaiCompat.modelAliases || ["v8os"]).join(", ")}
                                    onChange={(event) => setOpenAICompat({
                                        modelAliases: event.target.value.split(",").map((item) => item.trim()).filter(Boolean).length
                                            ? event.target.value.split(",").map((item) => item.trim()).filter(Boolean)
                                            : ["v8os"],
                                    })}
                                />
                            </div>
                        </div>

                        <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                <div>
                                    <AdminHoverInfo content={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatApiKeysDescription")} panelClassName="text-xs leading-5">
                                        <span className="cursor-help text-sm font-semibold text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatApiKeys")}</span>
                                    </AdminHoverInfo>
                                </div>
                                <div className="flex gap-2">
                                    <Input className="w-48" value={tokenLabel} onChange={(event) => setTokenLabel(event.target.value)} placeholder={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatTokenLabelPlaceholder")}/>
                                    <Button type="button" onClick={() => void createCompatToken()} disabled={tokenBusy}>
                                        {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCreateToken")}
                                    </Button>
                                </div>
                            </div>
                            {tokens.length ? (<div className="space-y-2">
                                    {tokens.map((token) => (<div key={token.id} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3">
                                            <div className="flex flex-wrap items-center justify-between gap-2">
                                                <div className="min-w-0">
                                                    <div className="text-sm font-medium text-slate-900">{token.label || token.id}</div>
                                                    <div className="text-xs text-slate-500">{token.fingerprint || "—"}{token.createdAt ? ` · ${token.createdAt}` : ""}</div>
                                                </div>
                                                <div className="flex gap-2">
                                                    <Button type="button" size="sm" variant="outline" onClick={() => void copyText(token.token, "API Key")}>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCopyKey")}</Button>
                                                    <Button type="button" size="sm" variant="outline" onClick={() => void deleteCompatToken(token.id)} disabled={tokenBusy}>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatDeleteKey")}</Button>
                                                </div>
                                            </div>
                                            <Input className="mt-2 font-mono text-xs" readOnly value={token.token}/>
                                        </div>))}
                                </div>) : (<div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-sm text-slate-500">
                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatNoToken")}
                                </div>)}
                        </div>
                    </div>

                    <div className="space-y-4">
                        <div className="rounded-2xl border border-slate-200 bg-white p-4">
                            <div className="mb-3 flex items-center justify-between gap-3">
                                <AdminHoverInfo content={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.compatIngressDiagnosticsDescription")} panelClassName="text-xs leading-5">
                                    <span className="cursor-help text-sm font-semibold text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.compatIngressDiagnosticsTitle")}</span>
                                </AdminHoverInfo>
                                <Badge variant={status.pendingExternalTools?.waitingCount ? "default" : "secondary"}>
                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.compatIngressPendingTools", { count: status.pendingExternalTools?.waitingCount || 0 })}
                                </Badge>
                            </div>
                            {status.compatIngress?.recent?.length ? (<div className="space-y-2">
                                    {status.compatIngress.recent.slice(0, 3).map((item, index) => (<div key={`compat-ingress-${index}`} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge variant="outline">{String(item.protocol || "compat")}</Badge>
                                                <span>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.compatIngressPayloadTokens", { count: Number(item.payloadTokens || 0) })}</span>
                                                <span>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.compatIngressToolCount", { count: Number(item.clientToolCount || 0) })}</span>
                                            </div>
                                            <div className="mt-1 break-all text-slate-500">{String(item.rawRef || "—")}</div>
                                        </div>))}
                                </div>) : (<div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-3 py-4 text-xs text-slate-500">
                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.compatIngressNoRecent")}
                                </div>)}
                        </div>
                        <div className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-4">
                            <SettingToggleCard
                                title={
                                    <AdminHoverInfo content={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatEnableDescription")} panelClassName="text-xs leading-5">
                                        <span className="cursor-help text-sm font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatEnable")}</span>
                                    </AdminHoverInfo>
                                }
                                checked={config.openaiCompat.enabled}
                                onCheckedChange={(checked) => setOpenAICompat({ enabled: checked })}
                                className="border-none bg-transparent hover:bg-transparent p-0 shadow-none"
                            />
                            <SettingToggleCard
                                title={
                                    <AdminHoverInfo content={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatMainChainModeDescription")} panelClassName="text-xs leading-5">
                                        <span className="cursor-help text-sm font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatMainChainMode")}</span>
                                    </AdminHoverInfo>
                                }
                                checked={config.openaiCompat.v8MainChainModeEnabled}
                                onCheckedChange={(checked) => setOpenAICompatMainChainMode(checked)}
                                className="border-none bg-transparent hover:bg-transparent p-0 shadow-none"
                            />
                            {config.openaiCompat.v8MainChainModeEnabled ? (
                                <div className="grid gap-3 rounded-2xl border border-amber-200 bg-amber-50/70 p-3">
                                    <div className="text-xs font-semibold uppercase tracking-[0.16em] text-amber-800">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatAdvancedTitle")}</div>
                                    <SettingToggleCard
                                        title={
                                            <AdminHoverInfo content={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatWorkspaceHeadersDescription")} panelClassName="text-xs leading-5">
                                                <span className="cursor-help text-sm font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatWorkspaceHeaders")}</span>
                                            </AdminHoverInfo>
                                        }
                                        checked={config.openaiCompat.allowWorkspaceHeaders}
                                        onCheckedChange={(checked) => setOpenAICompat({ allowWorkspaceHeaders: checked })}
                                        className="border-none bg-transparent hover:bg-transparent p-0 shadow-none"
                                    />
                                    <SettingToggleCard
                                        title={
                                            <AdminHoverInfo content={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatRawWorkspacePathDescription")} panelClassName="text-xs leading-5">
                                                <span className="cursor-help text-sm font-medium text-amber-950">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatRawWorkspacePath")}</span>
                                            </AdminHoverInfo>
                                        }
                                        checked={config.openaiCompat.allowRawWorkspacePath}
                                        onCheckedChange={(checked) => setOpenAICompat({ allowRawWorkspacePath: checked })}
                                        className="border-none bg-transparent hover:bg-transparent p-0 shadow-none"
                                    />
                                    <div className="grid gap-4 sm:grid-cols-2">
                                        <div className="space-y-2">
                                            <Label htmlFor="openai-compat-max-tools">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatMaxExternalTools")}</Label>
                                            <Input id="openai-compat-max-tools" type="number" min={0} value={String(config.openaiCompat.maxExternalTools)} onChange={(event) => setOpenAICompat({ maxExternalTools: Number(event.target.value || 0) })}/>
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="openai-compat-scope-mode">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatScopeMode")}</Label>
                                            <Select value={config.openaiCompat.defaultScopeMode} onValueChange={(value) => setOpenAICompat({ defaultScopeMode: value })}>
                                                <SelectTrigger id="openai-compat-scope-mode">
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {!hasCurrentScopeModeOption && config.openaiCompat.defaultScopeMode ? <SelectItem value={config.openaiCompat.defaultScopeMode}>{resolveAdminLabel(t, "networkScopeMode", config.openaiCompat.defaultScopeMode)}</SelectItem> : null}
                                                    {scopeModeOptions.map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                </div>
                            ) : null}
                            <Button type="button" onClick={() => {
                        setConfig((prev) => ({
                            ...prev,
                            enabled: true,
                            openaiCompat: {
                                ...prev.openaiCompat,
                                enabled: true,
                                adminRelayOnly: true,
                                v8MainChainModeEnabled: false,
                                allowWorkspaceHeaders: false,
                                allowRawWorkspacePath: false,
                                defaultScopeMode: "explicit",
                            },
                        }));
                    }}>
                                {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatQuickEnable")}
                            </Button>
                        </div>

                        <div className="space-y-2">
                            <Label>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatCurlExample")}</Label>
                            <pre className="overflow-auto rounded-2xl border border-slate-200 bg-slate-950 p-4 text-xs leading-5 text-slate-100">{curlExample}</pre>
                        </div>
                        <div className="space-y-2">
                            <Label>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatSdkExample")}</Label>
                            <pre className="overflow-auto rounded-2xl border border-slate-200 bg-slate-950 p-4 text-xs leading-5 text-slate-100">{sdkExample}</pre>
                        </div>
                        <div className="space-y-2">
                            <Label>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.anthropicCompatCurlExample")}</Label>
                            <pre className="overflow-auto rounded-2xl border border-slate-200 bg-slate-950 p-4 text-xs leading-5 text-slate-100">{anthropicCurlExample}</pre>
                        </div>
                        <div className="space-y-2">
                            <Label>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.anthropicCompatClaudeCodeExample")}</Label>
                            <pre className="overflow-auto rounded-2xl border border-slate-200 bg-slate-950 p-4 text-xs leading-5 text-slate-100">{claudeCodeExample}</pre>
                        </div>
                        <div className="flex justify-end">
                            <Button onClick={() => void saveConfig()} disabled={savingConfig}>
                                {savingConfig ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kc225e8a3") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kc1f71c38")}
                            </Button>
                        </div>
                    </div>
                </div>
            </ConfigCard>

            {/* 旧 Network Links 的手工 peer / mesh / raw diagnostic UI 不再作为 Admin 入口展示；底层 API 保留给兼容和诊断。 */}
            {false ? (
                <>
            <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
                <ConfigCard title={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.networkNodeTitle"} description={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.networkNodeDescription"} variant="editor" bodyHeight="auto">
                    <div className="grid gap-5 lg:grid-cols-2">
                        <div className="space-y-4">
                            <SettingToggleCard
                                title={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k0fcd82fa")}
                                description={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k51ccd94e")}
                                checked={config.enabled}
                                onCheckedChange={(checked) => setConfig((prev) => ({ ...prev, enabled: checked }))}
                                className="rounded-2xl border border-slate-200 px-4 py-3 bg-transparent hover:bg-transparent shadow-none"
                            />

                            <div className="space-y-2">
                                <Label htmlFor="network-node-name">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k8c78324d")}</Label>
                                <Input id="network-node-name" value={config.node.displayName} onChange={(event) => setNode({ displayName: event.target.value })}/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="network-peer-id">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k23ad0ab3")}</Label>
                                <Input id="network-peer-id" value={config.node.peerId} onChange={(event) => setNode({ peerId: event.target.value })} placeholder="peer_xxx"/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="network-base-url">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kd575b517")}</Label>
                                <Input id="network-base-url" value={config.node.advertisedBaseUrl} onChange={(event) => setNode({ advertisedBaseUrl: event.target.value })} placeholder="http://127.0.0.1:9530"/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="network-ws-url">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kb0019af3")}</Label>
                                <Input id="network-ws-url" value={config.node.advertisedWsUrl} onChange={(event) => setNode({ advertisedWsUrl: event.target.value })} placeholder="ws://127.0.0.1:9530/v1/network-supervisor/peer/ws"/>
                            </div>
                        </div>

                        <div className="space-y-4">
                            <SettingToggleCard
                                title={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kf6ca0e75")}
                                description={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k2288dd56")}
                                checked={config.discovery.lanEnabled}
                                onCheckedChange={(checked) => setDiscovery({ lanEnabled: checked })}
                                className="rounded-2xl border border-slate-200 px-4 py-3 bg-transparent hover:bg-transparent shadow-none"
                            />
                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="network-group">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k653864a4")}</Label>
                                    <Input id="network-group" value={config.discovery.multicastGroup} onChange={(event) => setDiscovery({ multicastGroup: event.target.value })}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="network-port">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k66d313cc")}</Label>
                                    <Input id="network-port" type="number" value={String(config.discovery.multicastPort)} onChange={(event) => setDiscovery({ multicastPort: Number(event.target.value || 0) })}/>
                                </div>
                            </div>
                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="network-announce">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k82e446f5")}</Label>
                                    <Input id="network-announce" type="number" value={String(config.discovery.announceIntervalSeconds)} onChange={(event) => setDiscovery({ announceIntervalSeconds: Number(event.target.value || 0) })}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="network-expiry">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k2898c0c3")}</Label>
                                    <Input id="network-expiry" type="number" value={String(config.discovery.peerExpirySeconds)} onChange={(event) => setDiscovery({ peerExpirySeconds: Number(event.target.value || 0) })}/>
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="network-bootstrap">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k15b438dc")}</Label>
                                <Textarea id="network-bootstrap" rows={4} value={joinLines(config.discovery.wanBootstrapPeers)} onChange={(event) => setDiscovery({ wanBootstrapPeers: lines(event.target.value) })} placeholder={"https://peer-a.example.com\nhttps://peer-b.example.com"}/>
                            </div>
                        </div>
                    </div>

                    <div className="grid gap-5 border-t border-slate-100 pt-5 lg:grid-cols-3">
                        <div className="space-y-2">
                            <Label htmlFor="network-enrollment">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kf830fbd8")}</Label>
                            <Select value={config.trust.enrollmentMode} onValueChange={(value: "manual" | "open") => setTrust({ enrollmentMode: value })}>
                                <SelectTrigger id="network-enrollment">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {!hasCurrentEnrollmentModeOption && config.trust.enrollmentMode ? <SelectItem value={config.trust.enrollmentMode}>{resolveAdminLabel(t, "networkEnrollmentMode", config.trust.enrollmentMode)}</SelectItem> : null}
                                    {enrollmentModeOptions.map((option) => <SelectItem key={option.value} value={option.value}>{t(option.labelKey)}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="network-allowed-scopes">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k0e27e25f")}</Label>
                            <Input id="network-allowed-scopes" value={joinCsv(config.trust.allowedScopes)} onChange={(event) => setTrust({ allowedScopes: csv(event.target.value) })} placeholder="global, workspace, memory"/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="network-delegation-timeout">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k10d0be67")}</Label>
                            <Input id="network-delegation-timeout" type="number" value={String(config.delegation.defaultTimeoutSeconds)} onChange={(event) => setDelegation({ defaultTimeoutSeconds: Number(event.target.value || 0) })}/>
                        </div>
                    </div>

                    <div className="grid gap-5 lg:grid-cols-2">
                        <SettingToggleCard
                            title={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kc4940ac2")}
                            description={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kad9d78ed")}
                            checked={config.wake.enabled}
                            onCheckedChange={(checked) => setWake({ enabled: checked })}
                            className="rounded-2xl border border-slate-200 px-4 py-3 bg-transparent hover:bg-transparent shadow-none"
                        />
                        <SettingToggleCard
                            title={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k5c9b4ab7")}
                            description={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k17b60019")}
                            checked={config.delegation.enabled}
                            onCheckedChange={(checked) => setDelegation({ enabled: checked })}
                            className="rounded-2xl border border-slate-200 px-4 py-3 bg-transparent hover:bg-transparent shadow-none"
                        />
                    </div>

                    <div className="grid gap-4 lg:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="network-ack-timeout">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k5294b489")}</Label>
                            <Input id="network-ack-timeout" type="number" value={String(config.wake.ackTimeoutSeconds)} onChange={(event) => setWake({ ackTimeoutSeconds: Number(event.target.value || 0) })}/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="network-max-concurrency">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k45c23bdd")}</Label>
                            <Input id="network-max-concurrency" type="number" value={String(config.delegation.maxConcurrent)} onChange={(event) => setDelegation({ maxConcurrent: Number(event.target.value || 0) })}/>
                        </div>
                    </div>

                    <div className="flex justify-end">
                        <Button onClick={() => void saveConfig()} disabled={savingConfig}>
                            {savingConfig ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kc225e8a3") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kc1f71c38")}
                        </Button>
                    </div>
                </ConfigCard>

                <ConfigCard title={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kae425cff"} description={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k9687b4bd"} variant="list" bodyHeight="auto">
                    {loading ? (<div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
                            {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k28efa74d")}
                        </div>) : (<div className="space-y-4 text-sm text-slate-700">
                            <div className="flex flex-wrap gap-2">
                                <Badge variant={status.enabled ? "default" : "secondary"}>{status.enabled ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kdb6c0cc1") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k12b31ba6")}</Badge>
                                <Badge variant={status.started ? "default" : "secondary"}>{status.started ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k8d8295f0") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ka5a2a276")}</Badge>
                                <Badge variant={availability.available ? "default" : "secondary"}>{availability.available ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k4398bbbc") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ke95066ef")}</Badge>
                            </div>
                            <div className="grid gap-3 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                <div><span className="font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k4ec0ec8d")}</span> {status.node.peerId || "—"}</div>
                                <div><span className="font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k7c310026")}</span> {status.node.displayName || "—"}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kf0354341")}</span> {status.node.advertisedBaseUrl || "—"}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k0555b63c")}</span> {status.node.advertisedWsUrl || "—"}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k70e56416")}</span> {status.node.publicKeyFingerprint || "—"}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ka2aeb345")}</span> {status.node.localPeerTokenFingerprint || "—"}</div>
                            </div>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                                    <div className="text-xs text-slate-500">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ka700a601")}</div>
                                    <div className="mt-1 text-lg font-semibold text-slate-900">{status.discovery.discoveredPeerCount}</div>
                                </div>
                                <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                                    <div className="text-xs text-slate-500">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ke9012800")}</div>
                                    <div className="mt-1 text-lg font-semibold text-slate-900">{status.discovery.onlinePeerCount}</div>
                                </div>
                                <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                                    <div className="text-xs text-slate-500">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k51d9bdf8")}</div>
                                    <div className="mt-1 text-lg font-semibold text-slate-900">{status.delegation.activeInbound}</div>
                                </div>
                                <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                                    <div className="text-xs text-slate-500">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k7efaaaeb")}</div>
                                    <div className="mt-1 text-lg font-semibold text-slate-900">{status.delegation.trackedCount}</div>
                                </div>
                            </div>
                            {availability.reasons.length ? (<div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-900">
                                    <div className="font-medium">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ke477157c")}</div>
                                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-5">
                                        {availability.reasons.map((reason) => (<li key={reason}>{availabilityReason(reason)}</li>))}
                                    </ul>
                                </div>) : (<div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm text-emerald-900">
                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k0f3a887d")}
                                </div>)}
                        </div>)}
                </ConfigCard>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
                <ConfigCard title={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ke26394fd"} description={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k341139d5"} variant="list" bodyHeight={420} bodyScroll="auto">
                    <div className="space-y-6">
                        <section className="space-y-3">
                            <div className="flex items-center justify-between">
                                <h3 className="text-sm font-semibold text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.meshCandidatesTitle")}</h3>
                                <Badge variant="outline">{peers.meshCandidates.length}</Badge>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-xs leading-5 text-slate-600">
                                {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.meshCandidatesPolicy")}
                            </div>
                            {peers.meshCandidates.length ? (<div className="space-y-3">
                                    {peers.meshCandidates.map((candidate) => (
                                        <div key={candidate.id || candidate.peerBaseUrl || candidate.hostName} className="rounded-2xl border border-slate-200 bg-white px-4 py-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="font-medium text-slate-900">{candidate.hostName || candidate.dnsName || candidate.ips?.[0] || t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.meshUnknownPeer")}</div>
                                                <Badge variant="secondary">{candidate.source || "mesh"}</Badge>
                                                <Badge variant={candidate.online ? "default" : "secondary"}>{candidate.online ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kd4abefe4") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kfc2ade75")}</Badge>
                                                {candidate.deviceClass === "phone" ? <Badge variant="outline">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.phonePeerCandidateBadge")}</Badge> : null}
                                            </div>
                                            <div className="mt-2 space-y-1 text-xs text-slate-500">
                                                <div className="break-all">{candidate.peerBaseUrl || candidate.dnsName || candidate.ips?.join(" · ") || "—"}</div>
                                                {candidate.os ? <div>{candidate.os}</div> : null}
                                                {candidate.deviceClass === "phone" || candidate.requiresApproval ? <div className="text-amber-700">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.phonePeerApprovalHint")}</div> : null}
                                            </div>
                                            <div className="mt-4 flex flex-wrap gap-2">
                                                <Button variant="outline" size="sm" onClick={() => fillPeerFormFromMeshCandidate(candidate)}>
                                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.meshUseCandidate")}
                                                </Button>
                                            </div>
                                        </div>
                                    ))}
                                </div>) : (<div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-6 text-sm text-slate-500">
                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.meshNoCandidates")}
                                </div>)}
                        </section>

                        <section className="space-y-3">
                            <div className="flex items-center justify-between">
                                <h3 className="text-sm font-semibold text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kb07e9c89")}</h3>
                                <Badge variant="outline">{peers.discoveredItems.length}</Badge>
                            </div>
                            {peers.discoveredItems.length ? (<div className="space-y-3">
                                    {peers.discoveredItems.map((peer) => (<div key={`discovered-${peer.peerId}`} className="rounded-2xl border border-slate-200 bg-white px-4 py-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="font-medium text-slate-900">{peer.displayName || peer.peerId}</div>
                                                <Badge variant="outline">{peer.peerId}</Badge>
                                                <Badge variant={peer.online ? "default" : "secondary"}>{peer.online ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kd4abefe4") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kfc2ade75")}</Badge>
                                                {peer.source ? <Badge variant="secondary">{resolveAdminLabel(t, "networkPeerSource", peer.source)}</Badge> : null}
                                            </div>
                                            <div className="mt-2 space-y-1 text-xs text-slate-500">
                                                <div className="break-all">{peer.baseUrl || "—"}</div>
                                                {peer.address ? <div>{peer.address}</div> : null}
                                                {peer.lastSeenAt ? <div>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k917aca51")}{peer.lastSeenAt}</div> : null}
                                            </div>
                                            <div className="mt-4 flex flex-wrap gap-2">
                                                <Button variant="outline" size="sm" onClick={() => fillPeerForm(peer)}>
                                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kbb24ad74")}
                                                </Button>
                                                <Button variant="outline" size="sm" onClick={() => chooseDiagPeer(peer.peerId)}>
                                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k07d17f9d")}
                                                </Button>
                                                <Button variant="outline" size="sm" disabled={running === "challenge"} onClick={() => void runDiagnostic("challenge", peer.peerId)}>
                                                    {running === "challenge" && diag.peerId === peer.peerId ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ka9b6c6f6") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k2697884b")}
                                                </Button>
                                            </div>
                                        </div>))}
                                </div>) : (<div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-6 text-sm text-slate-500">
                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kd961dc13")}
                                </div>)}
                        </section>

                        <section className="space-y-3">
                            <div className="flex items-center justify-between">
                                <h3 className="text-sm font-semibold text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kec3b469e")}</h3>
                                <Badge variant="outline">{peers.trustedItems.length}</Badge>
                            </div>
                            {peers.trustedItems.length ? (<div className="space-y-3">
                                    {peers.trustedItems.map((peer) => (<div key={`trusted-${peer.peerId}`} className="rounded-2xl border border-slate-200 bg-white px-4 py-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="font-medium text-slate-900">{peer.displayName || peer.peerId}</div>
                                                <Badge variant="outline">{peer.peerId}</Badge>
                                                <Badge variant="default">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k7ce56639")}</Badge>
                                                <Badge variant={peer.online ? "default" : "secondary"}>{peer.online ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kd4abefe4") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kfc2ade75")}</Badge>
                                            </div>
                                            <div className="mt-2 space-y-1 text-xs text-slate-500">
                                                <div className="break-all">{peer.baseUrl || "—"}</div>
                                                <div className="break-all">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kcc6543c2")}{peer.tokenFingerprint || "—"}</div>
                                                <div>{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k4fdf4201")}{peer.allowedScopes.length ? joinCsv(peer.allowedScopes) : "—"}</div>
                                            </div>
                                            <div className="mt-4 flex flex-wrap gap-2">
                                                <Button variant="outline" size="sm" onClick={() => fillPeerForm(peer)}>
                                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k75997619")}
                                                </Button>
                                                <Button variant="outline" size="sm" onClick={() => chooseDiagPeer(peer.peerId)}>
                                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k07d17f9d")}
                                                </Button>
                                                <Button variant="outline" size="sm" onClick={() => void deletePeer(peer.peerId)}>
                                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k626f35dc")}
                                                </Button>
                                            </div>
                                        </div>))}
                                </div>) : (<div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-6 text-sm text-slate-500">
                                    {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k01f8b04d")}
                                </div>)}
                        </section>
                    </div>
                </ConfigCard>

                <ConfigCard title={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k82be1826"} description={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k0a824403"} variant="editor" bodyHeight="auto">
                    <div className="space-y-4">
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2">
                                <Label htmlFor="peer-form-id">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k23ad0ab3")}</Label>
                                <Input id="peer-form-id" value={peerForm.peerId} onChange={(event) => setPeerForm((prev) => ({ ...prev, peerId: event.target.value }))}/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="peer-form-name">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k71cd3126")}</Label>
                                <Input id="peer-form-name" value={peerForm.displayName} onChange={(event) => setPeerForm((prev) => ({ ...prev, displayName: event.target.value }))}/>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-base">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kd575b517")}</Label>
                            <Input id="peer-form-base" value={peerForm.baseUrl} onChange={(event) => setPeerForm((prev) => ({ ...prev, baseUrl: event.target.value }))}/>
                        </div>
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2">
                                <Label htmlFor="peer-form-transport">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.meshTransportProfile")}</Label>
                                <Input id="peer-form-transport" value={peerForm.transportProfileId} onChange={(event) => setPeerForm((prev) => ({ ...prev, transportProfileId: event.target.value }))}/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="peer-form-peer-base">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.meshPeerBaseUrl")}</Label>
                                <Input id="peer-form-peer-base" value={peerForm.peerBaseUrl} onChange={(event) => setPeerForm((prev) => ({ ...prev, peerBaseUrl: event.target.value }))}/>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-ws">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kb0019af3")}</Label>
                            <Input id="peer-form-ws" value={peerForm.wsUrl} onChange={(event) => setPeerForm((prev) => ({ ...prev, wsUrl: event.target.value }))}/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-public-key">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.keff9b55f")}</Label>
                            <Textarea id="peer-form-public-key" rows={4} value={peerForm.publicKey} onChange={(event) => setPeerForm((prev) => ({ ...prev, publicKey: event.target.value }))}/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-token">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ke6a799cc")}</Label>
                            <Input id="peer-form-token" value={peerForm.peerToken} onChange={(event) => setPeerForm((prev) => ({ ...prev, peerToken: event.target.value }))} placeholder={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k48afd459")}/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-scopes">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k84c3de2a")}</Label>
                            <Input id="peer-form-scopes" value={peerForm.allowedScopes} onChange={(event) => setPeerForm((prev) => ({ ...prev, allowedScopes: event.target.value }))} placeholder="global, workspace, memory"/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-workspaces">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k97eb1914")}</Label>
                            <Textarea id="peer-form-workspaces" rows={4} value={peerForm.allowedWorkspaces} onChange={(event) => setPeerForm((prev) => ({ ...prev, allowedWorkspaces: event.target.value }))} placeholder={"workspace-a\nworkspace-b"}/>
                        </div>
                        <div className="flex items-center justify-between gap-3">
                            <Button variant="outline" onClick={() => setPeerForm(EMPTY_PEER_FORM)}>
                                {t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k126cbd18")}
                            </Button>
                            <Button onClick={() => void savePeer()} disabled={savingPeer}>
                                {savingPeer ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kc225e8a3") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k944dbbe1")}
                            </Button>
                        </div>
                    </div>
                </ConfigCard>
            </div>

            <ConfigCard title={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k0c8e0e83"} description={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ke100aaa0"} variant="editor" bodyHeight="auto">
                <div className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="diag-peer">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kfc5fee2d")}</Label>
                            <Input id="diag-peer" value={diag.peerId} onChange={(event) => setDiag((prev) => ({ ...prev, peerId: event.target.value }))} placeholder="peer_xxx"/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="diag-note">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k96d8e7ec")}</Label>
                            <Textarea id="diag-note" rows={3} value={diag.note} onChange={(event) => setDiag((prev) => ({ ...prev, note: event.target.value }))} placeholder={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k7af11461")}/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="diag-task">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k161c7567")}</Label>
                            <Textarea id="diag-task" rows={6} value={diag.task} onChange={(event) => setDiag((prev) => ({ ...prev, task: event.target.value }))} placeholder={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kb9ee78e3")}/>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Button variant="outline" disabled={running !== ""} onClick={() => void runDiagnostic("challenge")}>
                                {running === "challenge" ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ka9b6c6f6") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k2697884b")}
                            </Button>
                            <Button variant="outline" disabled={running !== ""} onClick={() => void runDiagnostic("wake")}>
                                {running === "wake" ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.ka9b6c6f6") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k46ae5f45")}
                            </Button>
                            <Button disabled={running !== ""} onClick={() => void runDiagnostic("delegate")}>
                                {running === "delegate" ? t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k6bd09d35") : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kad52918d")}
                            </Button>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="diag-result">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k271deed6")}</Label>
                        <Textarea id="diag-result" rows={18} value={diag.result} onChange={(event) => setDiag((prev) => ({ ...prev, result: event.target.value }))} placeholder={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k4b7a780f")}/>
                    </div>
                </div>
            </ConfigCard>
                </>
            ) : null}
                </div>
            </details>
            <DocumentationGuideDialog
                open={guideOpen}
                onOpenChange={setGuideOpen}
                title={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.guideTitle")}
                description={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.guideDescription")}
                content={docContent}
            />
        </AdminPageShell>);
}
