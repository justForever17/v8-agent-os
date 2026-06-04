"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";
import { CANONICAL_RUNTIME_KINDS, CORE_RUNTIME_KINDS, isCoreRuntimeKind, getRuntimeDisplayName, isCanonicalRuntimeKind, getRuntimeControlHref, getRuntimeDisplayText, isLockedRuntimeKind } from "@/lib/runtime-admin";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";

type RuntimePolicy = {
    enabled?: boolean;
    priority?: number;
};

export type RuntimeDescriptor = {
    kind: string;
    displayName: string;
    policy?: RuntimePolicy;
    registered?: boolean;
    availability?: string;
    availabilityReason?: string;
};

type RuntimeSnapshot = {
    runtimes?: RuntimeDescriptor[];
};

const CORE_SORT_ORDER = new Map<string, number>(CORE_RUNTIME_KINDS.map((kind, index) => [kind, index]));
const CANONICAL_SORT_ORDER = new Map<string, number>(CANONICAL_RUNTIME_KINDS.map((kind, index) => [kind, index]));
const HIDDEN_DASHBOARD_RUNTIME_KINDS = new Set<string>(["desktop_live"]);

function sortRuntimes(items: RuntimeDescriptor[]) {
    return [...items].sort((left, right) => {
        const leftCore = isCoreRuntimeKind(left.kind);
        const rightCore = isCoreRuntimeKind(right.kind);
        if (leftCore !== rightCore) {
            return leftCore ? -1 : 1;
        }
        if (leftCore && rightCore) {
            return (CORE_SORT_ORDER.get(left.kind) ?? 99) - (CORE_SORT_ORDER.get(right.kind) ?? 99);
        }
        const leftPriority = typeof left.policy?.priority === "number" ? left.policy.priority : 100;
        const rightPriority = typeof right.policy?.priority === "number" ? right.policy.priority : 100;
        if (leftPriority !== rightPriority) {
            return leftPriority - rightPriority;
        }
        const leftCanonical = CANONICAL_SORT_ORDER.get(left.kind);
        const rightCanonical = CANONICAL_SORT_ORDER.get(right.kind);
        if (leftCanonical !== undefined || rightCanonical !== undefined) {
            return (leftCanonical ?? 999) - (rightCanonical ?? 999);
        }
        return left.kind.localeCompare(right.kind);
    });
}

function mergeRuntimeSnapshot(items: RuntimeDescriptor[]) {
    const byKind = new Map<string, RuntimeDescriptor>();
    for (const item of items) {
        const normalizedKind = String(item.kind || "").trim();
        if (!normalizedKind) continue;
        byKind.set(normalizedKind, {
            ...item,
            kind: normalizedKind,
            registered: item.registered !== false,
        });
    }

    const merged: RuntimeDescriptor[] = [];
    for (const kind of CANONICAL_RUNTIME_KINDS) {
        const existing = byKind.get(kind);
        if (existing) {
            merged.push(existing);
            byKind.delete(kind);
            continue;
        }
        merged.push({
            kind,
            displayName: getRuntimeDisplayName({ kind }),
            policy: { enabled: true },
            registered: false,
        });
    }

    for (const runtime of byKind.values()) {
        merged.push(runtime);
    }
    return sortRuntimes(merged).filter((runtime) => !HIDDEN_DASHBOARD_RUNTIME_KINDS.has(runtime.kind));
}

function resolveRuntimeStateLabel(t: ReturnType<typeof useT>, runtime: RuntimeDescriptor) {
    const availability = String(runtime.availabilityReason || runtime.availability || "installed").trim();
    if (availability === "not_installed") {
        return t("components.runtime.RuntimeDashboardCards.k95c16ca5");
    }
    if (availability === "disabled_by_config") {
        return t("components.runtime.RuntimeDashboardCards.k9b3f5673");
    }
    if (availability === "disabled_by_policy" || runtime.policy?.enabled === false) {
        return t("components.runtime.RuntimeDashboardCards.k574ff3b2");
    }
    return t("components.runtime.RuntimeDashboardCards.kf34b4be4");
}

function resolveRuntimeToggleChecked(runtime: RuntimeDescriptor) {
    const availability = String(runtime.availabilityReason || runtime.availability || "installed").trim();
    if (availability === "not_installed" || availability === "disabled_by_config") {
        return false;
    }
    return runtime.policy?.enabled !== false;
}

