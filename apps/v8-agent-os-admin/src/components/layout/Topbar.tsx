"use client";

import { type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bell, Loader2, Monitor, Search, Server } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { searchAdminTopbarEntries } from "@/components/layout/admin-topbar-search";
import { LocaleToggle } from "@/components/layout/LocaleToggle";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { lt } from "@/lib/locale";
import { getAdminNavItem } from "@/lib/admin-navigation";
import { cn } from "@/lib/utils";

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

export function Topbar() {
    const pathname = usePathname();
    const router = useRouter();
    const current = getAdminNavItem(pathname);
    const t = useT();
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
                    title: t(lt("安装态读取失败", "Install state unavailable")),
                    description: t(lt("当前无法读取安装态信息。", "Unable to load the installation state right now.")),
                    variant: "destructive",
                });
                return;
            }
            setInstallState(payload as RuntimeInstallState);
        } catch (error) {
            console.error("Failed to load runtime install state:", error);
            toast({
                title: t(lt("安装态读取失败", "Install state unavailable")),
                description: t(lt("当前无法读取安装态信息。", "Unable to load the installation state right now.")),
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
                setInboxError(t(lt("当前无法读取消息摘要。", "Unable to load inbox right now.")));
                if (!silent) {
                    setInboxItems([]);
                }
                return;
            }
            setInboxItems(Array.isArray(payload.items) ? payload.items : []);
            setInboxError(null);
        } catch (error) {
            console.error("Failed to load admin inbox:", error);
            setInboxError(t(lt("当前无法读取消息摘要。", "Unable to load inbox right now.")));
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
                title: t(lt("桌面增强安装已启动", "Desktop install started")),
                description: String(payload.message || ""),
            });
            void loadInstallState();
            closePanels();
        } catch (error) {
            console.error("Failed to start desktop install:", error);
            toast({
                title: t(lt("安装启动失败", "Failed to start install")),
                description: error instanceof Error ? error.message : t(lt("当前无法启动桌面增强安装。", "Unable to start the desktop install right now.")),
                variant: "destructive",
            });
        } finally {
            setInstallSubmitting(false);
        }
    }, [closePanels, installState?.installPlatform, loadInstallState, t, toast]);

    const installProfileLabel = installState?.installProfile === "desktop"
        ? t(lt("桌面安装", "Desktop install"))
        : t(lt("最小安装", "Minimal install"));
    const InstallIcon = installState?.installProfile === "desktop" ? Monitor : Server;

    return (
        <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur">
            <div className="flex min-h-20 items-center justify-between gap-4 px-6 py-4">
                <div className="space-y-1">
                    <div className="text-sm font-medium text-slate-900">{t(current.title)}</div>
                    <div className="text-sm text-slate-500">{t(current.description)}</div>
                </div>
                <div className="flex items-center gap-2">
                    <LocaleToggle />
                    <div ref={installContainerRef} className="relative">
                        <Button
                            variant="outline"
                            size="icon"
                            onClick={toggleInstallPanel}
                            className="rounded-2xl border-slate-200 bg-white text-slate-500"
                            aria-label={installProfileLabel}
                            title={installProfileLabel}
                            aria-expanded={activePanel === "install"}
                        >
                            <InstallIcon className="h-4 w-4" />
                        </Button>
                        {activePanel === "install" ? (
                            <Card className="absolute right-0 top-full z-50 mt-2 w-[22rem] max-w-[calc(100vw-2rem)] rounded-3xl border-slate-200 bg-white/95 p-4 shadow-2xl">
                                {installLoading ? (
                                    <div className="flex h-28 items-center justify-center">
                                        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
                                    </div>
                                ) : (
                                    <div className="space-y-4 text-sm text-slate-600">
                                        <div className="space-y-1">
                                            <div className="text-sm font-semibold text-slate-900">{installProfileLabel}</div>
                                            <div>{t(lt("当前平台", "Platform"))}: {installState?.installPlatform || "-"}</div>
                                            <div>{t(lt("安装来源", "Managed by bootstrap"))}: {installState?.bootstrapManaged ? t(lt("是", "Yes")) : t(lt("否", "No"))}</div>
                                        </div>
                                        <div className="space-y-1">
                                            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">
                                                {t(lt("已安装 runtime 家族", "Installed runtime families"))}
                                            </div>
                                            <div className="flex flex-wrap gap-1.5">
                                                {(installState?.installedRuntimeFamilies || []).map((family) => (
                                                    <span key={family} className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-600">
                                                        {family}
                                                    </span>
                                                ))}
                                            </div>
                                        </div>
                                        {installState?.canInstallDesktop ? (
                                            <div className="space-y-2">
                                                <div className="text-xs text-slate-500">
                                                    {installState?.canAutoRestart
                                                        ? t(lt("可直接补装当前系统的桌面依赖，并在完成后重启 engine。", "Install desktop dependencies for this platform and restart the engine automatically."))
                                                        : t(lt("可补装当前系统的桌面依赖，但当前环境不是 bootstrap-managed，安装后需要手动重启 engine。", "Desktop dependencies can be installed, but this environment is not bootstrap-managed, so you need to restart the engine manually afterwards."))}
                                                </div>
                                                <Button
                                                    className="w-full rounded-2xl"
                                                    onClick={() => void handleInstallDesktop()}
                                                    disabled={installSubmitting}
                                                >
                                                    {installSubmitting ? (
                                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                    ) : null}
                                                    {t(lt("安装桌面能力", "Install desktop capabilities"))}
                                                </Button>
                                            </div>
                                        ) : (
                                            <div className="rounded-2xl bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
                                                {t(lt("当前机器已完成桌面安装。", "Desktop capabilities are already installed on this machine."))}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </Card>
                        ) : null}
                    </div>
                    <div ref={searchContainerRef} className="relative">
                        <Button
                            variant="outline"
                            size="icon"
                            onClick={toggleSearch}
                            className="rounded-2xl border-slate-200 bg-white text-slate-500"
                            aria-label={t(lt("搜索", "Search"))}
                            title={t(lt("搜索", "Search"))}
                            aria-expanded={activePanel === "search"}
                        >
                            <Search className="h-4 w-4" />
                        </Button>
                        {activePanel === "search" ? (
                            <Card className="absolute right-0 top-full z-50 mt-2 w-[26rem] max-w-[calc(100vw-2rem)] rounded-3xl border-slate-200 bg-white/95 p-3 shadow-2xl">
                                <div className="space-y-3">
                                    <Input
                                        ref={searchInputRef}
                                        value={searchQuery}
                                        onChange={(event) => setSearchQuery(event.target.value)}
                                        onKeyDown={handleSearchKeyDown}
                                        placeholder={t(lt("搜索页面、runtime 或记忆标签…", "Search pages, runtimes, or memory tabs…"))}
                                        aria-label={t(lt("搜索管理页", "Search admin pages"))}
                                        className="rounded-2xl border-slate-200"
                                    />
                                    <div className="max-h-80 overflow-y-auto">
                                        {searchResults.length ? (
                                            <div role="listbox" aria-label={t(lt("搜索候选", "Search results"))} className="space-y-1.5">
                                                {searchResults.map((item, index) => (
                                                    <button
                                                        key={item.id}
                                                        type="button"
                                                        onClick={() => navigateTo(item.href)}
                                                        className={cn(
                                                            "flex w-full items-start justify-between gap-3 rounded-2xl border border-transparent px-3 py-2 text-left transition",
                                                            index === activeSearchIndex ? "border-sky-200 bg-sky-50" : "hover:border-slate-200 hover:bg-slate-50",
                                                        )}
                                                        role="option"
                                                        aria-selected={index === activeSearchIndex}
                                                    >
                                                        <div className="min-w-0">
                                                            <div className="truncate text-sm font-semibold text-slate-900">{t(item.title)}</div>
                                                            <div className="truncate text-xs text-slate-500">
                                                                {item.subtitle ? `${t(item.subtitle)} · ` : ""}{item.href}
                                                            </div>
                                                        </div>
                                                        <span className={cn(
                                                            "rounded-full px-2 py-0.5 text-[11px] font-medium",
                                                            item.matchMode === "exact"
                                                                ? "bg-emerald-100 text-emerald-700"
                                                                : item.matchMode === "fuzzy"
                                                                    ? "bg-slate-100 text-slate-600"
                                                                    : "bg-sky-100 text-sky-700",
                                                        )}>
                                                            {item.matchMode === "exact" ? t(lt("精确", "Exact")) : item.matchMode === "fuzzy" ? t(lt("模糊", "Fuzzy")) : t(lt("常用", "Quick"))}
                                                        </span>
                                                    </button>
                                                ))}
                                            </div>
                                        ) : (
                                            <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-center text-sm text-slate-500">
                                                {t(lt("没有匹配项，试试页面名、runtime 名称或 memory tab。", "No matches yet. Try a page name, runtime name, or memory tab."))}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </Card>
                        ) : null}
                    </div>
                    <div ref={inboxContainerRef} className="relative">
                        <Button
                            variant="outline"
                            size="icon"
                            onClick={toggleInbox}
                            className="rounded-2xl border-slate-200 bg-white text-slate-500"
                            aria-label={t(lt("通知", "Alerts"))}
                            title={t(lt("通知", "Alerts"))}
                            aria-expanded={activePanel === "inbox"}
                        >
                            <Bell className="h-4 w-4" />
                            {unreadInboxCount > 0 ? (
                                <span className="absolute -right-1 -top-1 inline-flex min-h-5 min-w-5 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold text-white">
                                    {unreadInboxCount > 9 ? "9+" : unreadInboxCount}
                                </span>
                            ) : null}
                        </Button>
                        {activePanel === "inbox" ? (
                            <Card className="absolute right-0 top-full z-50 mt-2 w-[24rem] max-w-[calc(100vw-2rem)] rounded-3xl border-slate-200 bg-white/95 p-3 shadow-2xl">
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between">
                                        <div className="text-sm font-semibold text-slate-900">{t(lt("消息摘要", "Inbox"))}</div>
                                        {inboxLoading ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : null}
                                    </div>
                                    {inboxError ? (
                                        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700">
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
                                                    className="flex w-full items-start gap-3 rounded-2xl border border-transparent px-3 py-2 text-left transition hover:border-slate-200 hover:bg-slate-50"
                                                >
                                                    <SeverityDot severity={item.severity} />
                                                    <div className="min-w-0">
                                                        <div className="truncate text-sm font-semibold text-slate-900">{item.title}</div>
                                                        <div className="mt-0.5 text-xs leading-5 text-slate-500">{item.summary}</div>
                                                    </div>
                                                </button>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-center text-sm text-slate-500">
                                            {t(lt("当前没有新的系统摘要。", "No new system summaries right now."))}
                                        </div>
                                    )}
                                </div>
                            </Card>
                        ) : null}
                    </div>
                </div>
            </div>
        </header>
    );
}
