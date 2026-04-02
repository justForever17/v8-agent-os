"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/use-toast";
import { lt } from "@/lib/locale";
import { CANONICAL_RUNTIME_KINDS, CORE_RUNTIME_KINDS, getRuntimeControlHref, getRuntimeDisplayName, getRuntimeDisplayText, isCanonicalRuntimeKind, isCoreRuntimeKind, isLockedRuntimeKind } from "@/lib/runtime-admin";
import { cn } from "@/lib/utils";

type RuntimePolicy = {
    enabled?: boolean;
    priority?: number;
};

type RuntimeDescriptor = {
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
    return sortRuntimes(merged);
}

function StatusDot({ enabled }: { enabled: boolean }) {
    return (
        <span
            className={cn(
                "inline-flex h-2.5 w-2.5 shrink-0 rounded-full",
                enabled ? "bg-emerald-500 shadow-[0_0_0_4px_rgba(16,185,129,0.12)]" : "bg-rose-500 shadow-[0_0_0_4px_rgba(244,63,94,0.12)]",
            )}
            aria-hidden="true"
        />
    );
}

function resolveRuntimeStateLabel(t: ReturnType<typeof useT>, runtime: RuntimeDescriptor) {
    const availability = String(runtime.availabilityReason || runtime.availability || "installed").trim();
    if (availability === "not_installed") {
        return t(lt("未安装", "Not installed"));
    }
    if (availability === "disabled_by_config") {
        return t(lt("配置关闭", "Disabled by config"));
    }
    if (availability === "disabled_by_policy" || runtime.policy?.enabled === false) {
        return t(lt("关闭", "Off"));
    }
    return t(lt("启用", "On"));
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
                title: t(lt("Runtime 加载失败", "Runtime load failed")),
                description: t(lt("当前无法读取 runtime 状态。", "Unable to read runtime status right now.")),
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
            { key: "core", title: t(lt("核心", "Core")), items: core },
            { key: "other", title: t(lt("扩展", "More")), items: others },
        ].filter((group) => group.items.length > 0);
    }, [runtimes, t]);

    const resolveRuntimeLabel = useCallback((runtime: RuntimeDescriptor) => {
        if (isCanonicalRuntimeKind(runtime.kind)) {
            return t(getRuntimeDisplayText(runtime.kind));
        }
        return getRuntimeDisplayName(runtime);
    }, [t]);

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
                title: t(lt("切换失败", "Toggle failed")),
                description: t(lt("当前无法更新 runtime 状态。", "Unable to update runtime state right now.")),
                variant: "destructive",
            });
        } finally {
            setBusyKind(null);
        }
    }, [t, toast]);

    return (
        <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
            <CardHeader className="space-y-1 pb-4">
                <CardTitle className="text-lg text-slate-900">{t(lt("Runtimes", "Runtimes"))}</CardTitle>
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
                                    return (
                                        <Card key={runtime.kind} className="rounded-2xl border-slate-200 bg-slate-50/70 shadow-none">
                                            <CardContent className="flex items-center justify-between gap-4 p-4">
                                                <div className="min-w-0 space-y-2">
                                                    <Link
                                                        href={href}
                                                        className="block truncate text-sm font-semibold text-slate-900 transition hover:text-sky-700"
                                                    >
                                                        {resolveRuntimeLabel(runtime)}
                                                    </Link>
                                                    <div className="flex items-center gap-2 text-xs text-slate-500">
                                                        <StatusDot enabled={enabled} />
                                                        <span>{stateLabel}</span>
                                                        {onDemand ? (
                                                            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">
                                                                {t(lt("按需调用", "On demand"))}
                                                            </span>
                                                        ) : null}
                                                    </div>
                                                </div>
                                                <Switch
                                                    checked={enabled}
                                                    disabled={disabled || pending}
                                                    onCheckedChange={(checked) => void handleToggle(runtime, checked)}
                                                    aria-label={`${resolveRuntimeLabel(runtime)} toggle`}
                                                    className={disabled ? "data-[state=checked]:bg-slate-300 data-[state=unchecked]:bg-slate-200" : undefined}
                                                />
                                            </CardContent>
                                        </Card>
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
