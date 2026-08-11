"use client";

import { type KeyboardEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
    ProductShellTopbar,
    ProductSurfaceSwitcher,
    ProductTopbar,
    TopbarGlowActionButton,
} from "@v8/product-ui";
import { ArrowUpRight, Bell, CircleCheck, Loader2, Monitor, RefreshCw, Search, Wrench } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { searchAdminTopbarEntries } from "@/components/layout/admin-topbar-search";
import { LocaleToggle } from "@/components/layout/LocaleToggle";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { DeviceConnectDialog } from "@/components/admin/DeviceConnectDialog";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { getAdminNavItem } from "@/lib/admin-navigation";
import { fetchAdminJson, primeAdminJsonCache } from "@/lib/admin-client-cache";
import { cn } from "@/lib/utils";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { useDebugMode } from "@/lib/useDebugMode";
import { ShellWindowControls, type ShellUpdateStatus } from "./ShellWindowControls";

type InboxItem = {
    id: string;
    title: string;
    summary: string;
    severity: "error" | "warning" | "info";
    href: string;
    source: string;
};

type RuntimeFeaturePack = {
    id: string;
    productName: string;
    shortName: string;
    description: string;
    hover: string;
    recommendedOrder: number;
    runtimeFamilies: string[];
    status: "installed" | "not_installed" | "installing" | "failed";
    installed: boolean;
    installable: boolean;
    restartRequired: boolean;
    logName: string | null;
    hasError: boolean;
    executionProvider?: string | null;
    gpuAdapters?: string[];
};

type RuntimeFeaturePackState = {
    engineAvailable: boolean;
    refreshing?: boolean;
    retryAfterMs?: number | null;
    updatedAt?: number | null;
    packs: RuntimeFeaturePack[];
    summary: {
        total: number;
        installed: number;
        missing: number;
        installing: number;
        failed: number;
    };
};

type V8OSUpdateState = {
    status: "available" | "current" | "incompatible" | "unavailable";
    currentVersion: string | null;
    latestVersion: string | null;
    releaseUrl: string | null;
    checkedAt: string;
    action: "open_release_page";
};

function projectShellUpdateState(state: ShellUpdateStatus): V8OSUpdateState {
    const status = state.state === "available"
        ? "available"
        : state.state === "current"
            ? "current"
            : "unavailable";
    return {
        status,
        currentVersion: state.currentVersion || (state.state === "current" ? state.version || null : null),
        latestVersion: state.version || state.currentVersion || null,
        releaseUrl: state.releaseUrl || null,
        checkedAt: new Date().toISOString(),
        action: "open_release_page",
    };
}

type InboxPayload = {
    items?: InboxItem[];
    refreshing?: boolean;
    retryAfterMs?: number | null;
    updatedAt?: number | null;
};

const WEB_CHAT_SURFACE_URL = "http://localhost:9527/chat";
const V8OS_UPDATE_CACHE_TTL_MS = 5 * 60 * 1000;
const CONTROLLED_RELEASE_URL_RE = /^https:\/\/github\.com\/justForever17\/v8-agent-os\/releases\/tag\/v8-os-v20\d{2}\.(?:0[1-9]|1[0-2])\.(?:0[1-9]|[12]\d|3[01])\.(?:[1-9]|[1-9]\d)$/;

const subscribeToShellSurface = () => () => {};
const readShellSurface = () => Boolean(window.v8osShell?.isShell);
const readServerShellSurface = () => false;

function SeverityDot({ severity }: { severity: InboxItem["severity"] }) {
    return (
        <span
            className={cn(
                "inline-flex h-2.5 w-2.5 shrink-0 rounded-full",
                severity === "error" ? "bg-rose-500" : severity === "warning" ? "bg-amber-500" : "bg-sky-500",
            )}
            aria-hidden="true"
        />
    );
}

