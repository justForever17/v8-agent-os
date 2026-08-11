"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { Loader2, Workflow } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { ModelSelect } from "@/components/models/ModelSelect";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useT } from "@/components/providers/LocaleProvider";
import {
    fetchConfigDomain,
    peekConfigDomain,
    saveConfigDomain,
    type ConfigRegistryEnvelope,
} from "@/lib/config-registry";
import { fetchAdminJson, peekAdminJsonCache } from "@/lib/admin-client-cache";

const RPAWorkbench = dynamic(
    () => import("@/components/rpa/RPAWorkbench").then((mod) => mod.RPAWorkbench),
    {
        loading: () => (
            <div className="flex min-h-[280px] items-center justify-center rounded-2xl border border-border bg-card shadow-sm">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground/80" />
            </div>
        ),
    }
);

type ModelOption = {
    id?: string;
    modelRef?: string;
    providerId?: string;
    modelId: string;
    name: string;
    type: string;
    provider?: { id?: string; name?: string };
    providerName?: string;
};

type RpaData = {
    modelBindings: {
        discoveryModel: string;
    };
    executionPolicy: {
        runtimeFirst: boolean;
        localRecoveryPreferred: boolean;
        sideEffectIdempotency: boolean;
    };
};

type RuntimeCapabilityEntry = {
    kind: string;
    displayName?: string;
    summary?: string;
    availability?: string;
    policy?: {
        enabled?: boolean;
        autoRoute?: boolean;
        exposeDirectTools?: boolean;
    };
};

type RuntimeFeaturePackEntry = {
    id?: string;
    status?: string;
    restartRequired?: boolean;
};

type RpaAvailability = {
    robotFramework?: boolean;
    rpaFramework?: boolean;
    libraries?: Record<string, boolean>;
};

function runtimeCapabilityCanBeEnabled(capability: RuntimeCapabilityEntry | null) {
    return capability?.availability === "installed" || capability?.availability === "disabled_by_policy";
}

type FeaturePackGateState = "unknown" | "missing" | "restart_required" | "ready";

