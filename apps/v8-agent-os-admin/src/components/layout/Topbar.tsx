"use client";

import { type KeyboardEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ProductTopbar, TopbarGlowActionButton } from "@v8/product-ui";
import { Bell, Loader2, Monitor, Search, Server, Wrench } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { searchAdminTopbarEntries } from "@/components/layout/admin-topbar-search";
import { LocaleToggle } from "@/components/layout/LocaleToggle";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { DeviceConnectDialog } from "@/components/admin/DeviceConnectDialog";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { getAdminNavItem } from "@/lib/admin-navigation";
import { cn } from "@/lib/utils";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { useDebugMode } from "@/lib/useDebugMode";

type InboxItem = {
    id: string;
    title: string;
    summary: string;
    severity: "error" | "warning" | "info";
    href: string;
    source: string;
};

type RuntimeInstallState = {
    installProfile: "minimal" | "desktop";
    installPlatform: "windows" | "macos" | "linux";
    installedRuntimeFamilies: string[];
    bootstrapManaged: boolean;
    lastUpgradeAt: string | null;
    engineAvailable: boolean;
    canInstallDesktop: boolean;
    canAutoRestart: boolean;
};

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
    const [debugMode, toggleDebugMode] = useDebugMode();
    const { toast } = useToast();
    const [activePanel, setActivePanel] = useState<"install" | "search" | "inbox" | null>(null);
    const [searchQuery, setSearchQuery] = useState("");
    const [activeSearchIndex, setActiveSearchIndex] = useState(0);
    const [inboxItems, setInboxItems] = useState<InboxItem[]>([]);
    const [seenInboxIds, setSeenInboxIds] = useState<Set<string>>(new Set());
    const [inboxLoading, setInboxLoading] = useState(false);
    const [inboxError, setInboxError] = useState<string | null>(null);
    const [installState, setInstallState] = useState<RuntimeInstallState | null>(null);
    const [installLoading, setInstallLoading] = useState(false);
    const [installSubmitting, setInstallSubmitting] = useState(false);
    const searchContainerRef = useRef<HTMLDivElement | null>(null);
    const inboxContainerRef = useRef<HTMLDivElement | null>(null);
    const installContainerRef = useRef<HTMLDivElement | null>(null);
    const searchInputRef = useRef<HTMLInputElement | null>(null);

    const searchResults = useMemo(
        () => searchAdminTopbarEntries(searchQuery, 8),
        [searchQuery],
    );
    const unreadInboxCount = useMemo(
        () => inboxItems.filter((item) => !seenInboxIds.has(item.id)).length,
        [inboxItems, seenInboxIds],
    );

    const loadInstallState = useCallback(async () => {
        setInstallLoading(true);
        try {
            const response = await fetch("/api/runtime-install", { cache: "no-store" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                const message = typeof payload.error === "string" ? payload.error : `Request failed (${response.status})`;
                console.warn("Failed to load runtime install state:", message);
                toast({
                    title: t("components.layout.Topbar.kc9b3a73e"),
                    description: t("components.layout.Topbar.k7d9781c3"),
                    variant: "destructive",
                });
                return;
            }
            setInstallState(payload as RuntimeInstallState);
        } catch (error) {
            console.error("Failed to load runtime install state:", error);
            toast({
                title: t("components.layout.Topbar.kc9b3a73e"),
                description: t("components.layout.Topbar.k7d9781c3"),
                variant: "destructive",
            });
        } finally {
            setInstallLoading(false);
        }
    }, [t, toast]);

    const closePanels = useCallback(() => {
        setActivePanel(null);
        setActiveSearchIndex(0);
    }, []);

    const navigateTo = useCallback((href: string) => {
        closePanels();
        setSearchQuery("");
        router.push(href);
    }, [closePanels, router]);

    const loadInbox = useCallback(async (silent = false) => {
        if (!silent) {
            setInboxLoading(true);
        }
        try {
            const response = await fetch("/api/admin-inbox", { cache: "no-store" });
            const payload = await response.json().catch(() => ({ items: [] }));
            if (!response.ok) {
                const message = typeof payload.error === "string" ? payload.error : `Request failed (${response.status})`;
                console.warn("Failed to load admin inbox:", message);
                setInboxError(t("components.layout.Topbar.kaf9d80f8"));
                if (!silent) {
                    setInboxItems([]);
                }
                return;
            }
            setInboxItems(Array.isArray(payload.items) ? payload.items : []);
            setInboxError(null);
        } catch (error) {
            console.error("Failed to load admin inbox:", error);
            setInboxError(t("components.layout.Topbar.kaf9d80f8"));
            if (!silent) {
                setInboxItems([]);
            }
        } finally {
            setInboxLoading(false);
        }
    }, [t]);

    useEffect(() => {
        void loadInbox(true);
        void loadInstallState();
        const intervalId = window.setInterval(() => {
            if (document.visibilityState === "visible") {
                void loadInbox(true);
                void loadInstallState();
            }
        }, 45000);
        const handleVisible = () => {
            if (document.visibilityState === "visible") {
                void loadInbox(true);
                void loadInstallState();
            }
        };
        document.addEventListener("visibilitychange", handleVisible);
        return () => {
            window.clearInterval(intervalId);
            document.removeEventListener("visibilitychange", handleVisible);
        };
    }, [loadInbox, loadInstallState]);

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
        setActivePanel((currentPanel) => {
            const nextPanel = currentPanel === "install" ? null : "install";
            if (nextPanel === "install") {
                void loadInstallState();
            }
            return nextPanel;
        });
    }, [loadInstallState]);

    const handleInstallDesktop = useCallback(async () => {
        setInstallSubmitting(true);
        try {
            const response = await fetch("/api/runtime-install", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ platform: installState?.installPlatform || "auto" }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(typeof payload.error === "string" ? payload.error : `Request failed (${response.status})`);
            }
            toast({
                title: t("components.layout.Topbar.k6ff2c8c3"),
                description: String(payload.message || ""),
            });
            void loadInstallState();
            closePanels();
        } catch (error) {
            console.error("Failed to start desktop install:", error);
            toast({
                title: t("components.layout.Topbar.k1a77eba9"),
                description: error instanceof Error ? error.message : t("components.layout.Topbar.kca0ab74d"),
                variant: "destructive",
            });
        } finally {
            setInstallSubmitting(false);
        }
    }, [closePanels, installState?.installPlatform, loadInstallState, t, toast]);

    const installProfileLabel = installState?.installProfile === "desktop"
        ? t("components.layout.Topbar.kac0ecd7e")
        : t("components.layout.Topbar.k9163f343");
    const InstallIcon = installState?.installProfile === "desktop" ? Monitor : Server;

    return (
        <ProductTopbar
            brandImageSrc="/product-mark.png"
            brandLabel="V8 Agent OS"
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
                            aria-label={installProfileLabel}
                            title={installProfileLabel}
                            aria-expanded={activePanel === "install"}
                        >
                            <InstallIcon />
                        </TopbarGlowActionButton>
                        {activePanel === "install" ? (
                            <Card className="absolute right-0 top-full z-50 mt-2 w-[22rem] max-w-[calc(100vw-2rem)] rounded-3xl border-slate-200 bg-white/95 p-4 shadow-2xl dark:border-white/10 dark:bg-zinc-950/95">
                                {installLoading ? (
                                    <div className="flex h-28 items-center justify-center">
                                        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
                                    </div>
                                ) : (
                                    <div className="space-y-4 text-sm text-slate-600 dark:text-slate-300">
                                        <div className="space-y-1">
                                            <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">{installProfileLabel}</div>
                                            <div>{t("components.layout.Topbar.k0769c431")}: {installState?.installPlatform || "-"}</div>
                                            <div>{t("components.layout.Topbar.kff0c33d3")}: {installState?.bootstrapManaged ? t("components.layout.Topbar.k2ae24b34") : t("components.layout.Topbar.k8d9f05ae")}</div>
                                        </div>
                                        <div className="space-y-1">
                                            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400 dark:text-slate-500">
                                                {t("components.layout.Topbar.k7a89604c")}
                                            </div>
                                            <div className="flex flex-wrap gap-1.5">
                                                {(installState?.installedRuntimeFamilies || []).map((family) => (
                                                    <span key={family} className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-600 dark:bg-white/10 dark:text-slate-200">
                                                        {family}
                                                    </span>
                                                ))}
                                            </div>
                                        </div>
                                        {installState?.canInstallDesktop ? (
                                            <div className="space-y-2">
                                                <div className="text-xs text-slate-500 dark:text-slate-400">
                                                    {installState?.canAutoRestart
                                                        ? t("components.layout.Topbar.k3f17614c")
                                                        : t("components.layout.Topbar.k7e24dc73")}
                                                </div>
                                                <Button
                                                    className="w-full rounded-2xl"
                                                    onClick={() => void handleInstallDesktop()}
                                                    disabled={installSubmitting}
                                                >
                                                    {installSubmitting ? (
                                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                    ) : null}
                                                    {t("components.layout.Topbar.k81c75821")}
                                                </Button>
                                            </div>
                                        ) : (
                                            <div className="rounded-2xl bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200">
                                                {t("components.layout.Topbar.ka167fbd8")}
                                            </div>
                                        )}
                                    </div>
                                )}
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
                            <Card className="absolute right-0 top-full z-50 mt-2 w-[26rem] max-w-[calc(100vw-2rem)] rounded-3xl border-slate-200 bg-white/95 p-3 shadow-2xl dark:border-white/10 dark:bg-zinc-950/95">
                                <div className="space-y-3">
                                    <Input
                                        ref={searchInputRef}
                                        value={searchQuery}
                                        onChange={(event) => setSearchQuery(event.target.value)}
                                        onKeyDown={handleSearchKeyDown}
                                        placeholder={t("components.layout.Topbar.k6c2190ce")}
                                        aria-label={t("components.layout.Topbar.k8cef7920")}
                                        className="rounded-2xl border-slate-200 dark:border-white/10 dark:bg-white/[0.04]"
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
                                                            index === activeSearchIndex ? "border-sky-200 bg-sky-50 dark:border-sky-500/25 dark:bg-sky-500/10" : "hover:border-slate-200 hover:bg-slate-50 dark:hover:border-white/10 dark:hover:bg-white/[0.05]",
                                                        )}
                                                        role="option"
                                                        aria-selected={index === activeSearchIndex}
                                                    >
                                                        <div className="min-w-0">
                                                            <div className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">{t(item.title)}</div>
                                                            <div className="truncate text-xs text-slate-500 dark:text-slate-400">
                                                                {item.subtitle ? `${t(item.subtitle)} · ` : ""}{item.href}
                                                            </div>
                                                        </div>
                                                        <span className={cn(
                                                            "rounded-full px-2 py-0.5 text-[11px] font-medium",
                                                            item.matchMode === "exact"
                                                                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200"
                                                                : item.matchMode === "fuzzy"
                                                                    ? "bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-200"
                                                                    : "bg-sky-100 text-sky-700 dark:bg-sky-500/10 dark:text-sky-200",
                                                        )}>
                                                            {item.matchMode === "exact" ? t("components.layout.Topbar.k9d9dbbea") : item.matchMode === "fuzzy" ? t("components.layout.Topbar.k97b260d0") : t("components.layout.Topbar.kff332614")}
                                                        </span>
                                                    </button>
                                                ))}
                                            </div>
                                        ) : (
                                            <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-center text-sm text-slate-500 dark:border-white/10 dark:text-slate-400">
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
                            <Card className="absolute right-0 top-full z-50 mt-2 w-[24rem] max-w-[calc(100vw-2rem)] rounded-3xl border-slate-200 bg-white/95 p-3 shadow-2xl dark:border-white/10 dark:bg-zinc-950/95">
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between">
                                        <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">{t("components.layout.Topbar.k3957f4b0")}</div>
                                        {inboxLoading ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : null}
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
                                                    className="flex w-full items-start gap-3 rounded-2xl border border-transparent px-3 py-2 text-left transition hover:border-slate-200 hover:bg-slate-50 dark:hover:border-white/10 dark:hover:bg-white/[0.05]"
                                                >
                                                    <SeverityDot severity={item.severity} />
                                                    <div className="min-w-0">
                                                        <div className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">{item.title}</div>
                                                        <div className="mt-0.5 text-xs leading-5 text-slate-500 dark:text-slate-400">{item.summary}</div>
                                                    </div>
                                                </button>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-center text-sm text-slate-500 dark:border-white/10 dark:text-slate-400">
                                            {t("components.layout.Topbar.k105fef86")}
                                        </div>
                                    )}
                                </div>
                            </Card>
                        ) : null}
                    </div>
                </>
            )}
            windowControls={windowControls}
        />
    );
}

export const Topbar = AdminTopbar;