export function RuntimeDashboardCards() {
    const t = useT();
    const { toast } = useToast();
    const [loading, setLoading] = useState(true);
    const [busyKind, setBusyKind] = useState<string | null>(null);
    const [runtimes, setRuntimes] = useState<RuntimeDescriptor[]>([]);

    const loadSnapshot = useCallback(async () => {
        setLoading(true);
        try {
            const response = await fetch("/api/runtime-capabilities", { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`Runtime capabilities failed: ${response.status}`);
            }
            const payload: RuntimeSnapshot = await response.json().catch(() => ({}));
            setRuntimes(mergeRuntimeSnapshot(payload.runtimes || []));
        } catch (error) {
            console.error("Failed to load runtime cards:", error);
            toast({
                title: t("components.runtime.RuntimeDashboardCards.kfa4f38cd"),
                description: t("components.runtime.RuntimeDashboardCards.kd47576cd"),
                variant: "destructive",
            });
        } finally {
            setLoading(false);
        }
    }, [t, toast]);

    useEffect(() => {
        void loadSnapshot();
    }, [loadSnapshot]);

    const groups = useMemo(() => {
        const core = runtimes.filter((item) => isCoreRuntimeKind(item.kind));
        const others = runtimes.filter((item) => !isCoreRuntimeKind(item.kind));
        return [
            { key: "core", title: t("components.runtime.RuntimeDashboardCards.k095533f5"), items: core },
            { key: "other", title: t("components.runtime.RuntimeDashboardCards.kf484a950"), items: others },
        ].filter((group) => group.items.length > 0);
    }, [runtimes, t]);



    const handleToggle = useCallback(async (runtime: RuntimeDescriptor, checked: boolean) => {
        if (isCoreRuntimeKind(runtime.kind)) {
            return;
        }
        setBusyKind(runtime.kind);
        try {
            const response = await fetch(`/api/runtime-capabilities/${encodeURIComponent(runtime.kind)}/policy`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled: checked }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(payload?.detail || payload?.error || response.status));
            }
            setRuntimes(mergeRuntimeSnapshot((payload?.snapshot?.runtimes as RuntimeDescriptor[] | undefined) || []));
        } catch (error) {
            console.error("Failed to toggle runtime:", error);
            toast({
                title: t("components.runtime.RuntimeDashboardCards.k3cccf1fc"),
                description: t("components.runtime.RuntimeDashboardCards.k9653e087"),
                variant: "destructive",
            });
        } finally {
            setBusyKind(null);
        }
    }, [t, toast]);

    return (
        <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
            <CardHeader className="space-y-1 pb-4">
                <CardTitle className="text-lg text-slate-900">{t("components.runtime.RuntimeDashboardCards.k67336fc2")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
                {loading ? (
                    <div className="flex h-28 items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50/80">
                        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
                    </div>
                ) : (
                    groups.map((group) => (
                        <div key={group.key} className="space-y-3">
                            <div className="text-[11px] font-semibold uppercase tracking-[0.26em] text-slate-400">
                                {group.title}
                            </div>
                            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                                {group.items.map((runtime) => {
                                    const enabled = resolveRuntimeToggleChecked(runtime);
                                    const availability = String(runtime.availabilityReason || runtime.availability || "installed").trim();
                                    const disabled =
                                        isLockedRuntimeKind(runtime.kind) ||
                                        availability === "not_installed" ||
                                        availability === "disabled_by_config";
                                    const href = getRuntimeControlHref(runtime.kind);
                                    const pending = busyKind === runtime.kind;
                                    const stateLabel = resolveRuntimeStateLabel(t, runtime);
                                    const onDemand = availability === "installed" && !runtime.registered && isCanonicalRuntimeKind(runtime.kind);
                                    
                                    const label = isCanonicalRuntimeKind(runtime.kind)
                                        ? t(getRuntimeDisplayText(runtime.kind))
                                        : getRuntimeDisplayName(runtime);

                                    return (
                                        <SettingToggleCard
                                            key={runtime.kind}
                                            title={label}
                                            href={href}
                                            checked={enabled}
                                            disabled={disabled || pending}
                                            onCheckedChange={(checked) => void handleToggle(runtime, checked)}
                                            showStatusDot={true}
                                            statusDotEnabled={enabled}
                                            statusLabel={stateLabel}
                                            extraBadge={onDemand ? (
                                                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">
                                                    {t("components.runtime.RuntimeDashboardCards.k37f55da8")}
                                                </span>
                                            ) : null}
                                            className="rounded-2xl border-slate-200 bg-slate-50/70 shadow-none p-4"
                                        />
                                    );
                                })}
                            </div>
                        </div>
                    ))
                )}
            </CardContent>
        </Card>
    );
}