export default function RpaRuntimePage() {
    const t = useT();
    const [initialState] = useState(() => {
        const envelope = peekConfigDomain<RpaData>("rpa") ?? null;
        const models = peekAdminJsonCache<ModelOption[]>("/api/models");
        const capabilitySnapshot = peekAdminJsonCache<Record<string, unknown>>("/api/runtime-capabilities");
        const runtimes = Array.isArray(capabilitySnapshot?.runtimes) ? capabilitySnapshot.runtimes : [];
        const featurePacks = peekAdminJsonCache<{ packs?: RuntimeFeaturePackEntry[] }>("/api/runtime-feature-packs");
        const packs = Array.isArray(featurePacks?.packs) ? featurePacks.packs : [];
        const featurePackState: FeaturePackGateState = featurePacks !== undefined
            ? packs.find((item) => item.id === "rpa_automation")?.status === "installed" ? "restart_required" : "missing"
            : "unknown";
        return {
            envelope,
            models: Array.isArray(models) ? models : [],
            runtimeCapability: runtimes.find((item: RuntimeCapabilityEntry) => item.kind === "rpa") || null,
            featurePackState,
        };
    });
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<RpaData> | null>(initialState.envelope);
    const [models, setModels] = useState<ModelOption[]>(initialState.models);
    const [runtimeCapability, setRuntimeCapability] = useState<RuntimeCapabilityEntry | null>(initialState.runtimeCapability);
    const [loading, setLoading] = useState(!initialState.envelope);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [runtimeSaving, setRuntimeSaving] = useState(false);
    const [featurePackState, setFeaturePackState] = useState<FeaturePackGateState>(initialState.featurePackState);

    const loadData = async (force = false) => {
        try {
            const [config, modelList, capabilitySnapshot, featurePacks] = await Promise.all([
                fetchConfigDomain<RpaData>("rpa", { force }),
                fetchAdminJson<ModelOption[]>("/api/models", { force }),
                fetchAdminJson<Record<string, unknown>>("/api/runtime-capabilities", { force }),
                fetchAdminJson<{ packs?: RuntimeFeaturePackEntry[] }>("/api/runtime-feature-packs", { force })
                    .catch(() => null),
            ]);
            setEnvelope(config);
            setModels(Array.isArray(modelList) ? modelList : []);
            const runtimes = Array.isArray(capabilitySnapshot?.runtimes) ? capabilitySnapshot.runtimes : [];
            setRuntimeCapability(runtimes.find((item: RuntimeCapabilityEntry) => item.kind === "rpa") || null);
            if (featurePacks) {
                const packs = Array.isArray(featurePacks.packs) ? featurePacks.packs : [];
                const rpaPack = packs.find((item) => item.id === "rpa_automation");
                const rpaCapability = runtimes.find((item: RuntimeCapabilityEntry) => item.kind === "rpa") || null;
                if (rpaPack?.status !== "installed") {
                    setFeaturePackState("missing");
                } else if (rpaPack.restartRequired !== false || !runtimeCapabilityCanBeEnabled(rpaCapability)) {
                    setFeaturePackState("restart_required");
                } else {
                    const availability = await fetchAdminJson<RpaAvailability>("/api/rpa/availability", { force: true })
                        .catch(() => null);
                    setFeaturePackState(
                        availability?.robotFramework === true
                            && availability?.rpaFramework === true
                            && availability?.libraries?.["RPA.Browser.Selenium"] === true
                            && availability?.libraries?.["RPA.Excel.Files"] === true
                            ? "ready"
                            : "restart_required"
                    );
                }
            } else {
                setFeaturePackState("unknown");
            }
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadData();
    }, []);

    const llmModels = useMemo(
        () => models.filter((model) => ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes((model.type || "").toUpperCase())),
        [models]
    );

    const handleSave = async () => {
        if (!envelope) return;
        setSaving(true);
        try {
            const next = await saveConfigDomain<RpaData>("rpa", { data: envelope.data });
            setEnvelope(next);
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
        } finally {
            setSaving(false);
        }
    };

    const setRuntimeEnabled = async (enabled: boolean) => {
        if (enabled && featurePackState !== "ready") {
            if (featurePackState === "missing") {
                window.dispatchEvent(new Event("v8os:open-feature-packs"));
            } else {
                void loadData(true);
            }
            return;
        }
        setRuntimeSaving(true);
        try {
            await fetch("/api/runtime-capabilities/rpa/policy", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    enabled,
                    autoRoute: enabled,
                    exposeDirectTools: enabled,
                    notes: enabled ? "Admin rpa enabled" : "Admin rpa disabled",
                }),
            });
            await loadData(true);
        } finally {
            setRuntimeSaving(false);
        }
    };

    if (loading || !envelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground/80" />
            </div>
        );
    }

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={"app.admin.dashboard.rpa.page.kf4573dc1"}
                description={"app.admin.dashboard.rpa.page.kcc16b8bd"} 
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void handleSave()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Workflow className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.rpa.page.k6010e1ed")}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip
                items={[
                    { label: "app.admin.dashboard.rpa.page.kac114a5d", value: envelope.data.modelBindings.discoveryModel ? t("app.admin.dashboard.rpa.page.k7b68df0c") : t("app.admin.dashboard.rpa.page.k54745147"), description: "app.admin.dashboard.rpa.page.k091a9e28" },
                    { label: "app.admin.dashboard.rpa.page.k412949c6", value: envelope.data.executionPolicy.runtimeFirst ? t("app.admin.dashboard.rpa.page.k8702fc03") : t("app.admin.dashboard.rpa.page.k15545a62"), description: "app.admin.dashboard.rpa.page.kd7db31e0" },
                    { label: "app.admin.dashboard.rpa.page.kf49515d3", value: envelope.data.executionPolicy.sideEffectIdempotency ? t("app.admin.dashboard.rpa.page.k1f61b6f9") : t("app.admin.dashboard.rpa.page.k2cbc2a89"), description: "app.admin.dashboard.rpa.page.k0878d653" },
                    { label: "app.admin.dashboard.rpa.page.k8225c664", value: envelope.data.executionPolicy.localRecoveryPreferred ? t("app.admin.dashboard.rpa.page.kd7741288") : t("app.admin.dashboard.rpa.page.kf62f97c0"), description: "app.admin.dashboard.rpa.page.kab062523" },
                ]}
            />

            <ConfigCard title={"app.admin.dashboard.rpa.page.kac114a5d"} description={"app.admin.dashboard.rpa.page.ke461397f"}>
                <div className="space-y-2">
                    <Label>{t("app.admin.dashboard.rpa.page.k2948258d")}</Label>
                    <ModelSelect
                        models={llmModels}
                        value={envelope.data.modelBindings.discoveryModel || "__empty__"}
                        emptyLabel={t("app.admin.dashboard.rpa.page.k54745147")}
                        placeholder={t("app.admin.dashboard.rpa.page.k54745147")}
                        enforceTextContextWindow={false}
                        onValueChange={(value) =>
                            setEnvelope({
                                ...envelope,
                                data: {
                                    ...envelope.data,
                                    modelBindings: {
                                        ...envelope.data.modelBindings,
                                        discoveryModel: value,
                                    },
                                },
                            })
                        }
                    />
                </div>
            </ConfigCard>

            <StatusNotice title={"app.admin.dashboard.rpa.page.ka1059631"} tone="info" />

            {featurePackState === "missing" ? (
                <StatusNotice
                    title={"app.admin.dashboard.rpa.featurePackMissingTitle"}
                    description={"app.admin.dashboard.rpa.featurePackMissingDescription"}
                    tone="warning"
                />
            ) : null}

            <ConfigCard title={"app.admin.dashboard.rpa.page.kb6443896"} description={"app.admin.dashboard.rpa.page.kb0b0faae"}>
                <div className="space-y-3">
                    <div className="grid gap-3 text-sm text-muted-foreground sm:grid-cols-2">
                        <div className="rounded-2xl border border-border bg-muted/80 p-4">
                            <div className="text-xs text-muted-foreground">{t("app.admin.dashboard.rpa.page.k73b0e79c")}</div>
                            <div className="mt-1 text-base font-semibold text-foreground">
                                {runtimeCapability?.policy?.enabled === false ? t("app.admin.dashboard.rpa.page.kc6ff9900") : t("app.admin.dashboard.rpa.page.kdb6c0cc1")}
                            </div>
                            <div className="mt-2 text-xs text-muted-foreground">
                            {runtimeCapability?.displayName || "RPA Runtime"} · {runtimeCapability?.summary || t("app.admin.dashboard.rpa.page.k846fddf9")}
                            </div>
                        </div>
                        <div className="rounded-2xl border border-border bg-muted/80 p-4 text-xs leading-6 text-muted-foreground">
                            <div>{t("app.admin.dashboard.rpa.page.k660c8c15")}{runtimeCapability?.policy?.autoRoute === false ? t("app.admin.dashboard.rpa.page.k574ff3b2") : t("app.admin.dashboard.rpa.page.k85549844")}</div>
                            <div>{t("app.admin.dashboard.rpa.page.k320d306e")}{runtimeCapability?.policy?.exposeDirectTools === false ? t("app.admin.dashboard.rpa.page.k574ff3b2") : t("app.admin.dashboard.rpa.page.k85549844")}</div>
                            <div>{t("app.admin.dashboard.rpa.page.kb135dd5b")}</div>
                        </div>
                    </div>
                    <div className="flex flex-wrap gap-3">
                        <Button type="button" onClick={() => void setRuntimeEnabled(true)} disabled={runtimeSaving || runtimeCapability?.policy?.enabled !== false}>
                            {runtimeSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t("app.admin.dashboard.rpa.page.k6a58f024")}
                        </Button>
                        <Button type="button" variant="outline" onClick={() => void setRuntimeEnabled(false)} disabled={runtimeSaving || runtimeCapability?.policy?.enabled === false}>
                            {runtimeSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t("app.admin.dashboard.rpa.page.kff9e27eb")}
                        </Button>
                    </div>
                </div>
            </ConfigCard>

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />

            <AdvancedSection
                description={"app.admin.dashboard.rpa.page.k6bc68ae7"}
                defaultOpen={false}
            >
                {featurePackState !== "ready" ? (
                    <div className="flex items-center justify-between gap-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-500/35 dark:bg-amber-500/10">
                        <span className="text-sm text-amber-800 dark:text-amber-200">
                            {t(featurePackState === "missing"
                                ? "app.admin.dashboard.rpa.featurePackMissingDescription"
                                : featurePackState === "restart_required"
                                ? "components.layout.Topbar.featurePackRestartRequired"
                                : "app.admin.dashboard.rpa.featurePackUnknownDescription")}
                        </span>
                        <Button
                            type="button"
                            className="shrink-0"
                            onClick={() => {
                                if (featurePackState === "missing") {
                                    window.dispatchEvent(new Event("v8os:open-feature-packs"));
                                } else {
                                    void loadData(true);
                                }
                            }}
                        >
                            {t(featurePackState === "missing"
                                ? "components.layout.Topbar.featurePackInstall"
                                : "app.admin.dashboard.rpa.featurePackRetry")}
                        </Button>
                    </div>
                ) : (
                    <RPAWorkbench />
                )}
            </AdvancedSection>
        </AdminPageShell>
    );
}
