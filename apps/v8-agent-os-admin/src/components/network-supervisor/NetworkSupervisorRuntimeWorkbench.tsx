"use client";

import * as React from "react";
import Link from "next/link";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { lt } from "@/lib/locale";
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
};

type Availability = { available: boolean; reasons: string[] };

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
    delegationAvailability: Availability;
    toolAvailability?: { delegate_network_task?: Availability };
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
    if (!value || typeof value !== "object") return fallback;
    const payload = value as Record<string, unknown>;
    return String(payload.detail || payload.error || fallback);
};
const sourceLabel = (value: string) => {
    switch (String(value || "").trim().toLowerCase()) {
        case "lan":
            return lt("局域网发现", "LAN");
        case "trusted":
            return lt("已信任", "Trusted");
        case "bootstrap":
            return lt("广域网引导", "Bootstrap");
        default:
            return value || "—";
    }
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
    };
}

function normalizeStatus(value: unknown): RuntimeStatus {
    const payload = (value && typeof value === "object") ? value as Partial<RuntimeStatus> : {};
    return {
        ...EMPTY_STATUS,
        ...payload,
        node: { ...EMPTY_STATUS.node, ...(payload.node || {}) },
        discovery: { ...EMPTY_STATUS.discovery, ...(payload.discovery || {}) },
        delegation: { ...EMPTY_STATUS.delegation, ...(payload.delegation || {}) },
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
    const [running, setRunning] = React.useState<"" | "challenge" | "wake" | "delegate">("");

    const docsUrl = locale === "zh-CN"
        ? "https://github.com/justForever17/v8-agent-os/blob/main/docs/NETWORK_SUPERVISOR_RUNTIME_IMPLEMENTATION_PLAN_ZH.md"
        : "https://github.com/justForever17/v8-agent-os/blob/main/docs/NETWORK_SUPERVISOR_RUNTIME_IMPLEMENTATION_PLAN.md";
    const availability = status.toolAvailability?.delegate_network_task || status.delegationAvailability;
    const portNotices = bridgeDiagnostics?.notices || [];

    const availabilityReason = React.useCallback((reason: string) => {
        switch (reason) {
            case "runtime_disabled":
                return t(lt("runtime 还没有启用。", "The runtime is not enabled yet."));
            case "delegation_disabled":
                return t(lt("远程委派当前已关闭。", "Remote delegation is currently disabled."));
            case "no_trusted_peers":
                return t(lt("还没有 trusted peers。", "There are no trusted peers yet."));
            case "no_online_trusted_peers":
                return t(lt("已有 trusted peers，但当前都不在线。", "Trusted peers exist, but none are online."));
            default:
                return reason;
        }
    }, [t]);

    const renderPortNotice = React.useCallback((notice: LegacyPortNotice) => {
        switch (notice.code) {
            case "config_migrated":
                return t(lt(
                    `已把 ${notice.path} 里的旧本地端口自动迁移到 Admin 9528 / Engine 9530。建议重启相关服务，并确认本地 .env.local 没再写回旧值。`,
                    `Legacy local ports in ${notice.path} were auto-migrated to Admin 9528 / Engine 9530. Restart the related services and make sure no local .env.local writes the old values back.`,
                ));
            case "admin_env_legacy_ports":
                return t(lt(
                    `${notice.path} 里仍然存在 8000 或 5001。它可能继续把 Admin 拉回旧本地端口。`,
                    `${notice.path} still contains 8000 or 5001. It can drag Admin back to the legacy local ports.`,
                ));
            case "web_env_legacy_ports":
                return t(lt(
                    `${notice.path} 里仍然存在 8000 或 5001。它可能继续让 Web 连接到旧端口。`,
                    `${notice.path} still contains 8000 or 5001. It can make Web reconnect to the legacy ports.`,
                ));
            default:
                return notice.path;
        }
    }, [t]);

    const loadAll = React.useCallback(async () => {
        setLoading(true);
        setLoadError(null);
        try {
            const [configRes, statusRes, peerRes] = await Promise.all([
                fetch("/api/config-registry/network-supervisor-runtime", { cache: "no-store" }),
                fetch("/api/network-supervisor/status", { cache: "no-store" }),
                fetch("/api/network-supervisor/peers", { cache: "no-store" }),
            ]);
            const [configData, statusData, peerData] = await Promise.all([
                configRes.json().catch(() => ({})),
                statusRes.json().catch(() => ({})),
                peerRes.json().catch(() => ({})),
            ]);
            if (!configRes.ok) throw new Error(detail(configData, t(lt("读取配置失败。", "Failed to load configuration."))));
            if (!statusRes.ok) throw new Error(detail(statusData, t(lt("读取状态失败。", "Failed to load runtime status."))));
            if (!peerRes.ok) throw new Error(detail(peerData, t(lt("读取 peer 列表失败。", "Failed to load peer list."))));
            setConfig(mergeConfig((configData as { data?: Partial<RuntimeConfig> }).data));
            setStatus(normalizeStatus(statusData));
            setPeers(normalizePeers(peerData));
        } catch (error) {
            const message = error instanceof Error ? error.message : t(lt("无法读取 NETWORK SUPERVISOR RUNTIME。", "Unable to load NETWORK SUPERVISOR RUNTIME."));
            setLoadError(message);
            toast({ variant: "destructive", title: t(lt("加载失败", "Load failed")), description: message });
        } finally {
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
                throw new Error(detail(payload, t(lt("保存配置失败。", "Failed to save config."))));
            }
            setConfig(mergeConfig((payload as { data?: Partial<RuntimeConfig> }).data));
            toast({
                title: t(lt("配置已保存", "Configuration saved")),
                description: t(lt("NETWORK SUPERVISOR RUNTIME 会按新设置重新解释发现、信任与委派状态。", "NETWORK SUPERVISOR RUNTIME will reload discovery, trust, and delegation behavior with the new settings.")),
            });
            await loadAll();
        } catch (error) {
            toast({
                variant: "destructive",
                title: t(lt("保存失败", "Save failed")),
                description: error instanceof Error ? error.message : t(lt("当前无法保存配置。", "Unable to save config right now.")),
            });
        } finally {
            setSavingConfig(false);
        }
    }, [config, loadAll, t, toast]);

    const savePeer = React.useCallback(async () => {
        if (!peerForm.peerId.trim()) {
            toast({
                variant: "destructive",
                title: t(lt("缺少 Peer ID", "Peer ID is required")),
                description: t(lt("请先填写 Peer ID。", "Fill in the Peer ID first.")),
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
                throw new Error(detail(payload, t(lt("保存 peer 失败。", "Failed to save peer."))));
            }
            toast({
                title: t(lt("Peer 已保存", "Peer saved")),
                description: t(lt("trusted peer 列表已经更新。", "The trusted peer list has been updated.")),
            });
            setPeerForm((prev) => ({ ...prev, peerToken: "" }));
            await loadAll();
        } catch (error) {
            toast({
                variant: "destructive",
                title: t(lt("保存失败", "Save failed")),
                description: error instanceof Error ? error.message : t(lt("当前无法保存 peer。", "Unable to save the peer right now.")),
            });
        } finally {
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
                throw new Error(detail(payload, t(lt("删除 peer 失败。", "Failed to delete peer."))));
            }
            toast({
                title: t(lt("Peer 已删除", "Peer deleted")),
                description: t(lt("trusted peer 已从当前节点移除。", "The trusted peer has been removed from this node.")),
            });
            if (diag.peerId === peerId) {
                setDiag((prev) => ({ ...prev, peerId: "" }));
            }
            if (peerForm.peerId === peerId) {
                setPeerForm(EMPTY_PEER_FORM);
            }
            await loadAll();
        } catch (error) {
            toast({
                variant: "destructive",
                title: t(lt("删除失败", "Delete failed")),
                description: error instanceof Error ? error.message : t(lt("当前无法删除该 peer。", "Unable to delete this peer right now.")),
            });
        }
    }, [diag.peerId, loadAll, peerForm.peerId, t, toast]);

    const runDiagnostic = React.useCallback(async (kind: "challenge" | "wake" | "delegate", peerId?: string) => {
        const targetPeerId = String(peerId || diag.peerId || "").trim();
        if (!targetPeerId) {
            toast({
                variant: "destructive",
                title: t(lt("缺少目标 Peer", "Target peer is required")),
                description: t(lt("先从 discovered peers 或 trusted peers 里选一个目标。", "Pick a target from discovered peers or trusted peers first.")),
            });
            return;
        }
        setRunning(kind);
        try {
            const response = await fetch(
                kind === "delegate" ? "/api/network-supervisor/delegations" : `/api/network-supervisor/diagnostics/${kind}`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        peerId: targetPeerId,
                        note: diag.note.trim(),
                        task: diag.task.trim(),
                        timeoutSeconds: 120,
                    }),
                },
            );
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(detail(payload, t(lt("诊断请求失败。", "Diagnostic request failed."))));
            }
            setDiag((prev) => ({ ...prev, peerId: targetPeerId, result: JSON.stringify(payload, null, 2) }));
            toast({
                title: t(
                    kind === "challenge"
                        ? lt("Challenge 已发送", "Challenge sent")
                        : kind === "wake"
                          ? lt("Wake 已发送", "Wake sent")
                          : lt("委派已发出", "Delegation sent"),
                ),
                description: t(
                    kind === "delegate"
                        ? lt("如果远端接受，这里会显示 accepted / progress / result。", "If the remote peer accepts, this panel will show accepted / progress / result.")
                        : lt("请查看返回结果和状态区，确认远端是否已回应。", "Check the response payload and status panel to confirm the remote peer replied."),
                ),
            });
            await loadAll();
        } catch (error) {
            const message = error instanceof Error ? error.message : t(lt("当前无法执行该诊断。", "Unable to run this diagnostic right now."));
            setDiag((prev) => ({ ...prev, result: message }));
            toast({
                variant: "destructive",
                title: t(lt("诊断失败", "Diagnostic failed")),
                description: message,
            });
        } finally {
            setRunning("");
        }
    }, [diag.note, diag.peerId, diag.task, loadAll, t, toast]);

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={lt("NETWORK SUPERVISOR RUNTIME", "NETWORK SUPERVISOR RUNTIME")}
                description={lt(
                    "让当前节点发现、信任、唤醒并显式委派任务给其他 V8 节点。首版只做可观测、可诊断、可恢复的显式远程协作。",
                    "Discover, trust, wake, and explicitly delegate work to other V8 nodes. The first release keeps remote collaboration observable, diagnosable, and recoverable.",
                )}
                badges={[lt("显式委派", "Explicit delegation"), lt("局域网 + 广域网", "LAN + WAN")]}
                actions={
                    <>
                        <Button variant="outline" onClick={() => void loadAll()} disabled={loading}>
                            {t(lt("刷新", "Refresh"))}
                        </Button>
                        <Button asChild variant="outline">
                            <Link href={docsUrl} target="_blank">
                                {t(lt("设计文档", "Design doc"))}
                            </Link>
                        </Button>
                    </>
                }
            />

            {loadError ? (
                <ConfigCard
                    title={lt("加载失败", "Load failed")}
                    description={loadError}
                    className="border-red-200"
                >
                    <div className="flex items-center justify-end">
                        <Button onClick={() => void loadAll()}>{t(lt("重试", "Retry"))}</Button>
                    </div>
                </ConfigCard>
            ) : null}

            {portNotices.length ? (
                <ConfigCard
                    title={lt("端口与本地配置提醒", "Local port and config reminders")}
                    description={lt("这里直接提醒你哪些旧本地端口已经被自动迁移，哪些本地 .env 仍可能把系统拉回旧时代。", "This area shows which legacy local ports were auto-migrated and which local .env files can still drag the system back.")}
                    variant="list"
                    bodyHeight="auto"
                >
                    <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-950">
                        <ul className="list-disc space-y-2 pl-5 leading-6">
                            {portNotices.map((notice, index) => (
                                <li key={`${notice.code}-${notice.path}-${index}`}>{renderPortNotice(notice)}</li>
                            ))}
                        </ul>
                    </div>
                </ConfigCard>
            ) : null}

            <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
                <ConfigCard
                    title={lt("Runtime 配置", "Runtime configuration")}
                    description={lt("这里决定当前节点怎么发现 peers、暴露地址、处理 wake，以及是否允许远程委派。", "Control how this node discovers peers, advertises endpoints, handles wake requests, and allows remote delegation.")}
                    variant="editor"
                    bodyHeight="auto"
                >
                    <div className="grid gap-5 lg:grid-cols-2">
                        <div className="space-y-4">
                            <div className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                                <div className="space-y-1">
                                    <div className="text-sm font-medium text-slate-900">{t(lt("启用 runtime", "Enable runtime"))}</div>
                                    <div className="text-xs text-slate-500">{t(lt("关闭后，不再接收发现、wake 和远程 delegation。", "When disabled, this node stops discovery, wake, and remote delegation."))}</div>
                                </div>
                                <Switch checked={config.enabled} onCheckedChange={(checked) => setConfig((prev) => ({ ...prev, enabled: checked }))} aria-label="network-supervisor-enabled" />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="network-node-name">{t(lt("节点显示名", "Node display name"))}</Label>
                                <Input id="network-node-name" value={config.node.displayName} onChange={(event) => setNode({ displayName: event.target.value })} />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="network-peer-id">{t(lt("Peer ID", "Peer ID"))}</Label>
                                <Input id="network-peer-id" value={config.node.peerId} onChange={(event) => setNode({ peerId: event.target.value })} placeholder="peer_xxx" />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="network-base-url">{t(lt("Base URL", "Base URL"))}</Label>
                                <Input id="network-base-url" value={config.node.advertisedBaseUrl} onChange={(event) => setNode({ advertisedBaseUrl: event.target.value })} placeholder="http://127.0.0.1:9530" />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="network-ws-url">{t(lt("WS URL", "WS URL"))}</Label>
                                <Input id="network-ws-url" value={config.node.advertisedWsUrl} onChange={(event) => setNode({ advertisedWsUrl: event.target.value })} placeholder="ws://127.0.0.1:9530/v1/network-supervisor/peer/ws" />
                            </div>
                        </div>

                        <div className="space-y-4">
                            <div className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                                <div className="space-y-1">
                                    <div className="text-sm font-medium text-slate-900">{t(lt("启用局域网发现", "Enable LAN discovery"))}</div>
                                    <div className="text-xs text-slate-500">{t(lt("首版使用 UDP multicast，只做发现，不自动建立 trust。", "The first release uses UDP multicast for discovery only. Discovery does not imply trust."))}</div>
                                </div>
                                <Switch checked={config.discovery.lanEnabled} onCheckedChange={(checked) => setDiscovery({ lanEnabled: checked })} aria-label="network-discovery-enabled" />
                            </div>
                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="network-group">{t(lt("Multicast Group", "Multicast group"))}</Label>
                                    <Input id="network-group" value={config.discovery.multicastGroup} onChange={(event) => setDiscovery({ multicastGroup: event.target.value })} />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="network-port">{t(lt("Multicast Port", "Multicast port"))}</Label>
                                    <Input id="network-port" type="number" value={String(config.discovery.multicastPort)} onChange={(event) => setDiscovery({ multicastPort: Number(event.target.value || 0) })} />
                                </div>
                            </div>
                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="network-announce">{t(lt("Announce 秒数", "Announce interval"))}</Label>
                                    <Input id="network-announce" type="number" value={String(config.discovery.announceIntervalSeconds)} onChange={(event) => setDiscovery({ announceIntervalSeconds: Number(event.target.value || 0) })} />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="network-expiry">{t(lt("Peer 过期秒数", "Peer expiry"))}</Label>
                                    <Input id="network-expiry" type="number" value={String(config.discovery.peerExpirySeconds)} onChange={(event) => setDiscovery({ peerExpirySeconds: Number(event.target.value || 0) })} />
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="network-bootstrap">{t(lt("WAN Bootstrap Peers", "WAN bootstrap peers"))}</Label>
                                <Textarea id="network-bootstrap" rows={4} value={joinLines(config.discovery.wanBootstrapPeers)} onChange={(event) => setDiscovery({ wanBootstrapPeers: lines(event.target.value) })} placeholder={"https://peer-a.example.com\nhttps://peer-b.example.com"} />
                            </div>
                        </div>
                    </div>

                    <div className="grid gap-5 border-t border-slate-100 pt-5 lg:grid-cols-3">
                        <div className="space-y-2">
                            <Label htmlFor="network-enrollment">{t(lt("Enrollment 模式", "Enrollment mode"))}</Label>
                            <Input id="network-enrollment" value={config.trust.enrollmentMode} onChange={(event) => setTrust({ enrollmentMode: event.target.value === "open" ? "open" : "manual" })} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="network-allowed-scopes">{t(lt("允许的 scopes", "Allowed scopes"))}</Label>
                            <Input id="network-allowed-scopes" value={joinCsv(config.trust.allowedScopes)} onChange={(event) => setTrust({ allowedScopes: csv(event.target.value) })} placeholder="global, workspace, memory" />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="network-delegation-timeout">{t(lt("默认委派超时（秒）", "Default delegation timeout"))}</Label>
                            <Input id="network-delegation-timeout" type="number" value={String(config.delegation.defaultTimeoutSeconds)} onChange={(event) => setDelegation({ defaultTimeoutSeconds: Number(event.target.value || 0) })} />
                        </div>
                    </div>

                    <div className="grid gap-5 lg:grid-cols-2">
                        <div className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t(lt("启用 directed wake", "Enable directed wake"))}</div>
                                <div className="text-xs text-slate-500">{t(lt("只处理定向唤醒与回执，不把 wake 伪装成普通聊天消息。", "Wake only handles directed wake and acknowledgement. It never pretends to be a normal chat message."))}</div>
                            </div>
                            <Switch checked={config.wake.enabled} onCheckedChange={(checked) => setWake({ enabled: checked })} aria-label="network-wake-enabled" />
                        </div>
                        <div className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t(lt("启用远程 delegation", "Enable remote delegation"))}</div>
                                <div className="text-xs text-slate-500">{t(lt("真正执行仍走远端本地 chat runtime，network runtime 只负责 control-plane。", "Execution still happens on the remote node's local chat runtime. The network runtime only owns the control plane."))}</div>
                            </div>
                            <Switch checked={config.delegation.enabled} onCheckedChange={(checked) => setDelegation({ enabled: checked })} aria-label="network-delegation-enabled" />
                        </div>
                    </div>

                    <div className="grid gap-4 lg:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="network-ack-timeout">{t(lt("Wake ACK 超时（秒）", "Wake ACK timeout"))}</Label>
                            <Input id="network-ack-timeout" type="number" value={String(config.wake.ackTimeoutSeconds)} onChange={(event) => setWake({ ackTimeoutSeconds: Number(event.target.value || 0) })} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="network-max-concurrency">{t(lt("最大并发委派", "Max concurrent delegations"))}</Label>
                            <Input id="network-max-concurrency" type="number" value={String(config.delegation.maxConcurrent)} onChange={(event) => setDelegation({ maxConcurrent: Number(event.target.value || 0) })} />
                        </div>
                    </div>

                    <div className="flex justify-end">
                        <Button onClick={() => void saveConfig()} disabled={savingConfig}>
                            {savingConfig ? t(lt("保存中...", "Saving...")) : t(lt("保存配置", "Save configuration"))}
                        </Button>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title={lt("当前状态", "Current status")}
                    description={lt("这里直接看当前节点的身份、发现状态、委派能力和工具可用性原因。", "See the node identity, discovery state, delegation readiness, and the reasons behind tool availability here.")}
                    variant="list"
                    bodyHeight="auto"
                >
                    {loading ? (
                        <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
                            {t(lt("正在读取 runtime 状态...", "Loading runtime status..."))}
                        </div>
                    ) : (
                        <div className="space-y-4 text-sm text-slate-700">
                            <div className="flex flex-wrap gap-2">
                                <Badge variant={status.enabled ? "default" : "secondary"}>{status.enabled ? t(lt("已启用", "Enabled")) : t(lt("已关闭", "Disabled"))}</Badge>
                                <Badge variant={status.started ? "default" : "secondary"}>{status.started ? t(lt("已启动", "Started")) : t(lt("未启动", "Stopped"))}</Badge>
                                <Badge variant={availability.available ? "default" : "secondary"}>{availability.available ? t(lt("可委派", "Delegation ready")) : t(lt("暂不可委派", "Delegation blocked"))}</Badge>
                            </div>
                            <div className="grid gap-3 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                                <div><span className="font-medium text-slate-900">{t(lt("Peer ID：", "Peer ID:"))}</span> {status.node.peerId || "—"}</div>
                                <div><span className="font-medium text-slate-900">{t(lt("显示名：", "Display name:"))}</span> {status.node.displayName || "—"}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">{t(lt("Base URL：", "Base URL:"))}</span> {status.node.advertisedBaseUrl || "—"}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">{t(lt("WS URL：", "WS URL:"))}</span> {status.node.advertisedWsUrl || "—"}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">{t(lt("Public key 指纹：", "Public key fingerprint:"))}</span> {status.node.publicKeyFingerprint || "—"}</div>
                                <div className="break-all"><span className="font-medium text-slate-900">{t(lt("本地 token 指纹：", "Local token fingerprint:"))}</span> {status.node.localPeerTokenFingerprint || "—"}</div>
                            </div>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                                    <div className="text-xs text-slate-500">{t(lt("发现到的 peers", "Discovered peers"))}</div>
                                    <div className="mt-1 text-lg font-semibold text-slate-900">{status.discovery.discoveredPeerCount}</div>
                                </div>
                                <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                                    <div className="text-xs text-slate-500">{t(lt("在线 trusted peers", "Online trusted peers"))}</div>
                                    <div className="mt-1 text-lg font-semibold text-slate-900">{status.discovery.onlinePeerCount}</div>
                                </div>
                                <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                                    <div className="text-xs text-slate-500">{t(lt("活跃 inbound", "Active inbound"))}</div>
                                    <div className="mt-1 text-lg font-semibold text-slate-900">{status.delegation.activeInbound}</div>
                                </div>
                                <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                                    <div className="text-xs text-slate-500">{t(lt("跟踪中的委派", "Tracked delegations"))}</div>
                                    <div className="mt-1 text-lg font-semibold text-slate-900">{status.delegation.trackedCount}</div>
                                </div>
                            </div>
                            {availability.reasons.length ? (
                                <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-900">
                                    <div className="font-medium">{t(lt("当前为什么还不能稳定推荐 `delegate_network_task`", "Why `delegate_network_task` is not ready to recommend yet"))}</div>
                                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-5">
                                        {availability.reasons.map((reason) => (
                                            <li key={reason}>{availabilityReason(reason)}</li>
                                        ))}
                                    </ul>
                                </div>
                            ) : (
                                <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm text-emerald-900">
                                    {t(lt("当前节点已经具备显式远程委派条件。", "This node is ready for explicit remote delegation."))}
                                </div>
                            )}
                        </div>
                    )}
                </ConfigCard>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
                <ConfigCard
                    title={lt("Peer 路径", "Peer flow")}
                    description={lt("先发现，再 challenge，再 trust，再 wake，再 delegation。首版不做拓扑控制台，只把路径做顺。", "Start with discovery, then challenge, trust, wake, and delegation. The first release keeps the flow simple instead of building a topology console.")}
                    variant="list"
                    bodyHeight={420}
                    bodyScroll="auto"
                >
                    <div className="space-y-6">
                        <section className="space-y-3">
                            <div className="flex items-center justify-between">
                                <h3 className="text-sm font-semibold text-slate-900">{t(lt("Discovered peers", "Discovered peers"))}</h3>
                                <Badge variant="outline">{peers.discoveredItems.length}</Badge>
                            </div>
                            {peers.discoveredItems.length ? (
                                <div className="space-y-3">
                                    {peers.discoveredItems.map((peer) => (
                                        <div key={`discovered-${peer.peerId}`} className="rounded-2xl border border-slate-200 bg-white px-4 py-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="font-medium text-slate-900">{peer.displayName || peer.peerId}</div>
                                                <Badge variant="outline">{peer.peerId}</Badge>
                                                <Badge variant={peer.online ? "default" : "secondary"}>{peer.online ? t(lt("在线", "Online")) : t(lt("离线", "Offline"))}</Badge>
                                                {peer.source ? <Badge variant="secondary">{t(sourceLabel(peer.source))}</Badge> : null}
                                            </div>
                                            <div className="mt-2 space-y-1 text-xs text-slate-500">
                                                <div className="break-all">{peer.baseUrl || "—"}</div>
                                                {peer.address ? <div>{peer.address}</div> : null}
                                                {peer.lastSeenAt ? <div>{t(lt("最近发现：", "Last seen:"))}{peer.lastSeenAt}</div> : null}
                                            </div>
                                            <div className="mt-4 flex flex-wrap gap-2">
                                                <Button variant="outline" size="sm" onClick={() => fillPeerForm(peer)}>
                                                    {t(lt("带入编辑器", "Fill editor"))}
                                                </Button>
                                                <Button variant="outline" size="sm" onClick={() => chooseDiagPeer(peer.peerId)}>
                                                    {t(lt("设为诊断目标", "Use for diagnostics"))}
                                                </Button>
                                                <Button variant="outline" size="sm" disabled={running === "challenge"} onClick={() => void runDiagnostic("challenge", peer.peerId)}>
                                                    {running === "challenge" && diag.peerId === peer.peerId ? t(lt("发送中...", "Sending...")) : t(lt("Challenge", "Challenge"))}
                                                </Button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-6 text-sm text-slate-500">
                                    {t(lt("当前还没有发现到 peers。先确认局域网发现已启用，或在上方填 WAN bootstrap peers。", "No peers have been discovered yet. Enable LAN discovery first, or provide WAN bootstrap peers above."))}
                                </div>
                            )}
                        </section>

                        <section className="space-y-3">
                            <div className="flex items-center justify-between">
                                <h3 className="text-sm font-semibold text-slate-900">{t(lt("Trusted peers", "Trusted peers"))}</h3>
                                <Badge variant="outline">{peers.trustedItems.length}</Badge>
                            </div>
                            {peers.trustedItems.length ? (
                                <div className="space-y-3">
                                    {peers.trustedItems.map((peer) => (
                                        <div key={`trusted-${peer.peerId}`} className="rounded-2xl border border-slate-200 bg-white px-4 py-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="font-medium text-slate-900">{peer.displayName || peer.peerId}</div>
                                                <Badge variant="outline">{peer.peerId}</Badge>
                                                <Badge variant="default">{t(lt("Trusted", "Trusted"))}</Badge>
                                                <Badge variant={peer.online ? "default" : "secondary"}>{peer.online ? t(lt("在线", "Online")) : t(lt("离线", "Offline"))}</Badge>
                                            </div>
                                            <div className="mt-2 space-y-1 text-xs text-slate-500">
                                                <div className="break-all">{peer.baseUrl || "—"}</div>
                                                <div className="break-all">{t(lt("Token 指纹：", "Token fingerprint:"))}{peer.tokenFingerprint || "—"}</div>
                                                <div>{t(lt("Scopes：", "Scopes:"))}{peer.allowedScopes.length ? joinCsv(peer.allowedScopes) : "—"}</div>
                                            </div>
                                            <div className="mt-4 flex flex-wrap gap-2">
                                                <Button variant="outline" size="sm" onClick={() => fillPeerForm(peer)}>
                                                    {t(lt("编辑", "Edit"))}
                                                </Button>
                                                <Button variant="outline" size="sm" onClick={() => chooseDiagPeer(peer.peerId)}>
                                                    {t(lt("设为诊断目标", "Use for diagnostics"))}
                                                </Button>
                                                <Button variant="outline" size="sm" onClick={() => void deletePeer(peer.peerId)}>
                                                    {t(lt("删除", "Delete"))}
                                                </Button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-6 text-sm text-slate-500">
                                    {t(lt("还没有 trusted peers。可以先从 discovered peers 带入编辑器，再补 token 与 allowed scopes。", "There are no trusted peers yet. Start from a discovered peer, then add the token and allowed scopes."))}
                                </div>
                            )}
                        </section>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title={lt("Peer Editor", "Peer editor")}
                    description={lt("这里决定哪些 peer 被当前节点信任、允许哪些 scope、以及 challenge / wake / delegation 要打向谁。", "This is where you trust a peer, define allowed scopes, and decide which peer challenge / wake / delegation should target.")}
                    variant="editor"
                    bodyHeight="auto"
                >
                    <div className="space-y-4">
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2">
                                <Label htmlFor="peer-form-id">{t(lt("Peer ID", "Peer ID"))}</Label>
                                <Input id="peer-form-id" value={peerForm.peerId} onChange={(event) => setPeerForm((prev) => ({ ...prev, peerId: event.target.value }))} />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="peer-form-name">{t(lt("显示名", "Display name"))}</Label>
                                <Input id="peer-form-name" value={peerForm.displayName} onChange={(event) => setPeerForm((prev) => ({ ...prev, displayName: event.target.value }))} />
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-base">{t(lt("Base URL", "Base URL"))}</Label>
                            <Input id="peer-form-base" value={peerForm.baseUrl} onChange={(event) => setPeerForm((prev) => ({ ...prev, baseUrl: event.target.value }))} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-ws">{t(lt("WS URL", "WS URL"))}</Label>
                            <Input id="peer-form-ws" value={peerForm.wsUrl} onChange={(event) => setPeerForm((prev) => ({ ...prev, wsUrl: event.target.value }))} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-public-key">{t(lt("Public key", "Public key"))}</Label>
                            <Textarea id="peer-form-public-key" rows={4} value={peerForm.publicKey} onChange={(event) => setPeerForm((prev) => ({ ...prev, publicKey: event.target.value }))} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-token">{t(lt("Peer token", "Peer token"))}</Label>
                            <Input id="peer-form-token" value={peerForm.peerToken} onChange={(event) => setPeerForm((prev) => ({ ...prev, peerToken: event.target.value }))} placeholder={t(lt("只写入 secret，不会在 GET 回显。", "Only written to secrets. It will never be returned by GET."))} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-scopes">{t(lt("Allowed scopes", "Allowed scopes"))}</Label>
                            <Input id="peer-form-scopes" value={peerForm.allowedScopes} onChange={(event) => setPeerForm((prev) => ({ ...prev, allowedScopes: event.target.value }))} placeholder="global, workspace, memory" />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="peer-form-workspaces">{t(lt("Allowed workspaces", "Allowed workspaces"))}</Label>
                            <Textarea id="peer-form-workspaces" rows={4} value={peerForm.allowedWorkspaces} onChange={(event) => setPeerForm((prev) => ({ ...prev, allowedWorkspaces: event.target.value }))} placeholder={"workspace-a\nworkspace-b"} />
                        </div>
                        <div className="flex items-center justify-between gap-3">
                            <Button variant="outline" onClick={() => setPeerForm(EMPTY_PEER_FORM)}>
                                {t(lt("清空", "Clear"))}
                            </Button>
                            <Button onClick={() => void savePeer()} disabled={savingPeer}>
                                {savingPeer ? t(lt("保存中...", "Saving...")) : t(lt("保存 Peer", "Save peer"))}
                            </Button>
                        </div>
                    </div>
                </ConfigCard>
            </div>

            <ConfigCard
                title={lt("Diagnostics", "Diagnostics")}
                description={lt("首版用手动 challenge / wake / delegation 诊断整条链。先选一个 trusted peer，再决定你要唤醒还是直接委派任务。", "Use manual challenge / wake / delegation to diagnose the whole first-release path. Pick a trusted peer first, then decide whether you want to wake it or delegate work directly.")}
                variant="editor"
                bodyHeight="auto"
            >
                <div className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="diag-peer">{t(lt("当前目标 Peer", "Current target peer"))}</Label>
                            <Input id="diag-peer" value={diag.peerId} onChange={(event) => setDiag((prev) => ({ ...prev, peerId: event.target.value }))} placeholder="peer_xxx" />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="diag-note">{t(lt("备注 / note", "Note"))}</Label>
                            <Textarea id="diag-note" rows={3} value={diag.note} onChange={(event) => setDiag((prev) => ({ ...prev, note: event.target.value }))} placeholder={t(lt("例如：Admin 手动 challenge", "For example: manual challenge from Admin"))} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="diag-task">{t(lt("委派任务", "Delegation task"))}</Label>
                            <Textarea id="diag-task" rows={6} value={diag.task} onChange={(event) => setDiag((prev) => ({ ...prev, task: event.target.value }))} placeholder={t(lt("写一段要发给远端 chat runtime 的真实任务。", "Write the real task that should run on the remote chat runtime."))} />
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Button variant="outline" disabled={running !== ""} onClick={() => void runDiagnostic("challenge")}>
                                {running === "challenge" ? t(lt("发送中...", "Sending...")) : t(lt("Challenge", "Challenge"))}
                            </Button>
                            <Button variant="outline" disabled={running !== ""} onClick={() => void runDiagnostic("wake")}>
                                {running === "wake" ? t(lt("发送中...", "Sending...")) : t(lt("Wake", "Wake"))}
                            </Button>
                            <Button disabled={running !== ""} onClick={() => void runDiagnostic("delegate")}>
                                {running === "delegate" ? t(lt("委派中...", "Delegating...")) : t(lt("Delegate", "Delegate"))}
                            </Button>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="diag-result">{t(lt("返回结果 / 状态投影", "Result / status projection"))}</Label>
                        <Textarea
                            id="diag-result"
                            rows={18}
                            value={diag.result}
                            onChange={(event) => setDiag((prev) => ({ ...prev, result: event.target.value }))}
                            placeholder={t(lt("Challenge、wake 或 delegation 的返回结果会显示在这里。", "The response payload from challenge, wake, or delegation will appear here."))}
                        />
                    </div>
                </div>
            </ConfigCard>
        </AdminPageShell>
    );
}
