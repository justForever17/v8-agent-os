"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Save, Shield, Server, Wrench } from "lucide-react";

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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";

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

function requirednessLabel(value: string | undefined, t: (value: string) => string) {
    if (value === "required") return t("app.admin.dashboard.system.base.page.k0b1e02c7");
    if (value === "conditional") return t("app.admin.dashboard.system.base.page.k32a81d87");
    return t("app.admin.dashboard.system.base.page.k6f487abb");
}

export default function SystemBasePage() {
    const t = useT();
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<SystemBaseData> | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    const loadData = async () => {
        setLoading(true);
        try {
            const next = await fetchConfigDomain<SystemBaseData>("system-base");
            setEnvelope(next);
        } finally {
            setLoading(false);
        }
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

    if (loading || !envelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    const bridge = envelope.data.bridge || {};
    const webFetch = envelope.data.webFetch || {};
    const desktopTools = envelope.data.desktopTools || {};
    const desktopLive = envelope.data.desktopLive || {};
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
                            <div className="text-xs leading-5 text-slate-500">
                                {looksLikeLoopbackOrigin(bridge.adminBaseUrl)
                                    ? t("app.admin.dashboard.system.base.page.ka5970697")
                                    : t("app.admin.dashboard.system.base.page.k83edadde")}
                            </div>
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
                            <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.k7c90244a")}</div>
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
                        <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.system.base.page.k628c31f2")}</div>
                                <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.ka8d7f9a8")}</div>
                            </div>
                            <Switch
                                checked={Boolean(webFetch.bypassProxyEnv)}
                                onCheckedChange={(checked) =>
                                    updateData((current) => ({
                                        ...current,
                                        webFetch: { ...(current.webFetch || {}), bypassProxyEnv: checked },
                                    }))
                                }
                            />
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
                                <div className="rounded-xl border border-current/10 bg-white/70 px-3 py-2">
                                    {t("app.admin.dashboard.system.base.page.k7463473f")}{desktopReadiness.ocrReady ? t("app.admin.dashboard.system.base.page.k43d7227d") : t("app.admin.dashboard.system.base.page.k1a83bbab")}
                                </div>
                                <div className="rounded-xl border border-current/10 bg-white/70 px-3 py-2">
                                    {t("app.admin.dashboard.system.base.page.k7ea27759")}{desktopReadiness.imageLocatorReady ? t("app.admin.dashboard.system.base.page.k43d7227d") : t("app.admin.dashboard.system.base.page.k1a83bbab")}
                                </div>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                            <div className="mb-3 text-sm font-medium text-slate-900">{t("app.admin.dashboard.system.base.page.ke4f6362c")}</div>
                            <div className="grid gap-3 text-xs text-slate-600 md:grid-cols-2">
                                <div className="space-y-1">
                                    <div className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Tesseract</div>
                                    <div className="break-all">{detectedDesktopTools.tesseractPath || t("app.admin.dashboard.system.base.page.k1f3ec640")}</div>
                                </div>
                                <div className="space-y-1">
                                    <div className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Tessdata</div>
                                    <div className="break-all">{detectedDesktopTools.tessdataPrefix || t("app.admin.dashboard.system.base.page.k1f3ec640")}</div>
                                </div>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                            <div className="mb-4 space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.system.base.page.k2aee346c")}</div>
                                <div className="text-xs leading-5 text-slate-500">
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
                                    <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.kf4359c09")}</div>
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
                                    <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.k55b9f21a")}</div>
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
                        <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.system.base.page.k93bfc0dd")}</div>
                                <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.k2fc16829")}</div>
                            </div>
                            <Switch
                                checked={Boolean(desktopLive.enabled)}
                                onCheckedChange={(checked) =>
                                    updateData((current) => ({
                                        ...current,
                                        desktopLive: { ...(current.desktopLive || {}), enabled: checked },
                                    }))
                                }
                            />
                        </div>
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
                            <div className="text-xs leading-5 text-slate-500">
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
                                <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.k4d40211f")}</div>
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.system.base.page.kae23a462")}</Label>
                                <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                    <div className="space-y-1">
                                        <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.system.base.page.k2171c5c0")}</div>
                                        <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.k7812276c")}</div>
                                    </div>
                                    <Switch
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
                                    />
                                </div>
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
                                    <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.kfa8d1901")}</div>
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
                                    <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.k8d1bca52")}</div>
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
                                    <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.k9e79b22c")}</div>
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
                                    <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.system.base.page.kcf10d4e8")}</div>
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
                                <div className="text-sm font-semibold text-slate-900">{t(group.title)}</div>
                                <div className="grid gap-3 lg:grid-cols-2">
                                    {items.map((item) => (
                                        <div key={item.id} className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <div className="text-sm font-semibold text-slate-900">{item.label}</div>
                                                <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600 ring-1 ring-slate-200">
                                                    {requirednessLabel(item.requiredness, t)}
                                                </span>
                                            </div>
                                            <div className="mt-3 space-y-1.5 text-xs leading-5 text-slate-600">
                                                <div>{t("app.admin.dashboard.system.base.page.ka5c77bf6")}{item.currentPlatform || "unknown"} · {t("app.admin.dashboard.system.base.page.k8fa02dc0")}{(item.platforms || []).join(" / ")}</div>
                                                <div>{t("app.admin.dashboard.system.base.page.k2fcd0729")}{item.detection?.detected ? t("app.admin.dashboard.system.base.page.k61e9a74e") : item.appliesToCurrentPlatform ? t("app.admin.dashboard.system.base.page.k0042c6be") : t("app.admin.dashboard.system.base.page.k2ff317a4")}</div>
                                                <div>{t("app.admin.dashboard.system.base.page.k80d16f4b")}{(item.usedBy || []).join(" / ") || t("app.admin.dashboard.system.base.page.kdc45dcdf")}</div>
                                                <div>{item.installHint || t("app.admin.dashboard.system.base.page.k69f3de32")}</div>
                                                {item.detection?.detail ? <div className="text-slate-500">{item.detection.detail}</div> : null}
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
                        <div className="space-y-3 text-sm text-slate-600">
                            <div className="flex items-center gap-2"><Server className="h-4 w-4 text-sky-600" />{t("app.admin.dashboard.system.base.page.k942f7326")}{runtimeInfo.engineHost || "0.0.0.0"}</div>
                            <div>{t("app.admin.dashboard.system.base.page.k68572957")}{runtimeInfo.enginePort || 9530}</div>
                            <div>{t("app.admin.dashboard.system.base.page.kb1b5bb9c")}{runtimeInfo.engineReload ? t("app.admin.dashboard.system.base.page.kd945d5d0") : t("app.admin.dashboard.system.base.page.k12b31ba6")}</div>
                        </div>
                    </ConfigCard>
                    <ConfigCard title={t("app.admin.dashboard.system.base.page.k451d04f4")} description={t("app.admin.dashboard.system.base.page.k618f081f")} variant="summary">
                        <div className="space-y-3 text-sm text-slate-600">
                            <div className="flex items-center gap-2"><Shield className="h-4 w-4 text-sky-600" />{t("app.admin.dashboard.system.base.page.k5d4eac31")}</div>
                            <div>{t("app.admin.dashboard.system.base.page.k7a2fb015")}config.json</div>
                            <div>{t("app.admin.dashboard.system.base.page.k716ae2c4")}{bridge.adminBaseUrl || t("app.admin.dashboard.system.base.page.k6ed9c299")}</div>
                        </div>
                    </ConfigCard>
                    <ConfigCard title={t("app.admin.dashboard.system.base.page.k29f66eda")} description={t("app.admin.dashboard.system.base.page.kd63c68c8")} variant="summary">
                        <div className="space-y-3 text-sm text-slate-600">
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
