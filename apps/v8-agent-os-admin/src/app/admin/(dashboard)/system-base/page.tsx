"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, KeyRound, Loader2, RefreshCw, Save, Shield, Server, Trash2, Wrench } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { Textarea } from "@/components/ui/textarea";
import { resolveAdminLabel } from "@/lib/admin-labels";
import {
    fetchConfigDomain,
    peekConfigDomain,
    saveConfigDomain,
    type ConfigRegistryEnvelope,
} from "@/lib/config-registry";

type SystemBaseData = {
    bridge?: {
        engineBaseUrl?: string;
        engineWsBaseUrl?: string;
        adminBaseUrl?: string;
        internalSecret?: string;
        allowedOrigins?: string[];
    };
    webFetch?: {
        bypassProxyEnv?: boolean;
        cacheDir?: string;
        adaptiveStorageFile?: string;
        useAgentBrowserProfile?: boolean;
        agentBrowserProfileAllowlist?: string[];
    };
    desktopTools?: {
        tesseractPath?: string;
        tessdataPrefix?: string;
    };
    desktopLive?: {
        enabled?: boolean;
        maxWidth?: number;
        maxHeight?: number;
        targetFps?: number;
        singleViewerOnly?: boolean;
        idleReleaseSeconds?: number;
        captureDisplay?: string;
        audioEnabled?: boolean;
        audioSource?: string;
        audioSampleRate?: number;
        audioChannels?: number;
        iceServers?: Array<{
            urls?: string | string[];
            username?: string;
            credential?: string;
        }>;
    };
    remoteLink?: {
        enabled?: boolean;
        activeProfileId?: string;
        transportProfiles?: Array<{
            id?: string;
            kind?: string;
            label?: string;
            enabled?: boolean;
            adminBaseUrl?: string;
            engineBaseUrl?: string;
            peerBaseUrl?: string;
        }>;
        meshProviders?: Array<{
            id?: string;
            kind?: string;
            enabled?: boolean;
            mode?: string;
            controlUrl?: string;
            namespace?: string;
            allowRouteMutation?: boolean;
        }>;
    };
    remoteLinkManifest?: {
        transportKind?: string;
        activeProfileId?: string;
        admin?: {
            baseUrl?: string;
            apiBaseUrl?: string;
        };
        engine?: {
            baseUrl?: string;
            apiBaseUrl?: string;
        };
        warnings?: string[];
        peerCandidates?: MeshPeerCandidate[];
        diagnostics?: {
            warnings?: string[];
            info?: string[];
            candidateIps?: Array<{ address?: string; family?: string; private?: boolean }>;
            vpn?: {
                wireguardDetected?: boolean;
                tailscaleDetected?: boolean;
            };
        };
    };
    remoteLinkMeshStatus?: {
        providers?: Array<{
            id?: string;
            kind?: string;
            enabled?: boolean;
            installed?: boolean;
            loggedIn?: boolean;
            status?: string;
            dnsName?: string;
            addresses?: string[];
            recommendedUrls?: {
                adminBaseUrl?: string;
                engineBaseUrl?: string;
                peerBaseUrl?: string;
            };
            warnings?: string[];
            recommendedNextAction?: string;
            apiKeyConfigured?: boolean;
            apiKeyFingerprint?: string;
            peerCandidates?: MeshPeerCandidate[];
        }>;
        peerCandidates?: MeshPeerCandidate[];
        policy?: {
            mutatesRoutes?: boolean;
            managesKeys?: boolean;
            installsClients?: boolean;
        };
    };
    s3?: {
        endpoint?: string;
        region?: string;
        bucket?: string;
        accessKeyId?: string;
        secretAccessKey?: string;
    };
    runtimeInfo?: {
        engineHost?: string;
        enginePort?: number;
        engineReload?: boolean;
    };
    desktopReadiness?: {
        status?: "ready" | "partial" | "missing";
        ocrReady?: boolean;
        imageLocatorReady?: boolean;
        pointLocatorReady?: boolean;
        missingItems?: string[];
    };
    detectedDesktopTools?: {
        tesseractPath?: string;
        tessdataPrefix?: string;
    };
    dependencyStatus?: Array<{
        id: string;
        label: string;
        requiredness: "required" | "conditional" | "optional";
        category: "core" | "desktop" | "automation" | "media";
        platforms: string[];
        usedBy?: string[];
        installHint?: string;
        appliesToCurrentPlatform?: boolean;
        currentPlatform?: string;
        detection?: {
            detected?: boolean;
            detail?: string;
        };
    }>;
};

type MeshPeerCandidate = {
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
};

type HeadscalePanelData = {
    status?: Record<string, unknown>;
    users?: Array<Record<string, unknown>>;
    nodes?: Array<Record<string, unknown>>;
    preauthKeys?: Array<Record<string, unknown>>;
    createdPreauthKey?: Record<string, unknown> | null;
};

function formatEndpointSummary(value?: string, emptyLabel = "Not set") {
    if (!value) return emptyLabel;
    try {
        const url = new URL(value);
        const suffix = url.pathname && url.pathname !== "/" ? url.pathname.replace(/\/$/, "") : "";
        return `${url.host}${suffix}`;
    } catch {
        return value;
    }
}

function desktopStatusLabel(status: string | undefined, t: (value: string) => string) {
    if (status === "ready") return t("app.admin.dashboard.system.base.page.k43d7227d");
    if (status === "partial") return t("app.admin.dashboard.system.base.page.k536a6446");
    return t("app.admin.dashboard.system.base.page.kc1cf5e35");
}

function desktopStatusTone(status?: string) {
    if (status === "ready") return "text-emerald-700 bg-emerald-50 border-emerald-200";
    if (status === "partial") return "text-amber-700 bg-amber-50 border-amber-200";
    return "text-rose-700 bg-rose-50 border-rose-200";
}

function normalizeOriginLines(value: string) {
    const seen = new Set<string>();
    return value
        .split(/\r?\n/)
        .map((item) => item.trim().replace(/\/+$/, ""))
        .filter((item) => {
            if (!item || seen.has(item)) return false;
            seen.add(item);
            return true;
        });
}

function formatOriginsSummary(origins: string[] | undefined, t: (value: string) => string) {
    const normalized = normalizeOriginLines((origins || []).join("\n"));
    if (normalized.length === 0) return t("app.admin.dashboard.system.base.page.kd8a4dc44");
    if (normalized.length === 1) return normalized[0];
    return `${normalized[0]} · ${normalized.length}`;
}

function transportLabel(kind: string | undefined, t: (value: string) => string) {
    const normalized = String(kind || "manual_url").replace(/-/g, "_");
    if (normalized === "lan") return "LAN";
    if (normalized === "wireguard") return "WireGuard";
    if (normalized === "tailscale") return "Tailscale";
    if (normalized === "headscale") return "Headscale";
    if (normalized === "custom_vpn") return t("app.admin.dashboard.system.base.remoteLink.customVpn");
    return t("app.admin.dashboard.system.base.remoteLink.manualUrl");
}

function looksLikeLoopbackOrigin(value?: string) {
    const normalized = String(value || "").trim();
    return /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?(?:\/|$)/i.test(normalized);
}

