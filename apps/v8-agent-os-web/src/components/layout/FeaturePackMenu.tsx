"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { ArrowUpRight, CircleCheck, Loader2, Monitor, RefreshCw, ShieldAlert } from "lucide-react";
import { TopbarGlowActionButton } from "@/components/layout/TopbarGlowActionButton";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";

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

type ShellUpdateStatus = {
    state: "idle" | "checking" | "available" | "current" | "error";
    version?: string;
    currentVersion?: string;
    releaseUrl?: string;
    error?: string;
};

type V8OSUpdateState = {
    status: "available" | "current" | "incompatible" | "unavailable";
    currentVersion: string | null;
    latestVersion: string | null;
    releaseUrl: string | null;
    checkedAt: string;
    action: "open_release_page";
};

const V8OS_UPDATE_CACHE_TTL_MS = 5 * 60 * 1000;
const CONTROLLED_RELEASE_URL_RE = /^https:\/\/github\.com\/justForever17\/v8-agent-os\/releases\/tag\/v8-os-v20\d{2}\.(?:0[1-9]|1[0-2])\.(?:0[1-9]|[12]\d|3[01])\.(?:[1-9]|[1-9]\d)$/;

const subscribeToShellSurface = () => () => {};
const readShellSurface = () => Boolean(window.v8osShell?.isShell);
const readServerShellSurface = () => false;

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

