"use client";
import * as React from "react";
import Link from "next/link";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
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
};
type PeersPayload = {
    items: PeerItem[];
    trustedItems: PeerItem[];
    discoveredItems: PeerItem[];
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
    openaiCompat: {
        enabled: boolean;
        modelAliases: string[];
        adminRelayOnly: boolean;
        allowWorkspaceHeaders: boolean;
        allowRawWorkspacePath: boolean;
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
};
type DiagState = {
    peerId: string;
    note: string;
    task: string;
    result: string;
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
    openaiCompat: {
        enabled: false,
        modelAliases: ["v8os"],
        adminRelayOnly: true,
        allowWorkspaceHeaders: true,
        allowRawWorkspacePath: false,
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
        allowWorkspaceHeaders: true,
        allowRawWorkspacePath: false,
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
const EMPTY_PEERS: PeersPayload = { items: [], trustedItems: [], discoveredItems: [] };
const EMPTY_PEER_FORM: PeerForm = { peerId: "", displayName: "", baseUrl: "", wsUrl: "", publicKey: "", allowedScopes: "", allowedWorkspaces: "", peerToken: "" };
const EMPTY_DIAG: DiagState = { peerId: "", note: "", task: "", result: "" };
const csv = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
const lines = (value: string) => value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
const joinCsv = (value?: string[]) => Array.isArray(value) ? value.join(", ") : "";
const joinLines = (value?: string[]) => Array.isArray(value) ? value.join("\n") : "";
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
        openaiCompat: { ...DEFAULT_CONFIG.openaiCompat, ...(payload.openaiCompat || {}) },
    };
}
function normalizeStatus(value: unknown): RuntimeStatus {
    const payload = (value && typeof value === "object") ? value as Partial<RuntimeStatus> : {};
    const openaiCompat = (payload.openaiCompat || {}) as NonNullable<RuntimeStatus["openaiCompat"]>;
    const anthropicCompat = (payload.anthropicCompat || {}) as NonNullable<RuntimeStatus["anthropicCompat"]>;
    const compatIngress = (payload.compatIngress || {}) as NonNullable<RuntimeStatus["compatIngress"]>;
    const pendingExternalTools = (payload.pendingExternalTools || {}) as NonNullable<RuntimeStatus["pendingExternalTools"]>;
    return {
        ...EMPTY_STATUS,
        ...payload,
        node: { ...EMPTY_STATUS.node, ...(payload.node || {}) },
        discovery: { ...EMPTY_STATUS.discovery, ...(payload.discovery || {}) },
        delegation: { ...EMPTY_STATUS.delegation, ...(payload.delegation || {}) },
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
            allowWorkspaceHeaders: openaiCompat.allowWorkspaceHeaders !== false,
            allowRawWorkspacePath: Boolean(openaiCompat.allowRawWorkspacePath),
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
    };
}
export function NetworkSupervisorRuntimeWorkbench({ bridgeDiagnostics }: NetworkSupervisorRuntimeWorkbenchProps) {
    const t = useT();
    const { locale } = useLocale();
    const { toast } = useToast();
    const [config, setConfig] = React.useState<RuntimeConfig>(DEFAULT_CONFIG);
    const [status, setStatus] = React.useState<RuntimeStatus>(EMPTY_STATUS);
    const [peers, setPeers] = React.useState<PeersPayload>(EMPTY_PEERS);
    const [peerForm, setPeerForm] = React.useState<PeerForm>(EMPTY_PEER_FORM);
    const [diag, setDiag] = React.useState<DiagState>(EMPTY_DIAG);
    const [loading, setLoading] = React.useState(true);
    const [loadError, setLoadError] = React.useState<string | null>(null);
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
    const scopeModeOptions = getAdminOptions("networkScopeMode");
    const enrollmentModeOptions = getAdminOptions("networkEnrollmentMode");
    const hasCurrentScopeModeOption = scopeModeOptions.some((option) => option.value === config.openaiCompat.defaultScopeMode);
    const hasCurrentEnrollmentModeOption = enrollmentModeOptions.some((option) => option.value === config.trust.enrollmentMode);
    const curlExample = `curl ${compatChatUrl} \\
  -H "Authorization: Bearer ${primaryToken || "<API_KEY>"}" \\
  -H "Content-Type: application/json" \\
  -d "{\\"model\\":\\"${primaryModelAlias}\\",\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"ping\\"}]}"`;
    const sdkExample = `from openai import OpenAI

client = OpenAI(
    base_url="${compatBaseUrl}",
    api_key="${primaryToken || "<API_KEY>"}",
)

response = client.chat.completions.create(
    model="${primaryModelAlias}",
    messages=[{"role": "user", "content": "ping"}],
)`;
    const anthropicCurlExample = `curl ${anthropicCompatMessagesUrl} \\
  -H "x-api-key: ${primaryToken || "<API_KEY>"}" \\
  -H "anthropic-version: 2023-06-01" \\
  -H "Content-Type: application/json" \\
  -d "{\\"model\\":\\"${primaryModelAlias}\\",\\"max_tokens\\":256,\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"ping\\"}]}"`;
    const claudeCodeExample = `ANTHROPIC_BASE_URL=${anthropicCompatBaseUrl}
ANTHROPIC_AUTH_TOKEN=${primaryToken || "<API_KEY>"}
ANTHROPIC_MODEL=${primaryModelAlias}`;
    const portNotices = bridgeDiagnostics?.notices || [];
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
    const loadAll = React.useCallback(async () => {
        setLoading(true);
        setLoadError(null);
        try {
            const [configRes, statusRes, peerRes, tokenRes] = await Promise.all([
                fetch("/api/config-registry/network-supervisor-runtime", { cache: "no-store" }),
                fetch("/api/network-supervisor/status", { cache: "no-store" }),
                fetch("/api/network-supervisor/peers", { cache: "no-store" }),
                fetch("/api/network-supervisor/openai/tokens", { cache: "no-store" }),
            ]);
            const [configData, statusData, peerData, tokenData] = await Promise.all([
                configRes.json().catch(() => ({})),
                statusRes.json().catch(() => ({})),
                peerRes.json().catch(() => ({})),
                tokenRes.json().catch(() => ({})),
            ]);
            if (!configRes.ok)
                throw new Error(detail(configData, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k8d2fb12f")));
            if (!statusRes.ok)
                throw new Error(detail(statusData, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k5dcce62e")));
            if (!peerRes.ok)
                throw new Error(detail(peerData, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k66ef1038")));
            if (!tokenRes.ok)
                throw new Error(detail(tokenData, t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatTokensReadFailed")));
            setConfig(mergeConfig((configData as {
                data?: Partial<RuntimeConfig>;
            }).data));
            setStatus(normalizeStatus(statusData));
            setPeers(normalizePeers(peerData));
            setTokens(Array.isArray((tokenData as { items?: OpenAICompatToken[] }).items) ? (tokenData as { items: OpenAICompatToken[] }).items : []);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k105089b2");
            setLoadError(message);
            toast({ variant: "destructive", title: t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k65ed1d75"), description: message });
        }
        finally {
            setLoading(false);
        }
    }, [t, toast]);
    React.useEffect(() => {
        void loadAll();
    }, [loadAll]);
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
    const setOpenAICompat = React.useCallback((patch: Partial<RuntimeConfig["openaiCompat"]>) => {
        setConfig((prev) => ({ ...prev, openaiCompat: { ...prev.openaiCompat, ...patch } }));
    }, []);
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
        });
    }, []);
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
                            <div className="flex items-center justify-between gap-4">
                                <div>
                                    <AdminHoverInfo content={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatEnableDescription")} panelClassName="text-xs leading-5">
                                        <span className="cursor-help text-sm font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatEnable")}</span>
                                    </AdminHoverInfo>
                                </div>
                                <Switch checked={config.openaiCompat.enabled} onCheckedChange={(checked) => setOpenAICompat({ enabled: checked })} aria-label="openai-compat-enabled"/>
                            </div>
                            <div className="flex items-center justify-between gap-4">
                                <div>
                                    <AdminHoverInfo content={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatWorkspaceHeadersDescription")} panelClassName="text-xs leading-5">
                                        <span className="cursor-help text-sm font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatWorkspaceHeaders")}</span>
                                    </AdminHoverInfo>
                                </div>
                                <Switch checked={config.openaiCompat.allowWorkspaceHeaders} onCheckedChange={(checked) => setOpenAICompat({ allowWorkspaceHeaders: checked })} aria-label="openai-compat-workspace-headers"/>
                            </div>
                            <div className="flex items-center justify-between gap-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-3">
                                <div>
                                    <AdminHoverInfo content={t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatRawWorkspacePathDescription")} panelClassName="text-xs leading-5">
                                        <span className="cursor-help text-sm font-medium text-amber-950">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.openaiCompatRawWorkspacePath")}</span>
                                    </AdminHoverInfo>
                                </div>
                                <Switch checked={config.openaiCompat.allowRawWorkspacePath} onCheckedChange={(checked) => setOpenAICompat({ allowRawWorkspacePath: checked })} aria-label="openai-compat-raw-workspace-path"/>
                            </div>
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
                            <Button type="button" onClick={() => {
                        setConfig((prev) => ({ ...prev, enabled: true, openaiCompat: { ...prev.openaiCompat, enabled: true, adminRelayOnly: true } }));
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

            <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
                <ConfigCard title={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.networkNodeTitle"} description={"components.network.supervisor.NetworkSupervisorRuntimeWorkbench.networkNodeDescription"} variant="editor" bodyHeight="auto">
                    <div className="grid gap-5 lg:grid-cols-2">
                        <div className="space-y-4">
                            <div className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                                <div className="space-y-1">
                                    <div className="text-sm font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k0fcd82fa")}</div>
                                    <div className="text-xs text-slate-500">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k51ccd94e")}</div>
                                </div>
                                <Switch checked={config.enabled} onCheckedChange={(checked) => setConfig((prev) => ({ ...prev, enabled: checked }))} aria-label="network-supervisor-enabled"/>
                            </div>

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
                            <div className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                                <div className="space-y-1">
                                    <div className="text-sm font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kf6ca0e75")}</div>
                                    <div className="text-xs text-slate-500">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k2288dd56")}</div>
                                </div>
                                <Switch checked={config.discovery.lanEnabled} onCheckedChange={(checked) => setDiscovery({ lanEnabled: checked })} aria-label="network-discovery-enabled"/>
                            </div>
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
                        <div className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kc4940ac2")}</div>
                                <div className="text-xs text-slate-500">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.kad9d78ed")}</div>
                            </div>
                            <Switch checked={config.wake.enabled} onCheckedChange={(checked) => setWake({ enabled: checked })} aria-label="network-wake-enabled"/>
                        </div>
                        <div className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k5c9b4ab7")}</div>
                                <div className="text-xs text-slate-500">{t("components.network.supervisor.NetworkSupervisorRuntimeWorkbench.k17b60019")}</div>
                            </div>
                            <Switch checked={config.delegation.enabled} onCheckedChange={(checked) => setDelegation({ enabled: checked })} aria-label="network-delegation-enabled"/>
                        </div>
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
        </AdminPageShell>);
}
