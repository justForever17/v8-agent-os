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
import { lt } from "@/lib/locale";

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

function formatEndpointSummary(value?: string, emptyLabel = "未设置") {
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
    if (status === "ready") return t("已就绪");
    if (status === "partial") return t("部分可用");
    return t("需要补充");
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
    if (normalized.length === 0) return t("仅本机默认地址");
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
        label: lt("流畅", "Smooth"),
        summary: "640 × 360 · 5 FPS",
        description: lt("更省资源，适合手机和局域网观察。", "Uses fewer resources and works well for phones or LAN viewing."),
        values: { maxWidth: 640, maxHeight: 360, targetFps: 5 },
    },
    {
        id: "balanced",
        label: lt("平衡", "Balanced"),
        summary: "960 × 540 · 10 FPS",
        description: lt("默认推荐，清晰度和流畅度比较均衡。", "Recommended default with a balanced mix of clarity and smoothness."),
        values: { maxWidth: 960, maxHeight: 540, targetFps: 10 },
    },
    {
        id: "clear",
        label: lt("清晰", "Clear"),
        summary: "1280 × 720 · 15 FPS",
        description: lt("画面更清楚，但更占用服务端和网络资源。", "Sharper output, but it consumes more server and network resources."),
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
    if (value === "required") return t("必须装");
    if (value === "conditional") return t("用到再装");
    return t("增强可选");
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
            { label: "引擎地址", value: formatEndpointSummary(bridge.engineBaseUrl, t("未设置")), description: "影响管理台和网页端连接引擎的地址。" },
            {
                label: "管理台地址",
                value: formatEndpointSummary(bridge.adminBaseUrl, t("未设置")),
                description: looksLikeLoopbackOrigin(bridge.adminBaseUrl)
                    ? t("当前是 loopback 地址，仅本机浏览器可用，手机预览会失效。")
                    : t("这是 Phone/Web 访问管理台资源与预览的公共地址。"),
            },
            {
                label: t("桌面依赖"),
                value: desktopStatusLabel(readiness?.status, t),
                description: missingItems.length > 0 ? missingItems.slice(0, 2).join("，") : t("影响 OCR 和桌面自动化能力。"),
            },
            { label: t("浏览器来源"), value: formatOriginsSummary(bridge.allowedOrigins, t), description: t("控制远端网页、局域网地址和反向代理域名的跨域访问。") },
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
        { key: "core", title: lt("核心必需", "Core required") },
        { key: "desktop", title: lt("桌面能力", "Desktop features") },
        { key: "automation", title: lt("流程自动化", "Automation") },
        { key: "media", title: lt("媒体与网页增强", "Media & web") },
    ] as const;

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={t("系统基础配置")}
                description={t("管理服务地址、内部密钥、抓取缓存和桌面依赖。")}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void saveAll()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t("保存")}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip items={summaryItems} />

            <div className="grid gap-4 xl:grid-cols-2">
                <ConfigCard
                    title={t("服务联通")}
                    description={t("这些字段会影响管理台、网页端和引擎连接。")}
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2 md:col-span-2">
                            <Label>{t("引擎 HTTP 地址")}</Label>
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
                            <Label>{t("引擎 WS 地址")}</Label>
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
                            <Label>{t("管理台 API 地址")}</Label>
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
                                    ? t("当前配置是 loopback，只能本机浏览器访问。Phone/Web 预览工作区图片、视频、音频时，请改成同局域网可达的地址，例如 http://192.168.x.x:9528/api。")
                                    : t("这里填写的是客户端可达的管理台公共地址。Phone/Web 会用它来生成资源预览链接，建议保持为局域网或公网可访问的 /api 地址。")}
                            </div>
                        </div>
                        <div className="space-y-2 md:col-span-2">
                            <Label>{t("内部密钥")}</Label>
                            <Input
                                type="password"
                                value={bridge.internalSecret || ""}
                                onChange={(event) =>
                                    updateData((current) => ({
                                        ...current,
                                        bridge: { ...(current.bridge || {}), internalSecret: event.target.value },
                                    }))
                                }
                                placeholder={t("自动生成")}
                            />
                        </div>
                        <div className="space-y-2 md:col-span-2">
                            <Label>{t("允许的浏览器来源")}</Label>
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
                            <div className="text-xs leading-5 text-slate-500">{t("每行填写一个来源地址。这里用于远端网页、局域网访问和反向代理域名，不影响引擎内部运行，只影响浏览器跨域访问。修改后需要重启 Engine 才会更新实际 CORS allowlist。")}</div>
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title={t("抓取与缓存")}
                    description={t("这些字段会影响抓取、代理绕过和缓存位置。")}
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="space-y-4">
                        <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t("绕过代理环境变量")}</div>
                                <div className="text-xs leading-5 text-slate-500">{t("打开后会忽略异常代理变量，直接访问网页。")}</div>
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
                            <Label>{t("缓存目录")}</Label>
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
                            <Label>{t("自适应存储文件")}</Label>
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
                    title={t(lt("桌面依赖", "Desktop stack"))}
                    description={t(lt("先配置 OCR 和桌面视觉基础依赖。", "Configure OCR and the core desktop vision dependencies."))}
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
                                        {t(lt("当前检测状态：", "Current status:"))}{desktopStatusLabel(desktopReadiness.status, t)}
                                    </div>
                                    <p className="text-xs leading-5">
                                        {desktopReadiness.missingItems && desktopReadiness.missingItems.length > 0
                                            ? desktopReadiness.missingItems.join("，")
                                            : t(lt("OCR、基础图像定位和点位能力已达到可用状态。", "OCR, image location, and point detection are ready."))}
                                    </p>
                                </div>
                                <Button type="button" variant="outline" size="sm" onClick={applyDetectedDesktopTools}>
                                    {t(lt("使用当前检测结果填充", "Use detected values"))}
                                </Button>
                            </div>
                            <div className="mt-4 grid gap-2 text-xs md:grid-cols-2">
                                <div className="rounded-xl border border-current/10 bg-white/70 px-3 py-2">
                                    {t(lt("OCR：", "OCR:"))}{desktopReadiness.ocrReady ? t("已就绪") : t(lt("未就绪", "Not ready"))}
                                </div>
                                <div className="rounded-xl border border-current/10 bg-white/70 px-3 py-2">
                                    {t(lt("图像定位：", "Image locator:"))}{desktopReadiness.imageLocatorReady ? t("已就绪") : t(lt("未就绪", "Not ready"))}
                                </div>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                            <div className="mb-3 text-sm font-medium text-slate-900">{t(lt("当前检测到的基础环境值", "Detected baseline values"))}</div>
                            <div className="grid gap-3 text-xs text-slate-600 md:grid-cols-2">
                                <div className="space-y-1">
                                    <div className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Tesseract</div>
                                    <div className="break-all">{detectedDesktopTools.tesseractPath || t(lt("未检测到", "Not detected"))}</div>
                                </div>
                                <div className="space-y-1">
                                    <div className="text-[11px] uppercase tracking-[0.18em] text-slate-400">Tessdata</div>
                                    <div className="break-all">{detectedDesktopTools.tessdataPrefix || t(lt("未检测到", "Not detected"))}</div>
                                </div>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                            <div className="mb-4 space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t(lt("基础依赖", "Core dependencies"))}</div>
                                <div className="text-xs leading-5 text-slate-500">
                                    {t(lt("先把这 2 项填对，OCR 和基础桌面视觉能力才能稳定工作。", "Set these two fields first so OCR and basic desktop vision can work reliably."))}
                                </div>
                            </div>
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t(lt("Tesseract 路径", "Tesseract path"))}</Label>
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
                                    <div className="text-xs leading-5 text-slate-500">{t(lt("填写 tesseract.exe 的完整路径，不是安装目录。", "Enter the full path to tesseract.exe, not just the install folder."))}</div>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t(lt("Tessdata 路径", "Tessdata path"))}</Label>
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
                                    <div className="text-xs leading-5 text-slate-500">{t(lt("填写语言包目录，里面通常有 eng.traineddata、chi_sim.traineddata。", "Enter the language data directory. It usually contains eng.traineddata and chi_sim.traineddata."))}</div>
                                </div>
                            </div>
                        </div>

                    </div>
                </ConfigCard>

                <ConfigCard
                    title={t(lt("桌面直播", "Desktop streaming"))}
                    description={t(lt("控制服务端桌面观察流的清晰度、流畅度和观看规则。这里只做观看，不支持远程操作。", "Control clarity, smoothness, and viewer rules for the server-side desktop stream. This is view-only and does not allow remote control."))}
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="space-y-4">
                        <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t(lt("启用桌面直播", "Enable desktop streaming"))}</div>
                                <div className="text-xs leading-5 text-slate-500">{t(lt("关闭后，网页端不会显示电脑观看入口，也不会创建直播会话。", "When disabled, the web app hides the desktop viewer entry and will not create live sessions."))}</div>
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
                            <Label>{t(lt("观看预设", "Viewing preset"))}</Label>
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
                                    <SelectValue placeholder={t(lt("选择一个观看预设", "Choose a preset"))} />
                                </SelectTrigger>
                                <SelectContent>
                                    {DESKTOP_LIVE_PRESETS.map((preset) => (
                                        <SelectItem key={preset.id} value={preset.id}>
                                            {t(preset.label)} · {preset.summary}
                                        </SelectItem>
                                    ))}
                                    <SelectItem value="custom">{t(lt("自定义", "Custom"))}</SelectItem>
                                </SelectContent>
                            </Select>
                            <div className="text-xs leading-5 text-slate-500">
                                {desktopLivePreset === "custom"
                                    ? t(lt("当前宽高或帧率已经被手动调整为自定义值。", "Width, height, or FPS have been manually adjusted to custom values."))
                                    : t(DESKTOP_LIVE_PRESETS.find((preset) => preset.id === desktopLivePreset)?.description || lt("默认推荐，清晰度和流畅度比较均衡。", "Recommended default with a balanced mix of clarity and smoothness."))}
                            </div>
                        </div>

                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t(lt("空闲释放秒数", "Idle release (seconds)"))}</Label>
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
                                <div className="text-xs leading-5 text-slate-500">{t(lt("用户关闭或断开后，这个时间内会自动回收会话和采集资源。", "After viewers close or disconnect, sessions and capture resources are reclaimed within this window."))}</div>
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("观看规则", "Viewer rules"))}</Label>
                                <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                    <div className="space-y-1">
                                        <div className="text-sm font-medium text-slate-900">{t(lt("仅允许单个观看者", "Single viewer only"))}</div>
                                        <div className="text-xs leading-5 text-slate-500">{t(lt("打开后，已有用户正在观看时，其他人会被拦截。", "When enabled, new viewers are blocked if someone is already watching."))}</div>
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
                            title={t(lt("高级设置", "Advanced"))}
                            description={t(lt("手动调整原始宽高、帧率和采集显示器。只有在预设不满足需求时再改这里。", "Tune raw width, height, FPS, and capture display only when presets are not enough."))}
                            defaultOpen={desktopLivePreset === "custom"}
                        >
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t(lt("最大宽度", "Max width"))}</Label>
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
                                    <div className="text-xs leading-5 text-slate-500">{t(lt("默认建议 960。越大越清晰，也越占资源。", "960 is the recommended default. Larger values improve clarity but cost more resources."))}</div>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t(lt("最大高度", "Max height"))}</Label>
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
                                    <div className="text-xs leading-5 text-slate-500">{t(lt("默认建议 540。用于限制竖屏和小屏时的编码负担。", "540 is the recommended default to keep portrait and small-screen encoding manageable."))}</div>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t(lt("目标帧率", "Target FPS"))}</Label>
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
                                    <div className="text-xs leading-5 text-slate-500">{t(lt("默认 10 FPS，足够兼顾观察流畅度和服务端负载，不建议盲目调高。", "10 FPS is the default sweet spot for smooth viewing without pushing server load too high."))}</div>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t(lt("采集显示器", "Capture display"))}</Label>
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
                                    <div className="text-xs leading-5 text-slate-500">{t(lt("当前版本只支持主显示器，保留这个字段是为了后续兼容多屏。", "The current release supports only the primary display. This field is kept for future multi-display support."))}</div>
                                </div>
                            </div>
                        </AdvancedSection>
                    </div>
                </ConfigCard>

                <ConfigCard
                    title={t(lt("对象存储", "Object storage"))}
                    description={t(lt("改这里会影响上传文件和外部资源访问。", "Changes here affect uploads and external asset access."))}
                    variant="editor"
                    bodyHeight="clamp"
                    bodyScroll="auto"
                >
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label>{t(lt("访问地址", "Endpoint"))}</Label>
                            <Input value={s3.endpoint || ""} onChange={(event) => updateData((current) => ({ ...current, s3: { ...(current.s3 || {}), endpoint: event.target.value } }))} />
                        </div>
                        <div className="space-y-2">
                            <Label>{t(lt("区域", "Region"))}</Label>
                            <Input value={s3.region || ""} onChange={(event) => updateData((current) => ({ ...current, s3: { ...(current.s3 || {}), region: event.target.value } }))} />
                        </div>
                        <div className="space-y-2">
                            <Label>{t(lt("Bucket", "Bucket"))}</Label>
                            <Input value={s3.bucket || ""} onChange={(event) => updateData((current) => ({ ...current, s3: { ...(current.s3 || {}), bucket: event.target.value } }))} />
                        </div>
                        <div className="space-y-2">
                            <Label>{t(lt("Access Key", "Access key"))}</Label>
                            <Input value={s3.accessKeyId || ""} onChange={(event) => updateData((current) => ({ ...current, s3: { ...(current.s3 || {}), accessKeyId: event.target.value } }))} />
                        </div>
                        <div className="space-y-2 md:col-span-2">
                            <Label>{t(lt("Secret Key", "Secret key"))}</Label>
                            <Input type="password" value={s3.secretAccessKey || ""} onChange={(event) => updateData((current) => ({ ...current, s3: { ...(current.s3 || {}), secretAccessKey: event.target.value } }))} />
                        </div>
                    </div>
                </ConfigCard>
            </div>

            <ConfigCard
                title={t(lt("外部依赖总表", "Dependency matrix"))}
                description={t(lt("查看依赖要求和检测结果。", "Review dependency requirements and detection results."))}
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
                                                <div>{t(lt("当前平台：", "Platform:"))}{item.currentPlatform || "unknown"} · {t(lt("适用平台：", "Applies to:"))}{(item.platforms || []).join(" / ")}</div>
                                                <div>{t(lt("检测状态：", "Detection:"))}{item.detection?.detected ? t(lt("已检测到", "Detected")) : item.appliesToCurrentPlatform ? t(lt("未检测到", "Missing")) : t(lt("当前平台不适用", "Not needed on this platform"))}</div>
                                                <div>{t(lt("影响范围：", "Used by:"))}{(item.usedBy || []).join(" / ") || t(lt("系统能力", "System features"))}</div>
                                                <div>{item.installHint || t(lt("这里会说明缺失时影响哪些功能。", "This section explains which features are affected when the dependency is missing."))}</div>
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

            <AdvancedSection title={t(lt("当前运行信息", "Runtime info"))} description={t(lt("这里只显示当前进程的启动参数，不会写入配置。", "These values are read from the current process and are not written back to config."))}>
                <div className="grid gap-4 lg:grid-cols-3">
                    <ConfigCard title={t(lt("当前引擎地址", "Current engine address"))} description={t(lt("显示当前进程的监听参数。", "Shows the live host and port."))} variant="summary">
                        <div className="space-y-3 text-sm text-slate-600">
                            <div className="flex items-center gap-2"><Server className="h-4 w-4 text-sky-600" />{t(lt("主机：", "Host:"))}{runtimeInfo.engineHost || "0.0.0.0"}</div>
                            <div>{t(lt("端口：", "Port:"))}{runtimeInfo.enginePort || 9530}</div>
                            <div>{t(lt("热重载：", "Reload:"))}{runtimeInfo.engineReload ? t("已开启") : t("已关闭")}</div>
                        </div>
                    </ConfigCard>
                    <ConfigCard title={t(lt("保护信息", "Protection info"))} description={t(lt("显示系统内部联通所需的关键信息。", "Shows key internal connectivity details."))} variant="summary">
                        <div className="space-y-3 text-sm text-slate-600">
                            <div className="flex items-center gap-2"><Shield className="h-4 w-4 text-sky-600" />{t(lt("内部密钥：已写入配置", "Internal secret: written to config"))}</div>
                            <div>{t(lt("保存文件：", "Saved in:"))}config.json</div>
                            <div>{t(lt("管理台地址：", "Admin URL:"))}{bridge.adminBaseUrl || t("未设置")}</div>
                        </div>
                    </ConfigCard>
                    <ConfigCard title={t(lt("桌面能力", "Desktop capability"))} description={t(lt("快速查看桌面依赖是否已配置。", "Quickly inspect desktop dependency status."))} variant="summary">
                        <div className="space-y-3 text-sm text-slate-600">
                            <div className="flex items-center gap-2"><Wrench className="h-4 w-4 text-sky-600" />{t("状态：")}{desktopStatusLabel(desktopReadiness.status, t)}</div>
                            <div>{t(lt("OCR：", "OCR:"))}{desktopReadiness.ocrReady ? t(lt("可用", "Ready")) : t(lt("未就绪", "Not ready"))}</div>
                            <div>{t(lt("点位能力：", "Point locator:"))}{desktopReadiness.pointLocatorReady ? t(lt("可用", "Ready")) : t(lt("未就绪", "Not ready"))}</div>
                        </div>
                    </ConfigCard>
                </div>
            </AdvancedSection>

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />
        </AdminPageShell>
    );
}