export function AdminTopbar({ windowControls }: { windowControls?: ReactNode }) {
    const pathname = usePathname();
    const router = useRouter();
    const current = getAdminNavItem(pathname);
    const t = useT();
    const { locale } = useLocale();
    const [debugMode, toggleDebugMode] = useDebugMode();
    const { toast } = useToast();
    const [activePanel, setActivePanel] = useState<"install" | "search" | "inbox" | null>(null);
    const [searchQuery, setSearchQuery] = useState("");
    const [activeSearchIndex, setActiveSearchIndex] = useState(0);
    const [inboxItems, setInboxItems] = useState<InboxItem[]>([]);
    const [seenInboxIds, setSeenInboxIds] = useState<Set<string>>(new Set());
    const [inboxLoading, setInboxLoading] = useState(false);
    const [inboxError, setInboxError] = useState<string | null>(null);
    const [inboxRefreshAttempt, setInboxRefreshAttempt] = useState(0);
    const [installState, setInstallState] = useState<RuntimeFeaturePackState | null>(null);
    const [installLoading, setInstallLoading] = useState(false);
    const [installSubmittingPackId, setInstallSubmittingPackId] = useState<string | null>(null);
    const [v8osUpdateState, setV8osUpdateState] = useState<V8OSUpdateState | null>(null);
    const [v8osUpdateLoading, setV8osUpdateLoading] = useState(false);
    const [v8osUpdateError, setV8osUpdateError] = useState(false);
    const isShell = useSyncExternalStore(subscribeToShellSurface, readShellSurface, readServerShellSurface);
    const searchContainerRef = useRef<HTMLDivElement | null>(null);
    const inboxContainerRef = useRef<HTMLDivElement | null>(null);
    const installContainerRef = useRef<HTMLDivElement | null>(null);
    const searchInputRef = useRef<HTMLInputElement | null>(null);
    const inboxLoadingRef = useRef(false);
    const pendingInboxForceRef = useRef(false);
    const v8osUpdateLoadingRef = useRef(false);

    const TopbarComponent = isShell ? ProductShellTopbar : ProductTopbar;
    const resolvedWindowControls = windowControls ?? (isShell ? <ShellWindowControls /> : undefined);
    const chatSurfaceItem = isShell
        ? {
            id: "chat",
            label: t("components.layout.Topbar.surface.chat"),
            onSelect: () => window.v8osShell?.openWeb(),
            title: t("components.layout.Topbar.surface.openChat"),
        }
        : {
            id: "chat",
            label: t("components.layout.Topbar.surface.chat"),
            href: WEB_CHAT_SURFACE_URL,
            title: t("components.layout.Topbar.surface.openChat"),
        };

    const searchResults = useMemo(
        () => searchAdminTopbarEntries(searchQuery, 8),
        [searchQuery],
    );
    const unreadInboxCount = useMemo(
        () => inboxItems.filter((item) => !seenInboxIds.has(item.id)).length,
        [inboxItems, seenInboxIds],
    );

    const loadInstallState = useCallback(async (force = false, silent = false, refreshHealth = false) => {
        if (!silent) {
            setInstallLoading(true);
        }
        try {
            const url = refreshHealth ? "/api/runtime-feature-packs?refresh=1" : "/api/runtime-feature-packs";
            const payload = await fetchAdminJson<RuntimeFeaturePackState>(url, { force });
            if (refreshHealth) {
                primeAdminJsonCache("/api/runtime-feature-packs", payload, 5_000);
            }
            setInstallState(payload);
        } catch (error) {
            console.error("Failed to load runtime feature pack state:", error);
            if (!silent) {
                toast({
                    title: t("components.layout.Topbar.featurePacksLoadFailedTitle"),
                    description: t("components.layout.Topbar.featurePacksLoadFailedDescription"),
                    variant: "destructive",
                });
            }
        } finally {
            if (!silent) {
                setInstallLoading(false);
            }
        }
    }, [t, toast]);

    const loadV8OSUpdateState = useCallback(async (force = false) => {
        if (v8osUpdateLoadingRef.current) return;
        v8osUpdateLoadingRef.current = true;
        setV8osUpdateLoading(true);
        setV8osUpdateError(false);
        try {
            const shell = window.v8osShell;
            if (shell?.isShell) {
                if (!shell.getUpdateStatus) {
                    throw new Error("shell_update_bridge_unavailable");
                }
                let shellState = await shell.getUpdateStatus();
                if ((force || shellState.state === "idle" || shellState.state === "checking") && shell.checkForUpdates) {
                    shellState = await shell.checkForUpdates();
                }
                const projected = projectShellUpdateState(shellState);
                setV8osUpdateState(projected);
                setV8osUpdateError(projected.status === "unavailable");
                return;
            }
            const url = force ? "/api/v8os-update?refresh=1" : "/api/v8os-update";
            const payload = await fetchAdminJson<V8OSUpdateState>(url, {
                force,
                ttlMs: V8OS_UPDATE_CACHE_TTL_MS,
            });
            if (force) {
                primeAdminJsonCache("/api/v8os-update", payload, V8OS_UPDATE_CACHE_TTL_MS);
            }
            setV8osUpdateState(payload);
            setV8osUpdateError(payload.status === "unavailable");
        } catch (error) {
            console.error("Failed to check V8OS release state:", error);
            setV8osUpdateError(true);
        } finally {
            v8osUpdateLoadingRef.current = false;
            setV8osUpdateLoading(false);
        }
    }, []);

    const closePanels = useCallback(() => {
        setActivePanel(null);
        setActiveSearchIndex(0);
    }, []);

    const navigateTo = useCallback((href: string) => {
        closePanels();
        setSearchQuery("");
        router.push(href);
    }, [closePanels, router, setSearchQuery]);

    const loadInbox = useCallback(async (silent = false, force = false, refreshHealth = false) => {
        if (inboxLoadingRef.current) {
            if (refreshHealth) {
                pendingInboxForceRef.current = true;
                if (!silent) setInboxLoading(true);
            }
            return;
        }
        inboxLoadingRef.current = true;
        if (!silent) {
            setInboxLoading(true);
        }
        let requestSilent = silent;
        let requestForce = force;
        let requestRefreshHealth = refreshHealth;
        try {
            while (true) {
                try {
                    const url = requestRefreshHealth ? "/api/admin-inbox?refresh=1" : "/api/admin-inbox";
                    const payload = await fetchAdminJson<InboxPayload>(url, {
                        force: requestForce,
                        ttlMs: 10_000,
                    });
                    if (requestRefreshHealth) {
                        primeAdminJsonCache("/api/admin-inbox", payload, 10_000);
                    }
                    setInboxItems(Array.isArray(payload.items) ? payload.items : []);
                    if (payload.refreshing) {
                        setInboxRefreshAttempt((current) => current + 1);
                    } else {
                        setInboxRefreshAttempt(0);
                    }
                    setInboxError(null);
                } catch (error) {
                    console.error("Failed to load admin inbox:", error);
                    setInboxError(t("components.layout.Topbar.kaf9d80f8"));
                    if (!requestSilent) {
                        setInboxItems([]);
                    }
                }

                if (!pendingInboxForceRef.current) break;
                pendingInboxForceRef.current = false;
                requestSilent = false;
                requestForce = true;
                requestRefreshHealth = true;
                setInboxLoading(true);
            }
        } finally {
            pendingInboxForceRef.current = false;
            inboxLoadingRef.current = false;
            setInboxLoading(false);
        }
    }, [t]);

    useEffect(() => {
        if (inboxRefreshAttempt === 0) return;
        const timeoutId = window.setTimeout(() => {
            void loadInbox(true, true, true);
        }, 1_500);
        return () => window.clearTimeout(timeoutId);
    }, [inboxRefreshAttempt, loadInbox]);

    useEffect(() => {
        // Keep route transitions clear of non-critical governance reads.
        const initialLoadId = window.setTimeout(() => void loadInbox(true), 1200);
        const intervalId = window.setInterval(() => {
            if (document.visibilityState === "visible") {
                void loadInbox(true, true);
            }
        }, 45000);
        const handleVisible = () => {
            if (document.visibilityState === "visible") {
                void loadInbox(true, true);
            }
        };
        document.addEventListener("visibilitychange", handleVisible);
        return () => {
            window.clearTimeout(initialLoadId);
            window.clearInterval(intervalId);
            document.removeEventListener("visibilitychange", handleVisible);
        };
    }, [loadInbox]);

    useEffect(() => {
        closePanels();
    }, [pathname, closePanels]);

    useEffect(() => {
        const liveIds = new Set(inboxItems.map((item) => item.id));
        setSeenInboxIds((current) => new Set([...current].filter((id) => liveIds.has(id))));
    }, [inboxItems]);

    useEffect(() => {
        if (activePanel !== "inbox" || !inboxItems.length) {
            return;
        }
        setSeenInboxIds((current) => new Set([...current, ...inboxItems.map((item) => item.id)]));
    }, [activePanel, inboxItems]);

    useEffect(() => {
        if (activePanel === "search") {
            window.requestAnimationFrame(() => searchInputRef.current?.focus());
        }
    }, [activePanel]);

    useEffect(() => {
        setActiveSearchIndex(0);
    }, [searchQuery, activePanel]);

    useEffect(() => {
        const handlePointerDown = (event: MouseEvent) => {
            const target = event.target as Node;
            if (
                searchContainerRef.current?.contains(target) ||
                inboxContainerRef.current?.contains(target) ||
                installContainerRef.current?.contains(target)
            ) {
                return;
            }
            closePanels();
        };
        document.addEventListener("pointerdown", handlePointerDown);
        return () => document.removeEventListener("pointerdown", handlePointerDown);
    }, [closePanels]);

    const handleSearchKeyDown = useCallback((event: KeyboardEvent<HTMLInputElement>) => {
        if (!searchResults.length) {
            if (event.key === "Escape") {
                closePanels();
            }
            return;
        }
        if (event.key === "ArrowDown") {
            event.preventDefault();
            setActiveSearchIndex((currentIndex) => (currentIndex + 1) % searchResults.length);
            return;
        }
        if (event.key === "ArrowUp") {
            event.preventDefault();
            setActiveSearchIndex((currentIndex) => (currentIndex - 1 + searchResults.length) % searchResults.length);
            return;
        }
        if (event.key === "Enter") {
            event.preventDefault();
            navigateTo(searchResults[Math.min(activeSearchIndex, searchResults.length - 1)].href);
            return;
        }
        if (event.key === "Escape") {
            event.preventDefault();
            closePanels();
        }
    }, [activeSearchIndex, closePanels, navigateTo, searchResults]);

    const toggleSearch = useCallback(() => {
        setActivePanel((currentPanel) => currentPanel === "search" ? null : "search");
    }, []);

    const toggleInbox = useCallback(() => {
        setActivePanel((currentPanel) => {
            const nextPanel = currentPanel === "inbox" ? null : "inbox";
            if (nextPanel === "inbox") {
                void loadInbox(false);
            }
            return nextPanel;
        });
    }, [loadInbox]);

    const toggleInstallPanel = useCallback(() => {
        const opening = activePanel !== "install";
        setActivePanel(opening ? "install" : null);
        if (opening) {
            void loadInstallState(false, installState !== null);
            void loadV8OSUpdateState(false);
        }
    }, [activePanel, installState, loadInstallState, loadV8OSUpdateState]);

    useEffect(() => {
        const openFeaturePacks = () => {
            setActivePanel("install");
            void loadInstallState(true, installState !== null);
            void loadV8OSUpdateState(false);
        };
        window.addEventListener("v8os:open-feature-packs", openFeaturePacks);
        return () => window.removeEventListener("v8os:open-feature-packs", openFeaturePacks);
    }, [installState, loadInstallState, loadV8OSUpdateState]);

    useEffect(() => {
        const healthRefreshing = Boolean(installState?.refreshing);
        const packInstalling = Boolean(installState?.packs.some((pack) => pack.status === "installing"));
        if (!healthRefreshing && !packInstalling) return;
        const timeoutId = window.setTimeout(() => {
            void loadInstallState(true, true, healthRefreshing || packInstalling);
        }, healthRefreshing ? installState?.retryAfterMs || 1_500 : 5_000);
        return () => window.clearTimeout(timeoutId);
    }, [installState, loadInstallState]);

    const handleInstallFeaturePack = useCallback(async (packId: string) => {
        setInstallSubmittingPackId(packId);
        try {
            const response = await fetch("/api/runtime-feature-packs", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ packId, locale }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(typeof payload.error === "string" ? payload.error : `Request failed (${response.status})`);
            }
            toast({
                title: t("components.layout.Topbar.featurePackInstallStartedTitle"),
                description: t("components.layout.Topbar.featurePackInstallStartedDescription"),
            });
            void loadInstallState(true, true, true);
        } catch (error) {
            console.error("Failed to start feature pack install:", error);
            toast({
                title: t("components.layout.Topbar.featurePackInstallFailedTitle"),
                description: t("components.layout.Topbar.featurePackInstallFailedDescription"),
                variant: "destructive",
            });
        } finally {
            setInstallSubmittingPackId(null);
        }
    }, [loadInstallState, locale, t, toast]);

    const featurePackLabel = t("components.layout.Topbar.featurePacksLabel");
    const featurePackButtonTitle = installState?.summary?.missing
        ? t("components.layout.Topbar.featurePacksMissingCount", { count: String(installState.summary.missing) })
        : featurePackLabel;
    const controlledUpdateUrl = v8osUpdateState?.releaseUrl && CONTROLLED_RELEASE_URL_RE.test(v8osUpdateState.releaseUrl)
        ? v8osUpdateState.releaseUrl
        : null;
    const shellCanOpenUpdate = isShell
        && typeof window !== "undefined"
        && Boolean(window.v8osShell?.openUpdateRelease);
    const updateAvailable = v8osUpdateState?.status === "available"
        && (shellCanOpenUpdate || Boolean(controlledUpdateUrl));
    const updateStatusKey = v8osUpdateLoading && !v8osUpdateState
        ? "checking"
        : v8osUpdateError
            ? "unavailable"
            : v8osUpdateState?.status || "checking";
    const openV8OSUpdate = useCallback(async () => {
        const shell = window.v8osShell;
        if (shell?.isShell && shell.openUpdateRelease) {
            const opened = await shell.openUpdateRelease().catch(() => false);
            if (!opened) {
                setV8osUpdateError(true);
            } else {
                closePanels();
            }
            return;
        }
        if (controlledUpdateUrl) {
            window.open(controlledUpdateUrl, "_blank", "noopener,noreferrer");
            closePanels();
        }
    }, [closePanels, controlledUpdateUrl]);

    return (
        <TopbarComponent
            brandImageSrc="/product-mark.png"
            brandLabel="V8 Agent OS"
            surfaceSwitcher={(
                <ProductSurfaceSwitcher
                    ariaLabel={t("components.layout.Topbar.surfaceSwitcher")}
                    items={[
                        chatSurfaceItem,
                        {
                            id: "admin",
                            label: t("components.layout.Topbar.surface.admin"),
                            active: true,
                            title: t("components.layout.Topbar.surface.currentAdmin"),
                        },
                    ]}
                />
            )}
            title={t(current.title)}
            subtitle={t(current.description)}
            actions={(
                <>
                    <DeviceConnectDialog />
                    <LocaleToggle />
                    <ThemeToggle />
                    <AdminHoverInfo
                        content={debugMode ? t("components.layout.Topbar.debugModeEnabledHint") : t("components.layout.Topbar.debugModeDisabledHint")}
                        align="right"
                    >
                        <TopbarGlowActionButton
                            tone={debugMode ? "violet" : "slate"}
                            onClick={() => toggleDebugMode(!debugMode)}
                            aria-label={debugMode ? t("components.layout.Topbar.debugModeEnabledHint") : t("components.layout.Topbar.debugModeDisabledHint")}
                            title={debugMode ? t("components.layout.Topbar.debugModeEnabledHint") : t("components.layout.Topbar.debugModeDisabledHint")}
                        >
                            <Wrench />
                        </TopbarGlowActionButton>
                    </AdminHoverInfo>
                    <div ref={installContainerRef} className="relative">
                        <TopbarGlowActionButton
                            tone="emerald"
                            onClick={toggleInstallPanel}
                            aria-label={featurePackButtonTitle}
                            title={featurePackButtonTitle}
                            aria-expanded={activePanel === "install"}
                        >
                            <Monitor />
                        </TopbarGlowActionButton>
                        {activePanel === "install" ? (
                            <Card className="absolute right-0 top-full z-50 mt-2 h-[calc(100dvh-5.5rem)] max-h-[42rem] w-[24rem] max-w-[calc(100vw-1rem)] overflow-hidden rounded-3xl border-border bg-card/95 p-0 shadow-2xl dark:border-white/10 dark:bg-zinc-950/95">
                                <ScrollArea
                                    className="h-full min-w-0 max-w-full overflow-x-hidden"
                                    scrollbarClassName="w-2 bg-muted/70 py-1 dark:bg-white/[0.04]"
                                    thumbClassName="bg-muted-foreground/45 transition-colors hover:bg-muted-foreground/70 dark:bg-slate-500/60 dark:hover:bg-slate-400/80"
                                >
                                    <div className="w-full min-w-0 max-w-full space-y-4 overflow-x-hidden p-4 pr-5 text-sm text-muted-foreground dark:text-slate-300">
                                            <div className="space-y-1">
                                                <div className="text-sm font-semibold text-foreground dark:text-slate-100">{featurePackLabel}</div>
                                                <div className="text-xs text-muted-foreground dark:text-muted-foreground">
                                                    {t("components.layout.Topbar.featurePacksDescription")}
                                                </div>
                                            </div>
                                            <section className="-mx-4 border-y border-border/70 bg-muted/50 px-4 py-3 dark:border-white/10 dark:bg-white/[0.025]" aria-labelledby="v8os-update-title">
                                                <div className="flex items-start justify-between gap-3">
                                                    <div className="min-w-0">
                                                        <div id="v8os-update-title" className="text-sm font-semibold text-foreground dark:text-slate-100">
                                                            {t("components.layout.Topbar.v8osUpdateTitle")}
                                                        </div>
                                                        <div className="mt-0.5 text-[11px] leading-5 text-muted-foreground">
                                                            {t("components.layout.Topbar.v8osUpdateDescription")}
                                                        </div>
                                                    </div>
                                                    {v8osUpdateState?.status === "current" && !v8osUpdateError ? (
                                                        <CircleCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" aria-hidden="true" />
                                                    ) : null}
                                                </div>
                                                <div className="mt-3 grid grid-cols-2 gap-2">
                                                    <div className="min-w-0">
                                                        <div className="text-[10px] font-medium uppercase text-muted-foreground">
                                                            {t("components.layout.Topbar.v8osUpdateCurrentVersion")}
                                                        </div>
                                                        <div className="mt-0.5 truncate font-mono text-xs font-semibold text-foreground dark:text-slate-200" title={v8osUpdateState?.currentVersion || undefined}>
                                                            {v8osUpdateState?.currentVersion || "--"}
                                                        </div>
                                                    </div>
                                                    <div className="min-w-0 border-l border-border/70 pl-3 dark:border-white/10">
                                                        <div className="text-[10px] font-medium uppercase text-muted-foreground">
                                                            {t("components.layout.Topbar.v8osUpdateLatestVersion")}
                                                        </div>
                                                        <div className="mt-0.5 truncate font-mono text-xs font-semibold text-foreground dark:text-slate-200" title={v8osUpdateState?.latestVersion || undefined}>
                                                            {v8osUpdateState?.latestVersion || "--"}
                                                        </div>
                                                    </div>
                                                </div>
                                                <div className="mt-2 text-[11px] leading-5 text-muted-foreground" role="status" aria-live="polite">
                                                    {t(`components.layout.Topbar.v8osUpdateStatus.${updateStatusKey}`)}
                                                </div>
                                                <div className="mt-3 flex flex-wrap items-center gap-2">
                                                    <Button
                                                        type="button"
                                                        size="sm"
                                                        variant="outline"
                                                        className="h-8 rounded-xl px-2.5"
                                                        onClick={() => void loadV8OSUpdateState(true)}
                                                        disabled={v8osUpdateLoading}
                                                    >
                                                        <RefreshCw className={cn("mr-1.5 h-3.5 w-3.5", v8osUpdateLoading && "animate-spin")} />
                                                        {t("components.layout.Topbar.v8osUpdateCheck")}
                                                    </Button>
                                                    {updateAvailable ? (
                                                        <Button type="button" size="sm" className="h-8 rounded-xl px-2.5" onClick={() => void openV8OSUpdate()}>
                                                            {t("components.layout.Topbar.v8osUpdateAction")}
                                                            <ArrowUpRight className="ml-1.5 h-3.5 w-3.5" />
                                                        </Button>
                                                    ) : (
                                                        <Button type="button" size="sm" className="h-8 rounded-xl px-2.5" disabled>
                                                            {t("components.layout.Topbar.v8osUpdateAction")}
                                                        </Button>
                                                    )}
                                                </div>
                                                <div className="mt-2 text-[10px] leading-4 text-muted-foreground">
                                                    {t("components.layout.Topbar.v8osUpdatePreviewNotice")}
                                                </div>
                                            </section>
                                            <div className="space-y-2">
                                                {installLoading && !installState ? (
                                                    <div className="flex h-28 items-center justify-center">
                                                        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                                                    </div>
                                                ) : (installState?.packs || []).map((pack) => {
                                                const isInstalled = pack.status === "installed";
                                                const isInstalling = pack.status === "installing" || installSubmittingPackId === pack.id;
                                                const anotherPackInstalling = Boolean(
                                                    installSubmittingPackId
                                                    || installState?.packs.some((candidate) => candidate.status === "installing"),
                                                );
                                                const showInstall = pack.installable && !isInstalled;
                                                const canInstall = showInstall && !isInstalling && !anotherPackInstalling;
                                                const packI18nKey = `components.layout.Topbar.featurePack.${pack.id}`;
                                                const productName = t(`${packI18nKey}.name`);
                                                const description = t(`${packI18nKey}.description`);
                                                const hover = t(`${packI18nKey}.hover`);
                                                return (
                                                    <div key={pack.id} className="w-full min-w-0 max-w-full overflow-hidden rounded-2xl border border-border bg-muted/80 p-3 dark:border-white/10 dark:bg-white/[0.04]" title={hover}>
                                                        <div className="flex items-start justify-between gap-3">
                                                            <div className="min-w-0">
                                                                <div className="truncate text-sm font-semibold text-foreground dark:text-slate-100">{productName}</div>
                                                                <div className="mt-1 break-words text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere] dark:text-muted-foreground">{description}</div>
                                                                {pack.runtimeFamilies.length ? (
                                                                    <div className="mt-2 flex flex-wrap gap-1.5">
                                                                        {pack.runtimeFamilies.map((family) => (
                                                                            <span key={family} className="rounded-full bg-card px-2 py-0.5 text-[10px] font-medium text-muted-foreground dark:bg-card/10 dark:text-slate-300">
                                                                                {family}
                                                                            </span>
                                                                        ))}
                                                                    </div>
                                                                ) : null}
                                                            </div>
                                                            <span className={cn(
                                                                "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                                                                pack.status === "installed"
                                                                    ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200"
                                                                    : pack.status === "failed"
                                                                        ? "bg-rose-100 text-rose-700 dark:bg-rose-500/10 dark:text-rose-200"
                                                                        : pack.status === "installing"
                                                                            ? "bg-sky-100 text-sky-700 dark:bg-sky-500/10 dark:text-sky-200"
                                                                            : "bg-amber-100 text-amber-700 dark:bg-amber-500/10 dark:text-amber-200",
                                                            )}>
                                                                {t(`components.layout.Topbar.featurePackStatus.${pack.status}`)}
                                                            </span>
                                                        </div>
                                                        {pack.hasError ? (
                                                            <div className="mt-2 rounded-xl bg-rose-50 px-2.5 py-1.5 text-xs leading-5 text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">
                                                                {t("components.layout.Topbar.featurePackInstallFailedDetail")}
                                                            </div>
                                                        ) : null}
                                                        {pack.executionProvider ? (
                                                            <div className="mt-2 break-words text-[11px] leading-5 text-muted-foreground [overflow-wrap:anywhere]">
                                                                {t("components.layout.Topbar.featurePackExecutionProvider", { provider: pack.executionProvider })}
                                                                {pack.gpuAdapters?.length ? (
                                                                    <span title={pack.gpuAdapters.join(", ")}>
                                                                        {` · ${t("components.layout.Topbar.featurePackDetectedGpu", { gpu: pack.gpuAdapters[0] })}`}
                                                                    </span>
                                                                ) : null}
                                                            </div>
                                                        ) : null}
                                                        {pack.logName ? (
                                                            <div className="mt-2 truncate text-[11px] text-muted-foreground">
                                                                {t("components.layout.Topbar.featurePackLogRef")}: {pack.logName}
                                                            </div>
                                                        ) : null}
                                                        <div className="mt-3 flex min-w-0 flex-wrap items-center justify-between gap-3">
                                                            <div className="min-w-0 flex-1 break-words text-[11px] text-muted-foreground">
                                                                {pack.restartRequired ? t("components.layout.Topbar.featurePackRestartRequired") : t("components.layout.Topbar.featurePackNoRestart")}
                                                            </div>
                                                            {isInstalling ? (
                                                                <span className="inline-flex shrink-0 items-center text-xs text-sky-600 dark:text-sky-200">
                                                                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                                                    {t("components.layout.Topbar.featurePackInstalling")}
                                                                </span>
                                                            ) : showInstall ? (
                                                                <Button size="sm" className="shrink-0 rounded-xl" disabled={!canInstall} onClick={() => void handleInstallFeaturePack(pack.id)}>
                                                                    {t("components.layout.Topbar.featurePackInstall")}
                                                                </Button>
                                                            ) : null}
                                                        </div>
                                                    </div>
                                                );
                                                })}
                                            </div>
                                    </div>
                                </ScrollArea>
                            </Card>
                        ) : null}
                    </div>
                    <div ref={searchContainerRef} className="relative">
                        <TopbarGlowActionButton
                            tone="sky"
                            onClick={toggleSearch}
                            aria-label={t("components.layout.Topbar.ke9ace2b3")}
                            title={t("components.layout.Topbar.ke9ace2b3")}
                            aria-expanded={activePanel === "search"}
                        >
                            <Search />
                        </TopbarGlowActionButton>
                        {activePanel === "search" ? (
                            <Card className="absolute right-0 top-full z-50 mt-2 w-[26rem] max-w-[calc(100vw-2rem)] rounded-3xl border-border bg-card/95 p-3 shadow-2xl dark:border-white/10 dark:bg-zinc-950/95">
                                <div className="space-y-3">
                                    <Input
                                        ref={searchInputRef}
                                        value={searchQuery}
                                        onChange={(event) => setSearchQuery(event.target.value)}
                                        onKeyDown={handleSearchKeyDown}
                                        placeholder={t("components.layout.Topbar.k6c2190ce")}
                                        aria-label={t("components.layout.Topbar.k8cef7920")}
                                        className="rounded-2xl border-border dark:border-white/10 dark:bg-white/[0.04]"
                                    />
                                    <div className="max-h-80 overflow-y-auto">
                                        {searchResults.length ? (
                                            <div role="listbox" aria-label={t("components.layout.Topbar.k5a0d1278")} className="space-y-1.5">
                                                {searchResults.map((item, index) => (
                                                    <button
                                                        key={item.id}
                                                        type="button"
                                                        onClick={() => navigateTo(item.href)}
                                                        className={cn(
                                                            "flex w-full items-start justify-between gap-3 rounded-2xl border border-transparent px-3 py-2 text-left transition",
                                                            index === activeSearchIndex ? "border-sky-200 bg-sky-50 dark:border-sky-500/25 dark:bg-sky-500/10" : "hover:border-border hover:bg-muted/50 dark:hover:border-white/10 dark:hover:bg-white/[0.05]",
                                                        )}
                                                        role="option"
                                                        aria-selected={index === activeSearchIndex}
                                                    >
                                                        <div className="min-w-0">
                                                            <div className="truncate text-sm font-semibold text-foreground dark:text-slate-100">{t(item.title)}</div>
                                                            <div className="truncate text-xs text-muted-foreground dark:text-muted-foreground">
                                                                {item.subtitle ? `${t(item.subtitle)} · ` : ""}{item.href}
                                                            </div>
                                                        </div>
                                                        <span className={cn(
                                                            "rounded-full px-2 py-0.5 text-[11px] font-medium",
                                                            item.matchMode === "exact"
                                                                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200"
                                                                : item.matchMode === "fuzzy"
                                                                    ? "bg-muted text-muted-foreground dark:bg-card/10 dark:text-slate-200"
                                                                    : "bg-sky-100 text-sky-700 dark:bg-sky-500/10 dark:text-sky-200",
                                                        )}>
                                                            {item.matchMode === "exact" ? t("components.layout.Topbar.k9d9dbbea") : item.matchMode === "fuzzy" ? t("components.layout.Topbar.k97b260d0") : t("components.layout.Topbar.kff332614")}
                                                        </span>
                                                    </button>
                                                ))}
                                            </div>
                                        ) : (
                                            <div className="rounded-2xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground dark:border-white/10 dark:text-muted-foreground">
                                                {t("components.layout.Topbar.k4808f001")}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </Card>
                        ) : null}
                    </div>
                    <div ref={inboxContainerRef} className="relative">
                        <TopbarGlowActionButton
                            tone="rose"
                            onClick={toggleInbox}
                            className="overflow-visible"
                            aria-label={t("components.layout.Topbar.kaa548c2e")}
                            title={t("components.layout.Topbar.kaa548c2e")}
                            aria-expanded={activePanel === "inbox"}
                        >
                            <Bell />
                            {unreadInboxCount > 0 ? (
                                <span className="absolute -right-1 -top-1 z-10 inline-flex min-h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[9px] font-semibold leading-none text-white">
                                    {unreadInboxCount > 9 ? "9+" : unreadInboxCount}
                                </span>
                            ) : null}
                        </TopbarGlowActionButton>
                        {activePanel === "inbox" ? (
                            <Card className="absolute right-0 top-full z-50 mt-2 w-[24rem] max-w-[calc(100vw-2rem)] rounded-3xl border-border bg-card/95 p-3 shadow-2xl dark:border-white/10 dark:bg-zinc-950/95">
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between">
                                        <div className="text-sm font-semibold text-foreground dark:text-slate-100">{t("components.layout.Topbar.k3957f4b0")}</div>
                                        <div className="flex items-center gap-1">
                                            {inboxLoading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8"
                                                onClick={() => void loadInbox(false, true, true)}
                                                disabled={inboxLoading}
                                                aria-label={t("components.layout.Topbar.refreshInbox")}
                                                title={t("components.layout.Topbar.refreshInbox")}
                                            >
                                                <RefreshCw className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </div>
                                    {inboxError ? (
                                        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
                                            {inboxError}
                                        </div>
                                    ) : null}
                                    {inboxItems.length ? (
                                        <div className="max-h-80 space-y-1.5 overflow-y-auto">
                                            {inboxItems.map((item) => (
                                                <button
                                                    key={item.id}
                                                    type="button"
                                                    onClick={() => navigateTo(item.href)}
                                                    className="flex w-full items-start gap-3 rounded-2xl border border-transparent px-3 py-2 text-left transition hover:border-border hover:bg-muted/50 dark:hover:border-white/10 dark:hover:bg-white/[0.05]"
                                                >
                                                    <SeverityDot severity={item.severity} />
                                                    <div className="min-w-0">
                                                        <div className="truncate text-sm font-semibold text-foreground dark:text-slate-100">{item.title}</div>
                                                        <div className="mt-0.5 text-xs leading-5 text-muted-foreground dark:text-muted-foreground">{item.summary}</div>
                                                    </div>
                                                </button>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="rounded-2xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground dark:border-white/10 dark:text-muted-foreground">
                                            {t("components.layout.Topbar.k105fef86")}
                                        </div>
                                    )}
                                </div>
                            </Card>
                        ) : null}
                    </div>
                </>
            )}
            windowControls={resolvedWindowControls}
        />
    );
}

export const Topbar = AdminTopbar;