export function FeaturePackMenu() {
    const t = useT();
    const { locale } = useLocale();
    const router = useRouter();
    const isShell = useSyncExternalStore(subscribeToShellSurface, readShellSurface, readServerShellSurface);

    const [isOpen, setIsOpen] = useState(false);
    const [installState, setInstallState] = useState<RuntimeFeaturePackState | null>(null);
    const [installLoading, setInstallLoading] = useState(false);
    const [isUnauthorized, setIsUnauthorized] = useState(false);
    const [installSubmittingPackId, setInstallSubmittingPackId] = useState<string | null>(null);

    const [v8osUpdateState, setV8osUpdateState] = useState<V8OSUpdateState | null>(null);
    const [v8osUpdateLoading, setV8osUpdateLoading] = useState(false);
    const [v8osUpdateError, setV8osUpdateError] = useState(false);

    const containerRef = useRef<HTMLDivElement | null>(null);
    const v8osUpdateLoadingRef = useRef(false);

    const loadInstallState = useCallback(async (force = false, silent = false, refreshHealth = false) => {
        if (!silent) setInstallLoading(true);
        try {
            const url = refreshHealth ? "/api/admin/runtime-feature-packs?refresh=1" : "/api/admin/runtime-feature-packs";
            const response = await fetch(url, {
                cache: force ? "no-store" : "default",
            });
            if (response.status === 401) {
                setIsUnauthorized(true);
                return;
            }
            if (!response.ok) {
                throw new Error(`Failed to load: ${response.status}`);
            }
            const payload: RuntimeFeaturePackState = await response.json();
            setIsUnauthorized(false);
            setInstallState(payload);
        } catch (error) {
            console.error("Failed to load runtime feature pack state:", error);
        } finally {
            if (!silent) setInstallLoading(false);
        }
    }, []);

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
            const url = force ? "/api/admin/v8os-update?refresh=1" : "/api/admin/v8os-update";
            const response = await fetch(url, {
                cache: force ? "no-store" : "default",
            });
            if (response.ok) {
                const payload: V8OSUpdateState = await response.json();
                setV8osUpdateState(payload);
                setV8osUpdateError(payload.status === "unavailable");
            } else {
                setV8osUpdateError(true);
            }
        } catch (error) {
            console.error("Failed to check V8OS release state:", error);
            setV8osUpdateError(true);
        } finally {
            v8osUpdateLoadingRef.current = false;
            setV8osUpdateLoading(false);
        }
    }, []);

    const toggleOpen = useCallback(() => {
        setIsOpen((prev) => {
            const next = !prev;
            if (next) {
                void loadInstallState(true, installState !== null);
                void loadV8OSUpdateState(false);
            }
            return next;
        });
    }, [installState, loadInstallState, loadV8OSUpdateState]);

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        };
        if (isOpen) {
            document.addEventListener("mousedown", handleClickOutside);
        }
        return () => {
            document.removeEventListener("mousedown", handleClickOutside);
        };
    }, [isOpen]);

    useEffect(() => {
        const openFeaturePacks = () => {
            setIsOpen(true);
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
            const response = await fetch("/api/admin/runtime-feature-packs", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ packId, locale }),
            });
            if (response.status === 401) {
                setIsUnauthorized(true);
                return;
            }
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(typeof payload.error === "string" ? payload.error : `Request failed (${response.status})`);
            }
            void loadInstallState(true, true, true);
        } catch (error) {
            console.error("Failed to start feature pack install:", error);
        } finally {
            setInstallSubmittingPackId(null);
        }
    }, [loadInstallState, locale]);

    const featurePackLabel = t("components.layout.Topbar.featurePacksLabel");
    const visibleFeaturePacks = (installState?.packs || []).filter((pack) => pack.installable || pack.installed);
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
                setIsOpen(false);
            }
            return;
        }
        if (controlledUpdateUrl) {
            window.open(controlledUpdateUrl, "_blank", "noopener,noreferrer");
            setIsOpen(false);
        }
    }, [controlledUpdateUrl]);

    const handleOpenAdmin = useCallback(() => {
        setIsOpen(false);
        if (isShell && window.v8osShell?.openAdmin) {
            window.v8osShell.openAdmin();
        } else {
            router.push("/admin");
        }
    }, [isShell, router]);

    return (
        <div ref={containerRef} className="relative">
            <TopbarGlowActionButton
                tone="emerald"
                onClick={toggleOpen}
                aria-label={featurePackButtonTitle}
                title={featurePackButtonTitle}
                aria-expanded={isOpen}
            >
                <Monitor className="h-4 w-4" />
            </TopbarGlowActionButton>
            {isOpen ? (
                <Card className="fixed left-2 right-2 top-12 z-50 mt-2 h-[calc(100dvh-4rem)] max-h-[40rem] w-auto overflow-hidden rounded-lg border-border bg-card/95 p-0 shadow-2xl dark:border-white/10 dark:bg-zinc-950/95 sm:absolute sm:left-auto sm:right-0 sm:top-full sm:h-[calc(100dvh-5.5rem)] sm:w-[22rem] sm:max-w-[calc(100vw-1rem)]">
                    <ScrollArea className="h-full min-w-0 max-w-full overflow-x-hidden">
                        <div className="w-full min-w-0 max-w-full overflow-x-hidden p-3 pr-4 text-sm text-muted-foreground dark:text-slate-300">
                            <div className="flex items-center justify-between gap-3 pb-3">
                                <div className="text-sm font-semibold text-foreground dark:text-slate-100">{featurePackLabel}</div>
                                {installState ? (
                                    <span className="text-[11px] tabular-nums text-muted-foreground">
                                        {installState.summary.installed}/{visibleFeaturePacks.length}
                                    </span>
                                ) : null}
                            </div>

                            {/* V8OS Updates Section */}
                            <section className="-mx-3 border-y border-border/70 bg-muted/50 px-3 py-3 dark:border-white/10 dark:bg-white/[0.025]" aria-labelledby="v8os-update-title">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <div id="v8os-update-title" className="text-sm font-semibold text-foreground dark:text-slate-100">
                                            {t("components.layout.Topbar.v8osUpdateTitle")}
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
                                <div className="mt-2 text-[11px] leading-4 text-muted-foreground" role="status" aria-live="polite">
                                    {t(`components.layout.Topbar.v8osUpdateStatus.${updateStatusKey}`)}
                                </div>
                                <div className="mt-3 flex flex-wrap items-center gap-2">
                                    <Button
                                        type="button"
                                        size="sm"
                                        variant="outline"
                                        className="h-8 rounded-md px-2.5"
                                        onClick={() => void loadV8OSUpdateState(true)}
                                        disabled={v8osUpdateLoading}
                                    >
                                        <RefreshCw className={cn("mr-1.5 h-3.5 w-3.5", v8osUpdateLoading && "animate-spin")} />
                                        {t("components.layout.Topbar.v8osUpdateCheck")}
                                    </Button>
                                    {updateAvailable ? (
                                        <Button type="button" size="sm" className="h-8 rounded-md px-2.5" onClick={() => void openV8OSUpdate()}>
                                            {t("components.layout.Topbar.v8osUpdateAction")}
                                            <ArrowUpRight className="ml-1.5 h-3.5 w-3.5" />
                                        </Button>
                                    ) : (
                                        <Button type="button" size="sm" className="h-8 rounded-md px-2.5" disabled>
                                            {t("components.layout.Topbar.v8osUpdateAction")}
                                        </Button>
                                    )}
                                </div>
                            </section>

                            {/* Feature Packs List or Unauthorized Notice */}
                            <div className="mt-1">
                                {isUnauthorized ? (
                                    <div className="my-3 rounded-lg border border-amber-200/50 bg-amber-50/50 p-3 text-center dark:border-amber-500/20 dark:bg-amber-500/10">
                                        <ShieldAlert className="mx-auto h-5 w-5 text-amber-600 dark:text-amber-400" />
                                        <div className="mt-1.5 text-xs text-muted-foreground dark:text-amber-200/90">
                                            {t("components.layout.Topbar.featurePacksAdminRequired")}
                                        </div>
                                        <Button
                                            size="sm"
                                            className="mt-2.5 h-7 w-full rounded-md text-xs"
                                            onClick={handleOpenAdmin}
                                        >
                                            {t("components.layout.Topbar.featurePacksOpenAdmin")}
                                        </Button>
                                    </div>
                                ) : installLoading && !installState ? (
                                    <div className="flex h-28 items-center justify-center">
                                        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                                    </div>
                                ) : (
                                    visibleFeaturePacks.map((pack) => {
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
                                        const hover = t(`${packI18nKey}.hover`);

                                        return (
                                            <div key={pack.id} className="w-full min-w-0 border-b border-border/60 py-3 last:border-b-0 dark:border-white/10" title={hover}>
                                                <div className="flex min-w-0 items-center gap-2">
                                                    <div className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground dark:text-slate-100">{productName}</div>
                                                    <span className={cn(
                                                        "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold",
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
                                                    {isInstalling ? (
                                                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-sky-600 dark:text-sky-200" />
                                                    ) : showInstall ? (
                                                        <Button size="sm" className="h-7 shrink-0 rounded-md px-2.5" disabled={!canInstall} onClick={() => void handleInstallFeaturePack(pack.id)}>
                                                            {t("components.layout.Topbar.featurePackInstall")}
                                                        </Button>
                                                    ) : null}
                                                </div>
                                                {pack.hasError ? (
                                                    <div className="mt-1.5 truncate text-[11px] text-rose-700 dark:text-rose-200" title={pack.logName || undefined}>
                                                        {t("components.layout.Topbar.featurePackInstallFailedDetail")}
                                                    </div>
                                                ) : null}
                                                {pack.restartRequired ? (
                                                    <div className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
                                                        {t("components.layout.Topbar.featurePackRestartRequired")}
                                                    </div>
                                                ) : null}
                                            </div>
                                        );
                                    })
                                )}
                            </div>
                        </div>
                    </ScrollArea>
                </Card>
            ) : null}
        </div>
    );
}
