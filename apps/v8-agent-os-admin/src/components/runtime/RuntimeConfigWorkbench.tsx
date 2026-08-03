"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, Loader2 } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchAdminJson, peekAdminJsonCache, primeAdminJsonCache } from "@/lib/admin-client-cache";
import { getRuntimeDisplayName, getRuntimeDisplayText, isCanonicalRuntimeKind, isLockedRuntimeKind } from "@/lib/runtime-admin";

type RuntimePolicy = {
    enabled?: boolean;
};

type RuntimeDescriptor = {
    kind: string;
    displayName: string;
    summary?: string;
    policy?: RuntimePolicy;
    availability?: string;
    availabilityReason?: string;
};

type RuntimeSnapshot = {
    runtimes?: RuntimeDescriptor[];
};

const RUNTIME_CAPABILITIES_URL = "/api/runtime-capabilities";

function resolveRuntimeToggleChecked(runtime: RuntimeDescriptor | null) {
    if (!runtime) return false;
    const availability = String(runtime.availabilityReason || runtime.availability || "installed").trim();
    if (availability === "not_installed" || availability === "disabled_by_config") {
        return false;
    }
    return runtime.policy?.enabled !== false;
}

export function RuntimeConfigWorkbench({
    kind,
    fallbackDisplayName,
    governanceHref = "/admin/runtime-governance",
    showGovernanceLink = true,
}: {
    kind: string;
    fallbackDisplayName: string;
    governanceHref?: string;
    showGovernanceLink?: boolean;
}) {
    const t = useT();
    const { toast } = useToast();
    const cachedSnapshot = peekAdminJsonCache<RuntimeSnapshot>(RUNTIME_CAPABILITIES_URL);
    const [loading, setLoading] = useState(() => !cachedSnapshot);
    const [saving, setSaving] = useState(false);
    const [runtime, setRuntime] = useState<RuntimeDescriptor | null>(
        () => (cachedSnapshot?.runtimes || []).find((item) => item.kind === kind) || null,
    );

    const loadRuntime = useCallback(async (force = false) => {
        if (!peekAdminJsonCache<RuntimeSnapshot>(RUNTIME_CAPABILITIES_URL)) setLoading(true);
        try {
            const payload = await fetchAdminJson<RuntimeSnapshot>(RUNTIME_CAPABILITIES_URL, { force });
            const matched = (payload.runtimes || []).find((item) => item.kind === kind) || null;
            setRuntime(matched);
        } catch (error) {
            console.error("Failed to load runtime config workbench:", error);
            toast({
                title: t("components.runtime.RuntimeConfigWorkbench.kfa4f38cd"),
                description: t("components.runtime.RuntimeConfigWorkbench.kceeaf2ae"),
                variant: "destructive",
            });
        } finally {
            setLoading(false);
        }
    }, [kind, t, toast]);

    useEffect(() => {
        void loadRuntime();
    }, [loadRuntime]);

    const displayName = useMemo(
        () =>
            isCanonicalRuntimeKind(kind)
                ? t(getRuntimeDisplayText(kind))
                : getRuntimeDisplayName({ kind, displayName: runtime?.displayName || fallbackDisplayName }),
        [fallbackDisplayName, kind, runtime?.displayName, t],
    );

    const enabled = resolveRuntimeToggleChecked(runtime);
    const availability = String(runtime?.availabilityReason || runtime?.availability || "installed").trim();
    const disabled =
        isLockedRuntimeKind(kind) ||
        !runtime ||
        availability === "not_installed" ||
        availability === "disabled_by_config";

    const handleToggle = useCallback(async (checked: boolean) => {
        if (disabled) return;
        setSaving(true);
        try {
            const response = await fetch(`/api/runtime-capabilities/${encodeURIComponent(kind)}/policy`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled: checked }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(String(payload?.detail || payload?.error || response.status));
            }
            const snapshot = (payload?.snapshot || {}) as RuntimeSnapshot;
            primeAdminJsonCache(RUNTIME_CAPABILITIES_URL, snapshot);
            const matched = (snapshot.runtimes || []).find((item) => item.kind === kind) || null;
            setRuntime(matched);
        } catch (error) {
            console.error("Failed to save runtime config:", error);
            toast({
                title: t("components.runtime.RuntimeConfigWorkbench.k12769ce1"),
                description: t("components.runtime.RuntimeConfigWorkbench.k9653e087"),
                variant: "destructive",
            });
        } finally {
            setSaving(false);
        }
    }, [disabled, kind, t, toast]);

    return (
        <Card className="rounded-3xl border-border bg-card shadow-sm">
            <CardContent className="space-y-4 p-6">
                {loading ? (
                    <div className="flex h-24 items-center justify-center rounded-2xl border border-dashed border-border bg-muted/40">
                        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    </div>
                ) : (
                    <>
                        <SettingToggleCard
                            title={displayName}
                            description={
                                runtime
                                    ? (
                                        availability === "not_installed"
                                            ? t("components.runtime.RuntimeConfigWorkbench.k67cba518")
                                            : availability === "disabled_by_config"
                                                ? t("components.runtime.RuntimeConfigWorkbench.k23b359ee")
                                            : enabled
                                                ? t("components.runtime.RuntimeConfigWorkbench.k465806e6")
                                                : t("components.runtime.RuntimeConfigWorkbench.kc3525b80")
                                    )
                                    : t("components.runtime.RuntimeConfigWorkbench.k7fa15272")
                            }
                            checked={enabled}
                            disabled={disabled || saving}
                            onCheckedChange={(checked) => void handleToggle(checked)}
                            className="border-none bg-transparent hover:bg-transparent p-0 shadow-none"
                        />
                        {showGovernanceLink ? (
                            <div className="flex justify-end">
                                <Link
                                    href={governanceHref}
                                    className="inline-flex items-center gap-2 text-sm font-medium text-sky-700 transition hover:text-sky-800 dark:text-sky-300 dark:hover:text-sky-200"
                                >
                                    {t("components.runtime.RuntimeConfigWorkbench.k103f21d3")}
                                    <ArrowRight className="h-4 w-4" />
                                </Link>
                            </div>
                        ) : null}
                    </>
                )}
            </CardContent>
        </Card>
    );
}