const DESKTOP_LIVE_PRESETS = [
    {
        id: "smooth",
        label: "app.admin.dashboard.system.base.page.k6258ee39",
        summary: "640 × 360 · 5 FPS",
        description: "app.admin.dashboard.system.base.page.k157e0efb",
        values: { maxWidth: 640, maxHeight: 360, targetFps: 5 },
    },
    {
        id: "balanced",
        label: "app.admin.dashboard.system.base.page.k0f34dd0d",
        summary: "960 × 540 · 10 FPS",
        description: "app.admin.dashboard.system.base.page.k6a1fa3c3",
        values: { maxWidth: 960, maxHeight: 540, targetFps: 10 },
    },
    {
        id: "clear",
        label: "app.admin.dashboard.system.base.page.k76077083",
        summary: "1280 × 720 · 15 FPS",
        description: "app.admin.dashboard.system.base.page.ke7a24a3a",
        values: { maxWidth: 1280, maxHeight: 720, targetFps: 15 },
    },
] as const;

type DesktopLivePresetId = (typeof DESKTOP_LIVE_PRESETS)[number]["id"] | "custom";

function deriveDesktopLivePreset(config?: SystemBaseData["desktopLive"]): DesktopLivePresetId {
    const width = Number(config?.maxWidth ?? 960);
    const height = Number(config?.maxHeight ?? 540);
    const fps = Number(config?.targetFps ?? 10);
    const matchedPreset = DESKTOP_LIVE_PRESETS.find((preset) => (
        preset.values.maxWidth === width
        && preset.values.maxHeight === height
        && preset.values.targetFps === fps
    ));
    return matchedPreset?.id || "custom";
}

export default function SystemBasePage() {
    const t = useT();
    const [initialEnvelope] = useState(() => peekConfigDomain<SystemBaseData>("system-base") ?? null);
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<SystemBaseData> | null>(initialEnvelope);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [headscaleBusy, setHeadscaleBusy] = useState(false);
    const [headscaleApiKey, setHeadscaleApiKey] = useState("");
    const [headscaleUserId, setHeadscaleUserId] = useState("");
    const [headscaleTtlMinutes, setHeadscaleTtlMinutes] = useState(60);
    const [headscaleMessage, setHeadscaleMessage] = useState("");
    const [headscaleData, setHeadscaleData] = useState<HeadscalePanelData>({});

    const loadData = async () => {
        const next = await fetchConfigDomain<SystemBaseData>("system-base");
        setEnvelope(next);
    };

    useEffect(() => {
        void loadData();
    }, []);

    const summaryItems = useMemo(() => {
        const bridge = envelope?.data.bridge || {};
        const readiness = envelope?.data.desktopReadiness;
        const missingItems = readiness?.missingItems || [];
        return [
            { label:"app.admin.dashboard.system.base.page.k4fc169e3", value: formatEndpointSummary(bridge.engineBaseUrl, t("app.admin.dashboard.system.base.page.k6ed9c299")), description:"app.admin.dashboard.system.base.page.kc2beee30" },
            {
                label:"app.admin.dashboard.system.base.page.k982cc191",
                value: formatEndpointSummary(bridge.adminBaseUrl, t("app.admin.dashboard.system.base.page.k6ed9c299")),
                description: looksLikeLoopbackOrigin(bridge.adminBaseUrl)
                    ? t("app.admin.dashboard.system.base.page.k3d831ecf")
                    : t("app.admin.dashboard.system.base.page.kb4159a72"),
            },
            {
                label: t("app.admin.dashboard.system.base.page.kc963695d"),
                value: desktopStatusLabel(readiness?.status, t),
                description: missingItems.length > 0 ? missingItems.slice(0, 2).join("，") : t("app.admin.dashboard.system.base.page.kb18f35aa"),
            },
            { label: t("app.admin.dashboard.system.base.page.k9120bbb9"), value: formatOriginsSummary(bridge.allowedOrigins, t), description: t("app.admin.dashboard.system.base.page.k32616100") },
        ];
    }, [envelope, t]);

    const updateData = (recipe: (current: SystemBaseData) => SystemBaseData) => {
        setEnvelope((current) => {
            if (!current) return current;
            return { ...current, data: recipe(current.data || {}) };
        });
    };

    const saveAll = async () => {
        if (!envelope) return;
        setSaving(true);
        try {
            const next = await saveConfigDomain<SystemBaseData>("system-base", {
                data: envelope.data,
            });
            setEnvelope(next);
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
        } finally {
            setSaving(false);
        }
    };

    const headscaleRequest = async (path: string, init?: RequestInit) => {
        const response = await fetch(`/api/network-supervisor/headscale${path}`, {
            cache: "no-store",
            ...init,
            headers: {
                ...(init?.body ? { "Content-Type": "application/json" } : {}),
                ...(init?.headers || {}),
            },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = payload?.detail;
            throw new Error(
                typeof detail === "string"
                    ? detail
                    : detail?.failureClass || payload?.error || "Headscale request failed",
            );
        }
        return payload;
    };

    const refreshHeadscale = async () => {
        setHeadscaleBusy(true);
        setHeadscaleMessage("");
        try {
            const [status, users, nodes, preauthKeys] = await Promise.all([
                headscaleRequest("/status").catch((error) => ({ ok: false, error: error instanceof Error ? error.message : String(error) })),
                headscaleRequest("/users").catch(() => ({ items: [] })),
                headscaleRequest("/nodes").catch(() => ({ items: [] })),
                headscaleRequest("/preauthkeys").catch(() => ({ items: [] })),
            ]);
            setHeadscaleData({
                status,
                users: Array.isArray(users.items) ? users.items : [],
                nodes: Array.isArray(nodes.items) ? nodes.items : [],
                preauthKeys: Array.isArray(preauthKeys.items) ? preauthKeys.items : [],
            });
        } finally {
            setHeadscaleBusy(false);
        }
    };

    const saveHeadscaleApiKey = async () => {
        setHeadscaleBusy(true);
        setHeadscaleMessage("");
        try {
            await headscaleRequest("/api-key", {
                method: "POST",
                body: JSON.stringify({ apiKey: headscaleApiKey }),
            });
            setHeadscaleApiKey("");
            setHeadscaleMessage(t("app.admin.dashboard.system.base.remoteLink.headscaleKeySaved"));
            await loadData();
            await refreshHeadscale();
        } finally {
            setHeadscaleBusy(false);
        }
    };

    const clearHeadscaleApiKey = async () => {
        if (!window.confirm(t("app.admin.dashboard.system.base.remoteLink.headscaleClearConfirm"))) return;
        setHeadscaleBusy(true);
        try {
            await headscaleRequest("/api-key", { method: "DELETE" });
            setHeadscaleMessage(t("app.admin.dashboard.system.base.remoteLink.headscaleKeyCleared"));
            await loadData();
            setHeadscaleData({});
        } finally {
            setHeadscaleBusy(false);
        }
    };

    const createHeadscalePreauthKey = async () => {
        setHeadscaleBusy(true);
        setHeadscaleMessage("");
        try {
            const payload = await headscaleRequest("/preauthkeys", {
                method: "POST",
                body: JSON.stringify({ user: headscaleUserId, ttlMinutes: headscaleTtlMinutes, reusable: false, ephemeral: false }),
            });
            setHeadscaleData((current) => ({ ...current, createdPreauthKey: payload.preAuthKey || null }));
            setHeadscaleMessage(t("app.admin.dashboard.system.base.remoteLink.headscalePreauthCreated"));
            await refreshHeadscale();
        } finally {
            setHeadscaleBusy(false);
        }
    };

    const dangerousHeadscaleNodeAction = async (nodeId: string, action: "expire" | "delete") => {
        const label = action === "expire"
            ? t("app.admin.dashboard.system.base.remoteLink.headscaleExpireNode")
            : t("app.admin.dashboard.system.base.remoteLink.headscaleDeleteNode");
        if (!window.confirm(`${label}: ${nodeId}`)) return;
        setHeadscaleBusy(true);
        try {
            await headscaleRequest(`/nodes/${encodeURIComponent(nodeId)}${action === "expire" ? "/expire" : ""}?confirm=true`, {
                method: action === "delete" ? "DELETE" : "POST",
            });
            await refreshHeadscale();
        } finally {
            setHeadscaleBusy(false);
        }
    };

    const applyDetectedDesktopTools = () => {
        const detected = envelope?.data.detectedDesktopTools;
        if (!detected) return;
        updateData((current) => ({
            ...current,
            desktopTools: {
                ...(current.desktopTools || {}),
                tesseractPath: detected.tesseractPath || current.desktopTools?.tesseractPath || "",
                tessdataPrefix: detected.tessdataPrefix || current.desktopTools?.tessdataPrefix || "",
            },
        }));
    };

    if (!envelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground/80" />
            </div>
        );
    }

    const bridge = envelope.data.bridge || {};
    const webFetch = envelope.data.webFetch || {};
    const desktopTools = envelope.data.desktopTools || {};
    const desktopLive = envelope.data.desktopLive || {};
    const remoteLink = envelope.data.remoteLink || {};
    const remoteLinkManifest = envelope.data.remoteLinkManifest || {};
    const remoteLinkMeshStatus = envelope.data.remoteLinkMeshStatus || {};
    const remoteLinkProfiles = remoteLink.transportProfiles || [];
    const meshProviders = remoteLink.meshProviders || [];
    const meshProviderStatuses = remoteLinkMeshStatus.providers || [];
    const meshPeerCandidates = remoteLinkMeshStatus.peerCandidates || remoteLinkManifest.peerCandidates || [];
    const headscaleCreatedKey = String(headscaleData.createdPreauthKey?.["key"] || "");
    const headscaleStatusError = String(headscaleData.status?.["error"] || "");
    const activeRemoteLinkProfile = remoteLinkProfiles.find((profile) => profile.id === remoteLink.activeProfileId) || remoteLinkProfiles[0] || {};
    const desktopLivePreset = deriveDesktopLivePreset(desktopLive);
    const s3 = envelope.data.s3 || {};
    const runtimeInfo = envelope.data.runtimeInfo || {};
    const desktopReadiness = envelope.data.desktopReadiness || {};
    const detectedDesktopTools = envelope.data.detectedDesktopTools || {};
    const dependencyStatus = envelope.data.dependencyStatus || [];
    const dependencyGroups = [
        { key: "core", title: "app.admin.dashboard.system.base.page.kfd39f914" },
        { key: "desktop", title: "app.admin.dashboard.system.base.page.k94b54d59" },
        { key: "automation", title: "app.admin.dashboard.system.base.page.k4451cb2c" },
        { key: "media", title: "app.admin.dashboard.system.base.page.kc5f157c7" },
    ] as const;

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={t("app.admin.dashboard.system.base.page.k4a7de926")}
                description={t("app.admin.dashboard.system.base.page.k82d86050")}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void saveAll()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.system.base.page.k6010e1ed")}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip items={summaryItems} />

            <div className="grid gap-4 xl:grid-cols-2">
                <ConfigCard
                    title={t("app.admin.dashboard.system.base.page.kba82f34b")}
                    description={t("app.admin.dashboard.system.base.page.kfaf1d8eb")}
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2 md:col-span-2">
                            <Label>{t("app.admin.dashboard.system.base.page.kef257227")}</Label>
                            <Input
                                value={bridge.engineBaseUrl || ""}
                                onChange={(event) =>
                                    updateData((current) => ({
                                        ...current,
                                        bridge: { ...(current.bridge || {}), engineBaseUrl: event.target.value },
                                    }))
                                }
                                placeholder="http://127.0.0.1:9530/v1"
                            />
                        </div>
                        <div className="space-y-2 md:col-span-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k146394c0")}</Label>
                            <Input
                                value={bridge.engineWsBaseUrl || ""}
                                onChange={(event) =>
                                    updateData((current) => ({
                                        ...current,
                                        bridge: { ...(current.bridge || {}), engineWsBaseUrl: event.target.value },
                                    }))
                                }
                                placeholder="ws://127.0.0.1:9530/v1"
                            />
                        </div>
                        <div className="space-y-2 md:col-span-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k8cee6195")}</Label>
                            <Input
                                value={bridge.adminBaseUrl || ""}
                                onChange={(event) =>
                                    updateData((current) => ({
                                        ...current,
                                        bridge: { ...(current.bridge || {}), adminBaseUrl: event.target.value },
                                    }))
                                }
                                placeholder="http://127.0.0.1:9528/api"
                            />
                            {!looksLikeLoopbackOrigin(bridge.adminBaseUrl) ? (
                                <div className="text-xs leading-5 text-muted-foreground">
                                    {t("app.admin.dashboard.system.base.page.k83edadde")}
                                </div>
                            ) : null}
                        </div>
                        <div className="space-y-2 md:col-span-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k9a85870c")}</Label>
                            <Input
                                type="password"
                                value={bridge.internalSecret || ""}
                                onChange={(event) =>
                                    updateData((current) => ({
                                        ...current,
                                        bridge: { ...(current.bridge || {}), internalSecret: event.target.value },
                                    }))
                                }
                                placeholder={t("app.admin.dashboard.system.base.page.k9fee3bb2")}
                            />
                        </div>
                        <div className="space-y-2 md:col-span-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k5d5ee22b")}</Label>
                            <Textarea
                                value={(bridge.allowedOrigins || []).join("\n")}
                                onChange={(event) =>
                                    updateData((current) => ({
                                        ...current,
                                        bridge: {
                                            ...(current.bridge || {}),
                                            allowedOrigins: normalizeOriginLines(event.target.value),
                                        },
                                    }))
                                }
                                placeholder={"http://localhost:9527\nhttp://localhost:9528\nhttps://your-web.example.com"}
                                className="min-h-[108px]"
                            />
                            <div className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.system.base.page.k7c90244a")}</div>
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title={t("app.admin.dashboard.system.base.remoteLink.title")}
                    description={t("app.admin.dashboard.system.base.remoteLink.description")}
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="space-y-4">
                        <SettingToggleCard
                            title={t("app.admin.dashboard.system.base.remoteLink.enabled")}
                            description={t("app.admin.dashboard.system.base.remoteLink.enabledHelp")}
                            checked={remoteLink.enabled !== false}
                            onCheckedChange={(checked) =>
                                updateData((current) => ({
                                    ...current,
                                    remoteLink: { ...(current.remoteLink || {}), enabled: checked },
                                }))
                            }
                            className="bg-muted/80 hover:bg-muted/80 rounded-2xl px-4 py-3"
                        />
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.system.base.remoteLink.activeProfile")}</Label>
                                <Select
                                    value={remoteLink.activeProfileId || activeRemoteLinkProfile.id || "manual-local"}
                                    onValueChange={(value) =>
                                        updateData((current) => ({
                                            ...current,
                                            remoteLink: { ...(current.remoteLink || {}), activeProfileId: value },
                                        }))
                                    }
                                >
                                    <SelectTrigger>
                                        <SelectValue placeholder={t("app.admin.dashboard.system.base.remoteLink.chooseProfile")} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {remoteLinkProfiles.map((profile) => (
                                            <SelectItem key={profile.id || profile.kind} value={profile.id || ""}>
                                                {profile.label || profile.id} · {transportLabel(profile.kind, t)}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.system.base.remoteLink.currentRoute")}</Label>
                                <div className="rounded-xl border border-border bg-card px-3 py-2 text-sm text-foreground">
                                    {transportLabel(remoteLinkManifest.transportKind || activeRemoteLinkProfile.kind, t)}
                                    {remoteLinkManifest.activeProfileId ? ` · ${remoteLinkManifest.activeProfileId}` : ""}
                                </div>
                            </div>
                        </div>
                        <div className="grid gap-4 md:grid-cols-3">
                            {(["adminBaseUrl", "engineBaseUrl", "peerBaseUrl"] as const).map((field) => (
                                <div key={field} className="space-y-2">
                                    <Label>
                                        {field === "adminBaseUrl"
                                            ? t("app.admin.dashboard.system.base.remoteLink.adminUrl")
                                            : field === "engineBaseUrl"
                                                ? t("app.admin.dashboard.system.base.remoteLink.engineUrl")
                                                : t("app.admin.dashboard.system.base.remoteLink.peerUrl")}
                                    </Label>
                                    <Input
                                        value={String(activeRemoteLinkProfile[field] || "")}
                                        onChange={(event) =>
                                            updateData((current) => {
                                                const currentRemote = current.remoteLink || {};
                                                const activeId = currentRemote.activeProfileId || activeRemoteLinkProfile.id || "manual-local";
                                                const nextProfiles = (currentRemote.transportProfiles || remoteLinkProfiles).map((profile) => (
                                                    profile.id === activeId ? { ...profile, [field]: event.target.value } : profile
                                                ));
                                                return {
                                                    ...current,
                                                    remoteLink: { ...currentRemote, transportProfiles: nextProfiles },
                                                };
                                            })
                                        }
                                        placeholder={field === "adminBaseUrl" ? "http://192.168.x.x:9528" : "http://192.168.x.x:9530"}
                                    />
                                </div>
                            ))}
                        </div>
                        <div className="rounded-2xl border border-border bg-card p-4 text-xs leading-5 text-muted-foreground">
                            <div className="mb-2 font-semibold text-foreground">{t("app.admin.dashboard.system.base.remoteLink.diagnostics")}</div>
                            <div>
                                {t("app.admin.dashboard.system.base.remoteLink.candidateIps")}{" "}
                                {(remoteLinkManifest.diagnostics?.candidateIps || []).map((item) => item.address).filter(Boolean).join(" · ")
                                    || t("app.admin.dashboard.system.base.page.k6ed9c299")}
                            </div>
                            <div>
                                WireGuard: {remoteLinkManifest.diagnostics?.vpn?.wireguardDetected ? t("app.admin.dashboard.system.base.page.k43d7227d") : t("app.admin.dashboard.system.base.page.k1f3ec640")}
                                {" · "}
                                Tailscale: {remoteLinkManifest.diagnostics?.vpn?.tailscaleDetected ? t("app.admin.dashboard.system.base.page.k43d7227d") : t("app.admin.dashboard.system.base.page.k1f3ec640")}
                            </div>
                            {(remoteLinkManifest.warnings || remoteLinkManifest.diagnostics?.warnings || []).length > 0 ? (
                                <div className="mt-2 text-amber-700">
                                    {t("app.admin.dashboard.system.base.remoteLink.warnings")}{" "}
                                    {(remoteLinkManifest.warnings || remoteLinkManifest.diagnostics?.warnings || []).slice(0, 4).join(" · ")}
                                </div>
                            ) : null}
                            {(remoteLinkManifest.diagnostics?.info || []).length > 0 ? (
                                <div className="mt-2 text-muted-foreground">
                                    {t("app.admin.dashboard.system.base.remoteLink.info")}{" "}
                                    {(remoteLinkManifest.diagnostics?.info || []).slice(0, 4).join(" · ")}
                                </div>
                            ) : null}
                            <div className="mt-2 text-muted-foreground">{t("app.admin.dashboard.system.base.remoteLink.readOnlyNotice")}</div>
                        </div>
                        <AdvancedSection
                            title={t("app.admin.dashboard.system.base.remoteLink.meshProviders")}
                            description={t("app.admin.dashboard.system.base.remoteLink.meshProvidersHelp")}
                            defaultOpen={false}
                        >
                            <div className="space-y-3">
                                <div className="rounded-2xl border border-border bg-muted/70 p-4">
                                    <div className="mb-3 flex items-center justify-between gap-3">
                                        <div>
                                            <div className="text-sm font-semibold text-foreground">{t("app.admin.dashboard.system.base.remoteLink.peerCandidates")}</div>
                                            <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.system.base.remoteLink.peerCandidatesHelp")}</div>
                                        </div>
                                        <span className="rounded-full bg-card px-2 py-1 text-xs font-semibold text-muted-foreground">{meshPeerCandidates.length}</span>
                                    </div>
                                    {meshPeerCandidates.length > 0 ? (
                                        <div className="grid gap-2">
                                            {meshPeerCandidates.slice(0, 8).map((candidate) => (
                                                <div key={candidate.id || candidate.peerBaseUrl || candidate.hostName} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-card px-3 py-2 text-xs">
                                                    <div className="min-w-0">
                                                        <div className="truncate font-semibold text-foreground">
                                                            {candidate.hostName || candidate.dnsName || candidate.ips?.[0] || t("app.admin.dashboard.system.base.remoteLink.unknownPeer")}
                                                        </div>
                                                        <div className="truncate text-muted-foreground">
                                                            {candidate.source} · {candidate.os || t("app.admin.dashboard.system.base.page.k6ed9c299")} · {candidate.peerBaseUrl || candidate.ips?.join(" · ")}
                                                        </div>
                                                    </div>
                                                    <div className={candidate.online ? "text-emerald-700" : "text-muted-foreground/80"}>
                                                        {candidate.online ? t("app.admin.dashboard.system.base.remoteLink.peerOnline") : t("app.admin.dashboard.system.base.remoteLink.peerOffline")}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="rounded-xl border border-dashed border-border bg-card px-3 py-3 text-xs text-muted-foreground">
                                            {t("app.admin.dashboard.system.base.remoteLink.noPeerCandidates")}
                                        </div>
                                    )}
                                </div>
                                {meshProviderStatuses.map((providerStatus) => {
                                    const providerConfig = meshProviders.find((item) => item.id === providerStatus.id || item.kind === providerStatus.kind) || {};
                                    return (
                                        <div key={providerStatus.id || providerStatus.kind} className="rounded-2xl border border-border bg-card p-4">
                                                <SettingToggleCard
                                                    title={
                                                        <div className="text-sm font-semibold text-foreground">
                                                            {providerStatus.kind === "headscale" ? "Headscale" : "Tailscale"}
                                                            <span className="ml-2 text-xs font-medium text-muted-foreground">{providerStatus.status || "unknown"}</span>
                                                        </div>
                                                    }
                                                    description={providerStatus.dnsName || providerStatus.addresses?.[0] || providerStatus.recommendedNextAction || t("app.admin.dashboard.system.base.remoteLink.noMeshAddress")}
                                                    checked={providerConfig.enabled !== false && providerStatus.enabled !== false}
                                                    onCheckedChange={(checked) =>
                                                        updateData((current) => {
                                                            const currentRemote = current.remoteLink || {};
                                                            const existing = currentRemote.meshProviders || meshProviders;
                                                            const targetId = providerStatus.id || providerStatus.kind || "";
                                                            const nextProviders = existing.map((provider) => (
                                                                (provider.id || provider.kind) === targetId
                                                                    ? { ...provider, enabled: checked, allowRouteMutation: false }
                                                                    : provider
                                                            ));
                                                            return {
                                                                ...current,
                                                                remoteLink: { ...currentRemote, meshProviders: nextProviders },
                                                            };
                                                        })
                                                    }
                                                    className="border-none bg-transparent hover:bg-transparent p-0 shadow-none gap-3 items-start"
                                                />
                                            {providerStatus.recommendedUrls ? (
                                                <div className="mt-3 grid gap-2 text-xs md:grid-cols-3">
                                                    <div className="rounded-xl bg-muted/50 px-3 py-2">
                                                        <div className="font-medium text-foreground">Admin</div>
                                                        <div className="break-all text-muted-foreground">{providerStatus.recommendedUrls.adminBaseUrl}</div>
                                                    </div>
                                                    <div className="rounded-xl bg-muted/50 px-3 py-2">
                                                        <div className="font-medium text-foreground">Engine</div>
                                                        <div className="break-all text-muted-foreground">{providerStatus.recommendedUrls.engineBaseUrl}</div>
                                                    </div>
                                                    <div className="rounded-xl bg-muted/50 px-3 py-2">
                                                        <div className="font-medium text-foreground">{t("app.admin.dashboard.system.base.remoteLink.peerUrl")}</div>
                                                        <div className="break-all text-muted-foreground">{providerStatus.recommendedUrls.peerBaseUrl}</div>
                                                    </div>
                                                </div>
                                            ) : null}
                                            {providerStatus.kind === "headscale" ? (
                                                <div className="mt-3 space-y-3">
                                                    <div className="grid gap-3 md:grid-cols-2">
                                                        <Input
                                                            value={String(providerConfig.controlUrl || "")}
                                                            onChange={(event) =>
                                                                updateData((current) => {
                                                                    const currentRemote = current.remoteLink || {};
                                                                    const existing = currentRemote.meshProviders || meshProviders;
                                                                    return {
                                                                        ...current,
                                                                        remoteLink: {
                                                                            ...currentRemote,
                                                                            meshProviders: existing.map((provider) => (
                                                                                (provider.id || provider.kind) === "headscale"
                                                                                    ? { ...provider, controlUrl: event.target.value, allowRouteMutation: false }
                                                                                    : provider
                                                                            )),
                                                                        },
                                                                    };
                                                                })
                                                            }
                                                            placeholder="https://headscale.example.com"
                                                        />
                                                        <Input
                                                            value={String(providerConfig.namespace || "")}
                                                            onChange={(event) =>
                                                                updateData((current) => {
                                                                    const currentRemote = current.remoteLink || {};
                                                                    const existing = currentRemote.meshProviders || meshProviders;
                                                                    return {
                                                                        ...current,
                                                                        remoteLink: {
                                                                            ...currentRemote,
                                                                            meshProviders: existing.map((provider) => (
                                                                                (provider.id || provider.kind) === "headscale"
                                                                                    ? { ...provider, namespace: event.target.value, allowRouteMutation: false }
                                                                                    : provider
                                                                            )),
                                                                        },
                                                                    };
                                                                })
                                                            }
                                                            placeholder={t("app.admin.dashboard.system.base.remoteLink.headscaleNamespace")}
                                                        />
                                                    </div>
                                                    <div className="rounded-xl border border-border bg-muted/80 p-3">
                                                        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                                                            <span className="font-semibold text-foreground">{t("app.admin.dashboard.system.base.remoteLink.headscaleApiKey")}</span>
                                                            <span className={providerStatus.apiKeyConfigured ? "text-emerald-700" : "text-amber-700"}>
                                                                {providerStatus.apiKeyConfigured
                                                                    ? `${t("app.admin.dashboard.system.base.remoteLink.configured")} · ${providerStatus.apiKeyFingerprint || ""}`
                                                                    : t("app.admin.dashboard.system.base.remoteLink.notConfigured")}
                                                            </span>
                                                        </div>
                                                        <div className="grid gap-2 md:grid-cols-[1fr_auto_auto_auto]">
                                                            <Input
                                                                type="password"
                                                                value={headscaleApiKey}
                                                                onChange={(event) => setHeadscaleApiKey(event.target.value)}
                                                                placeholder={t("app.admin.dashboard.system.base.remoteLink.headscaleApiKeyPlaceholder")}
                                                            />
                                                            <Button type="button" size="sm" onClick={() => void saveHeadscaleApiKey()} disabled={headscaleBusy || !headscaleApiKey.trim()}>
                                                                <KeyRound className="mr-2 h-4 w-4" />
                                                                {t("app.admin.dashboard.system.base.remoteLink.saveKey")}
                                                            </Button>
                                                            <Button type="button" size="sm" variant="outline" onClick={() => void refreshHeadscale()} disabled={headscaleBusy}>
                                                                <RefreshCw className="mr-2 h-4 w-4" />
                                                                {t("app.admin.dashboard.system.base.remoteLink.testHeadscale")}
                                                            </Button>
                                                            <Button type="button" size="sm" variant="outline" onClick={() => void clearHeadscaleApiKey()} disabled={headscaleBusy || !providerStatus.apiKeyConfigured}>
                                                                <Trash2 className="mr-2 h-4 w-4" />
                                                                {t("app.admin.dashboard.system.base.remoteLink.clearKey")}
                                                            </Button>
                                                        </div>
                                                        {headscaleMessage ? <div className="mt-2 text-xs text-emerald-700">{headscaleMessage}</div> : null}
                                                        {headscaleStatusError ? <div className="mt-2 text-xs text-amber-700">{headscaleStatusError}</div> : null}
                                                    </div>
                                                    <div className="rounded-xl border border-border bg-card p-3">
                                                        <div className="mb-2 text-xs font-semibold text-foreground">{t("app.admin.dashboard.system.base.remoteLink.createPreauthKey")}</div>
                                                        <div className="grid gap-2 md:grid-cols-[1fr_120px_auto]">
                                                            <Input value={headscaleUserId} onChange={(event) => setHeadscaleUserId(event.target.value)} placeholder={t("app.admin.dashboard.system.base.remoteLink.headscaleUserPlaceholder")} />
                                                            <Input type="number" min={5} max={43200} value={headscaleTtlMinutes} onChange={(event) => setHeadscaleTtlMinutes(Number(event.target.value || 60))} />
                                                            <Button type="button" size="sm" onClick={() => void createHeadscalePreauthKey()} disabled={headscaleBusy || !headscaleUserId.trim()}>
                                                                {t("app.admin.dashboard.system.base.remoteLink.createKey")}
                                                            </Button>
                                                        </div>
                                                        {headscaleCreatedKey ? (
                                                            <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                                                                <div className="font-semibold">{t("app.admin.dashboard.system.base.remoteLink.oneTimePreauthKey")}</div>
                                                                <div className="break-all font-mono">{headscaleCreatedKey}</div>
                                                            </div>
                                                        ) : null}
                                                    </div>
                                                    <div className="grid gap-3 text-xs md:grid-cols-3">
                                                        <div className="rounded-xl border border-border bg-muted/70 p-3">
                                                            <div className="mb-2 font-semibold text-foreground">{t("app.admin.dashboard.system.base.remoteLink.headscaleUsers")}</div>
                                                            {(headscaleData.users || []).slice(0, 6).map((item) => (
                                                                <div key={String(item["id"] || item["name"])} className="truncate text-muted-foreground">{String(item["name"] || item["id"])}</div>
                                                            ))}
                                                            {(headscaleData.users || []).length === 0 ? <div className="text-muted-foreground/80">{t("app.admin.dashboard.system.base.remoteLink.noHeadscaleData")}</div> : null}
                                                        </div>
                                                        <div className="rounded-xl border border-border bg-muted/70 p-3">
                                                            <div className="mb-2 font-semibold text-foreground">{t("app.admin.dashboard.system.base.remoteLink.headscaleNodes")}</div>
                                                            {(headscaleData.nodes || []).slice(0, 6).map((item) => {
                                                                const nodeId = String(item["id"] || item["nodeId"] || "");
                                                                return (
                                                                    <div key={nodeId || String(item["name"])} className="mb-2 rounded-lg bg-card px-2 py-2">
                                                                        <div className="truncate font-medium text-foreground">{String(item["name"] || item["givenName"] || nodeId)}</div>
                                                                        <div className="truncate text-muted-foreground/80">{String(item["ipAddresses"] || item["lastSeen"] || "")}</div>
                                                                        {nodeId ? (
                                                                            <div className="mt-2 flex gap-2">
                                                                                <Button type="button" size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => void dangerousHeadscaleNodeAction(nodeId, "expire")}>
                                                                                    {t("app.admin.dashboard.system.base.remoteLink.expire")}
                                                                                </Button>
                                                                                <Button type="button" size="sm" variant="outline" className="h-7 px-2 text-xs text-rose-700" onClick={() => void dangerousHeadscaleNodeAction(nodeId, "delete")}>
                                                                                    {t("app.admin.dashboard.system.base.remoteLink.delete")}
                                                                                </Button>
                                                                            </div>
                                                                        ) : null}
                                                                    </div>
                                                                );
                                                            })}
                                                            {(headscaleData.nodes || []).length === 0 ? <div className="text-muted-foreground/80">{t("app.admin.dashboard.system.base.remoteLink.noHeadscaleData")}</div> : null}
                                                        </div>
                                                        <div className="rounded-xl border border-border bg-muted/70 p-3">
                                                            <div className="mb-2 font-semibold text-foreground">{t("app.admin.dashboard.system.base.remoteLink.headscalePreauthKeys")}</div>
                                                            {(headscaleData.preauthKeys || []).slice(0, 6).map((item) => (
                                                                <div key={String(item["id"] || item["keyFingerprint"] || item["keyPrefix"])} className="truncate text-muted-foreground">
                                                                    {String(item["keyPrefix"] || item["keyFingerprint"] || item["id"])}
                                                                </div>
                                                            ))}
                                                            {(headscaleData.preauthKeys || []).length === 0 ? <div className="text-muted-foreground/80">{t("app.admin.dashboard.system.base.remoteLink.noHeadscaleData")}</div> : null}
                                                        </div>
                                                    </div>
                                                </div>
                                            ) : null}
                                        </div>
                                    );
                                })}
                            </div>
                        </AdvancedSection>
                        <div className="flex flex-wrap gap-2">
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => void navigator.clipboard?.writeText(remoteLinkManifest.admin?.baseUrl || bridge.adminBaseUrl || "")}
                            >
                                {t("app.admin.dashboard.system.base.remoteLink.copyAdmin")}
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => void navigator.clipboard?.writeText(remoteLinkManifest.engine?.baseUrl || bridge.engineBaseUrl || "")}
                            >
                                {t("app.admin.dashboard.system.base.remoteLink.copyEngine")}
                            </Button>
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title={t("app.admin.dashboard.system.base.page.kf79a66a7")}
                    description={t("app.admin.dashboard.system.base.page.k6538874e")}
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="space-y-4">
                        <SettingToggleCard
                            title={t("app.admin.dashboard.system.base.page.k628c31f2")}
                            description={t("app.admin.dashboard.system.base.page.ka8d7f9a8")}
                            checked={Boolean(webFetch.bypassProxyEnv)}
                            onCheckedChange={(checked) =>
                                updateData((current) => ({
                                    ...current,
                                    webFetch: { ...(current.webFetch || {}), bypassProxyEnv: checked },
                                }))
                            }
                            className="bg-muted/80 hover:bg-muted/80 rounded-2xl px-4 py-3"
                        />
                        <SettingToggleCard
                            title={t("app.admin.dashboard.system.base.webFetch.agentProfile.title")}
                            description={t("app.admin.dashboard.system.base.webFetch.agentProfile.description")}
                            checked={Boolean(webFetch.useAgentBrowserProfile)}
                            onCheckedChange={(checked) =>
                                updateData((current) => ({
                                    ...current,
                                    webFetch: { ...(current.webFetch || {}), useAgentBrowserProfile: checked },
                                }))
                            }
                            className="bg-muted/80 hover:bg-muted/80 rounded-2xl px-4 py-3"
                        />
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.system.base.webFetch.agentProfile.allowlist")}</Label>
                            <Textarea
                                value={(webFetch.agentBrowserProfileAllowlist || []).join("\n")}
                                onChange={(event) =>
                                    updateData((current) => ({
                                        ...current,
                                        webFetch: {
                                            ...(current.webFetch || {}),
                                            agentBrowserProfileAllowlist: event.target.value
                                                .split(/\r?\n|,/)
                                                .map((item) => item.trim())
                                                .filter(Boolean),
                                        },
                                    }))
                                }
                                placeholder={"github.com\nnotion.so\nmetaso.cn"}
                                className="min-h-[96px]"
                            />
                            <div className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.system.base.webFetch.agentProfile.allowlistHelp")}</div>
                        </div>
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k2bd98420")}</Label>
                            <Input
                                value={webFetch.cacheDir || ""}
                                onChange={(event) =>
                                    updateData((current) => ({
                                        ...current,
                                        webFetch: { ...(current.webFetch || {}), cacheDir: event.target.value },
                                    }))
                                }
                            />
                        </div>
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k0b6261c6")}</Label>
                            <Input
                                value={webFetch.adaptiveStorageFile || ""}
                                onChange={(event) =>
                                    updateData((current) => ({
                                        ...current,
                                        webFetch: { ...(current.webFetch || {}), adaptiveStorageFile: event.target.value },
                                    }))
                                }
                            />
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title={t("app.admin.dashboard.system.base.page.kc963695d")}
                    description={t("app.admin.dashboard.system.base.page.k59f74f45")}
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="space-y-4">
                        <div className={`rounded-2xl border px-4 py-4 ${desktopStatusTone(desktopReadiness.status)}`}>
                            <div className="flex flex-wrap items-start justify-between gap-3">
                                <div className="space-y-2">
                                    <div className="flex items-center gap-2 text-sm font-semibold">
                                        {desktopReadiness.status === "ready" ? (
                                            <CheckCircle2 className="h-4 w-4" />
                                        ) : (
                                            <AlertTriangle className="h-4 w-4" />
                                        )}
                                        {t("app.admin.dashboard.system.base.page.k72053b6d")}{desktopStatusLabel(desktopReadiness.status, t)}
                                    </div>
                                    <p className="text-xs leading-5">
                                        {desktopReadiness.missingItems && desktopReadiness.missingItems.length > 0
                                            ? desktopReadiness.missingItems.join("，")
                                            : t("app.admin.dashboard.system.base.page.kcde456ba")}
                                    </p>
                                </div>
                                <Button type="button" variant="outline" size="sm" onClick={applyDetectedDesktopTools}>
                                    {t("app.admin.dashboard.system.base.page.k735a8050")}
                                </Button>
                            </div>
                            <div className="mt-4 grid gap-2 text-xs md:grid-cols-2">
                                <div className="rounded-xl border border-current/10 bg-card/70 px-3 py-2">
                                    {t("app.admin.dashboard.system.base.page.k7463473f")}{desktopReadiness.ocrReady ? t("app.admin.dashboard.system.base.page.k43d7227d") : t("app.admin.dashboard.system.base.page.k1a83bbab")}
                                </div>
                                <div className="rounded-xl border border-current/10 bg-card/70 px-3 py-2">
                                    {t("app.admin.dashboard.system.base.page.k7ea27759")}{desktopReadiness.imageLocatorReady ? t("app.admin.dashboard.system.base.page.k43d7227d") : t("app.admin.dashboard.system.base.page.k1a83bbab")}
                                </div>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-border bg-muted/70 p-4">
                            <div className="mb-3 text-sm font-medium text-foreground">{t("app.admin.dashboard.system.base.page.ke4f6362c")}</div>
                            <div className="grid gap-3 text-xs text-muted-foreground md:grid-cols-2">
                                <div className="space-y-1">
                                    <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground/80">Tesseract</div>
                                    <div className="break-all">{detectedDesktopTools.tesseractPath || t("app.admin.dashboard.system.base.page.k1f3ec640")}</div>
                                </div>
                                <div className="space-y-1">
                                    <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground/80">Tessdata</div>
                                    <div className="break-all">{detectedDesktopTools.tessdataPrefix || t("app.admin.dashboard.system.base.page.k1f3ec640")}</div>
                                </div>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
                            <div className="mb-4 space-y-1">
                                <div className="text-sm font-medium text-foreground">{t("app.admin.dashboard.system.base.page.k2aee346c")}</div>
                                <div className="text-xs leading-5 text-muted-foreground">
                                    {t("app.admin.dashboard.system.base.page.k09d2d940")}
                                </div>
                            </div>
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.system.base.page.k29298ccf")}</Label>
                                    <Input
                                        value={desktopTools.tesseractPath || ""}
                                        onChange={(event) =>
                                            updateData((current) => ({
                                                ...current,
                                                desktopTools: { ...(current.desktopTools || {}), tesseractPath: event.target.value },
                                            }))
                                        }
                                        placeholder="C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
                                    />
                                    <div className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.system.base.page.kf4359c09")}</div>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.system.base.page.k7e1cdc13")}</Label>
                                    <Input
                                        value={desktopTools.tessdataPrefix || ""}
                                        onChange={(event) =>
                                            updateData((current) => ({
                                                ...current,
                                                desktopTools: { ...(current.desktopTools || {}), tessdataPrefix: event.target.value },
                                            }))
                                        }
                                        placeholder="C:\\Program Files\\Tesseract-OCR\\tessdata"
                                    />
                                    <div className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.system.base.page.k55b9f21a")}</div>
                                </div>
                            </div>
                        </div>

                    </div>
                </ConfigCard>

                <ConfigCard
                    title={t("app.admin.dashboard.system.base.page.ka8b76bc7")}
                    description={t("app.admin.dashboard.system.base.page.k94619878")}
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="space-y-4">
                        <SettingToggleCard
                            title={t("app.admin.dashboard.system.base.page.k93bfc0dd")}
                            description={t("app.admin.dashboard.system.base.page.k2fc16829")}
                            checked={Boolean(desktopLive.enabled)}
                            onCheckedChange={(checked) =>
                                updateData((current) => ({
                                    ...current,
                                    desktopLive: { ...(current.desktopLive || {}), enabled: checked },
                                }))
                            }
                            className="bg-muted/80 hover:bg-muted/80 rounded-2xl px-4 py-3"
                        />
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k41d6637a")}</Label>
                            <Select
                                value={desktopLivePreset}
                                onValueChange={(value) => {
                                    if (value === "custom") return;
                                    const preset = DESKTOP_LIVE_PRESETS.find((item) => item.id === value);
                                    if (!preset) return;
                                    updateData((current) => ({
                                        ...current,
                                        desktopLive: {
                                            ...(current.desktopLive || {}),
                                            maxWidth: preset.values.maxWidth,
                                            maxHeight: preset.values.maxHeight,
                                            targetFps: preset.values.targetFps,
                                        },
                                    }));
                                }}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder={t("app.admin.dashboard.system.base.page.k5766cb07")} />
                                </SelectTrigger>
                                <SelectContent>
                                    {DESKTOP_LIVE_PRESETS.map((preset) => (
                                        <SelectItem key={preset.id} value={preset.id}>
                                            {t(preset.label)} · {preset.summary}
                                        </SelectItem>
                                    ))}
                                    <SelectItem value="custom">{t("app.admin.dashboard.system.base.page.kf1007633")}</SelectItem>
                                </SelectContent>
                            </Select>
                            <div className="text-xs leading-5 text-muted-foreground">
                                {desktopLivePreset === "custom"
                                    ? t("app.admin.dashboard.system.base.page.k3130cb28")
                                    : t(DESKTOP_LIVE_PRESETS.find((preset) => preset.id === desktopLivePreset)?.description || "app.admin.dashboard.system.base.page.k6a1fa3c3")}
                            </div>
                        </div>

                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.system.base.page.k2170fc7e")}</Label>
                                <Input
                                    type="number"
                                    min={5}
                                    step={1}
                                    value={desktopLive.idleReleaseSeconds ?? 15}
                                    onChange={(event) =>
                                        updateData((current) => ({
                                            ...current,
                                            desktopLive: {
                                                ...(current.desktopLive || {}),
                                                idleReleaseSeconds: Number(event.target.value || 15),
                                            },
                                        }))
                                    }
                                />
                                <div className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.system.base.page.k4d40211f")}</div>
                            </div>
                            <div className="space-y-2">
                                <SettingToggleCard
                                    title={t("app.admin.dashboard.system.base.page.k2171c5c0")}
                                    description={t("app.admin.dashboard.system.base.page.k7812276c")}
                                    checked={Boolean(desktopLive.singleViewerOnly ?? true)}
                                    onCheckedChange={(checked) =>
                                        updateData((current) => ({
                                            ...current,
                                            desktopLive: {
                                                ...(current.desktopLive || {}),
                                                singleViewerOnly: checked,
                                            },
                                        }))
                                    }
                                    className="bg-muted/80 hover:bg-muted/80 rounded-2xl px-4 py-3"
                                />
                            </div>
                        </div>

                        <AdvancedSection
                            title={t("app.admin.dashboard.system.base.page.kc7749ff9")}
                            description={t("app.admin.dashboard.system.base.page.k8ca2d576")}
                            defaultOpen={desktopLivePreset === "custom"}
                        >
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.system.base.page.ked9ec1b1")}</Label>
                                    <Input
                                        type="number"
                                        min={320}
                                        step={10}
                                        value={desktopLive.maxWidth ?? 960}
                                        onChange={(event) =>
                                            updateData((current) => ({
                                                ...current,
                                                desktopLive: {
                                                    ...(current.desktopLive || {}),
                                                    maxWidth: Number(event.target.value || 960),
                                                },
                                            }))
                                        }
                                    />
                                    <div className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.system.base.page.kfa8d1901")}</div>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.system.base.page.k75781af8")}</Label>
                                    <Input
                                        type="number"
                                        min={180}
                                        step={10}
                                        value={desktopLive.maxHeight ?? 540}
                                        onChange={(event) =>
                                            updateData((current) => ({
                                                ...current,
                                                desktopLive: {
                                                    ...(current.desktopLive || {}),
                                                    maxHeight: Number(event.target.value || 540),
                                                },
                                            }))
                                        }
                                    />
                                    <div className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.system.base.page.k8d1bca52")}</div>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.system.base.page.k557fa0a6")}</Label>
                                    <Input
                                        type="number"
                                        min={1}
                                        max={15}
                                        step={1}
                                        value={desktopLive.targetFps ?? 10}
                                        onChange={(event) =>
                                            updateData((current) => ({
                                                ...current,
                                                desktopLive: {
                                                    ...(current.desktopLive || {}),
                                                    targetFps: Number(event.target.value || 10),
                                                },
                                            }))
                                        }
                                    />
                                    <div className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.system.base.page.k9e79b22c")}</div>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.system.base.page.k748dc31b")}</Label>
                                    <Input
                                        value={desktopLive.captureDisplay || "primary"}
                                        onChange={(event) =>
                                            updateData((current) => ({
                                                ...current,
                                                desktopLive: {
                                                    ...(current.desktopLive || {}),
                                                    captureDisplay: event.target.value || "primary",
                                                },
                                            }))
                                        }
                                        placeholder="primary"
                                    />
                                    <div className="text-xs leading-5 text-muted-foreground">{t("app.admin.dashboard.system.base.page.kcf10d4e8")}</div>
                                </div>
                            </div>
                        </AdvancedSection>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title={t("app.admin.dashboard.system.base.page.k5fd0ec33")}
                    description={t("app.admin.dashboard.system.base.page.k558ec5cd")}
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k3681630a")}</Label>
                            <Input value={s3.endpoint || ""} onChange={(event) => updateData((current) => ({ ...current, s3: { ...(current.s3 || {}), endpoint: event.target.value } }))} />
                        </div>
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.system.base.page.ka2a5cb11")}</Label>
                            <Input value={s3.region || ""} onChange={(event) => updateData((current) => ({ ...current, s3: { ...(current.s3 || {}), region: event.target.value } }))} />
                        </div>
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k481b3fd7")}</Label>
                            <Input value={s3.bucket || ""} onChange={(event) => updateData((current) => ({ ...current, s3: { ...(current.s3 || {}), bucket: event.target.value } }))} />
                        </div>
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k67403af0")}</Label>
                            <Input value={s3.accessKeyId || ""} onChange={(event) => updateData((current) => ({ ...current, s3: { ...(current.s3 || {}), accessKeyId: event.target.value } }))} />
                        </div>
                        <div className="space-y-2 md:col-span-2">
                            <Label>{t("app.admin.dashboard.system.base.page.k48562cc9")}</Label>
                            <Input type="password" value={s3.secretAccessKey || ""} onChange={(event) => updateData((current) => ({ ...current, s3: { ...(current.s3 || {}), secretAccessKey: event.target.value } }))} />
                        </div>
                    </div>
                </ConfigCard>
            </div>

            <ConfigCard
                title={t("app.admin.dashboard.system.base.page.ka4c0a095")}
                description={t("app.admin.dashboard.system.base.page.k6cd110b1")}
                bodyHeight="clamp"
                bodyScroll="auto"
            >
                <div className="space-y-5">
                    {dependencyGroups.map((group) => {
                        const items = dependencyStatus.filter((item) => item.category === group.key);
                        if (items.length === 0) return null;
                        return (
                            <div key={group.key} className="space-y-3">
                                <div className="text-sm font-semibold text-foreground">{t(group.title)}</div>
                                <div className="grid gap-3 lg:grid-cols-2">
                                    {items.map((item) => (
                                        <div key={item.id} className="rounded-2xl border border-border bg-muted/70 px-4 py-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="text-sm font-semibold text-foreground">{item.label}</div>
                                                <span className="rounded-full bg-card px-2.5 py-1 text-[11px] font-medium text-muted-foreground ring-1 ring-slate-200">
                                                    {resolveAdminLabel(t, "dependencyRequiredness", item.requiredness)}
                                                </span>
                                            </div>
                                            <div className="mt-3 space-y-1.5 text-xs leading-5 text-muted-foreground">
                                                <div>{t("app.admin.dashboard.system.base.page.ka5c77bf6")}{item.currentPlatform || "unknown"} · {t("app.admin.dashboard.system.base.page.k8fa02dc0")}{(item.platforms || []).join(" / ")}</div>
                                                <div>{t("app.admin.dashboard.system.base.page.k2fcd0729")}{item.detection?.detected ? t("app.admin.dashboard.system.base.page.k61e9a74e") : item.appliesToCurrentPlatform ? t("app.admin.dashboard.system.base.page.k0042c6be") : t("app.admin.dashboard.system.base.page.k2ff317a4")}</div>
                                                <div>{t("app.admin.dashboard.system.base.page.k80d16f4b")}{(item.usedBy || []).join(" / ") || t("app.admin.dashboard.system.base.page.kdc45dcdf")}</div>
                                                <div>{item.installHint || t("app.admin.dashboard.system.base.page.k69f3de32")}</div>
                                                {item.detection?.detail ? <div className="text-muted-foreground">{item.detection.detail}</div> : null}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </ConfigCard>

            <AdvancedSection title={t("app.admin.dashboard.system.base.page.kd507ab95")} description={t("app.admin.dashboard.system.base.page.k6f194618")}>
                <div className="grid gap-4 lg:grid-cols-3">
                    <ConfigCard title={t("app.admin.dashboard.system.base.page.ka0dc81c2")} description={t("app.admin.dashboard.system.base.page.k919153c5")} variant="summary">
                        <div className="space-y-3 text-sm text-muted-foreground">
                            <div className="flex items-center gap-2"><Server className="h-4 w-4 text-sky-600" />{t("app.admin.dashboard.system.base.page.k942f7326")}{runtimeInfo.engineHost || "0.0.0.0"}</div>
                            <div>{t("app.admin.dashboard.system.base.page.k68572957")}{runtimeInfo.enginePort || 9530}</div>
                            <div>{t("app.admin.dashboard.system.base.page.kb1b5bb9c")}{runtimeInfo.engineReload ? t("app.admin.dashboard.system.base.page.kd945d5d0") : t("app.admin.dashboard.system.base.page.k12b31ba6")}</div>
                        </div>
                    </ConfigCard>
                    <ConfigCard title={t("app.admin.dashboard.system.base.page.k451d04f4")} description={t("app.admin.dashboard.system.base.page.k618f081f")} variant="summary">
                        <div className="space-y-3 text-sm text-muted-foreground">
                            <div className="flex items-center gap-2"><Shield className="h-4 w-4 text-sky-600" />{t("app.admin.dashboard.system.base.page.k5d4eac31")}</div>
                            <div>{t("app.admin.dashboard.system.base.page.k7a2fb015")}config.json</div>
                            <div>{t("app.admin.dashboard.system.base.page.k716ae2c4")}{bridge.adminBaseUrl || t("app.admin.dashboard.system.base.page.k6ed9c299")}</div>
                        </div>
                    </ConfigCard>
                    <ConfigCard title={t("app.admin.dashboard.system.base.page.k29f66eda")} description={t("app.admin.dashboard.system.base.page.kd63c68c8")} variant="summary">
                        <div className="space-y-3 text-sm text-muted-foreground">
                            <div className="flex items-center gap-2"><Wrench className="h-4 w-4 text-sky-600" />{t("app.admin.dashboard.system.base.page.kd12a1540")}{desktopStatusLabel(desktopReadiness.status, t)}</div>
                            <div>{t("app.admin.dashboard.system.base.page.k7463473f")}{desktopReadiness.ocrReady ? t("app.admin.dashboard.system.base.page.kca64360a") : t("app.admin.dashboard.system.base.page.k1a83bbab")}</div>
                            <div>{t("app.admin.dashboard.system.base.page.kb9740fdc")}{desktopReadiness.pointLocatorReady ? t("app.admin.dashboard.system.base.page.kca64360a") : t("app.admin.dashboard.system.base.page.k1a83bbab")}</div>
                        </div>
                    </ConfigCard>
                </div>
            </AdvancedSection>

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />
        </AdminPageShell>
    );
}
