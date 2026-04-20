"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Loader2, MonitorSmartphone } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";

type ModelOption = {
    id: string;
    modelId: string;
    name: string;
    type: string;
    provider?: { name?: string };
    providerName?: string;
};

type ComputerUseData = {
    modelBindings: {
        plannerModel: string;
        visualJudgeModel: string;
        ocrAssistModel: string;
        candidateRerankerModel: string;
        fallbackRerankerModel?: string;
    };
    candidateRerankEnabled: boolean;
    memoryProfiles: {
        version?: number;
        apps?: Record<string, unknown>;
    };
    environmentPolicy: {
        runtimeFirst: boolean;
        interruptRequiresBlocker: boolean;
        noiseIgnoredByDefault: boolean;
    };
};

type RuntimeCapabilityEntry = {
    kind: string;
    displayName?: string;
    summary?: string;
    policy?: {
        enabled?: boolean;
        autoRoute?: boolean;
        exposeDirectTools?: boolean;
    };
};

export default function DesktopAutomationPage() {
    const t = useT();
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<ComputerUseData> | null>(null);
    const [models, setModels] = useState<ModelOption[]>([]);
    const [runtimeCapability, setRuntimeCapability] = useState<RuntimeCapabilityEntry | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [runtimeSaving, setRuntimeSaving] = useState(false);

    const loadData = async () => {
        setLoading(true);
        try {
            const [config, modelsResponse, capabilitySnapshot] = await Promise.all([
                fetchConfigDomain<ComputerUseData>("computer-use"),
                fetch("/api/models", { cache: "no-store" }).then((response) => response.json().catch(() => [])),
                fetch("/api/runtime-capabilities", { cache: "no-store" }).then((response) => response.json().catch(() => ({}))),
            ]);
            setEnvelope(config);
            setModels(Array.isArray(modelsResponse) ? modelsResponse : []);
            const runtimes = Array.isArray(capabilitySnapshot?.runtimes) ? capabilitySnapshot.runtimes : [];
            setRuntimeCapability(runtimes.find((item: RuntimeCapabilityEntry) => item.kind === "computer_use") || null);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadData();
    }, []);

    const appCount = useMemo(() => Object.keys(envelope?.data.memoryProfiles?.apps || {}).length, [envelope]);
    const llmModels = useMemo(
        () => models.filter((model) => ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes((model.type || "").toUpperCase())),
        [models]
    );
    const rerankModels = useMemo(
        () => models.filter((model) => ["RERANK", "RERANKER"].includes((model.type || "").toUpperCase())),
        [models]
    );

    const updateBinding = (key: keyof ComputerUseData["modelBindings"], value: string) => {
        if (!envelope) return;
        setEnvelope({
            ...envelope,
            data: {
                ...envelope.data,
                modelBindings: {
                    ...envelope.data.modelBindings,
                    [key]: value === "__empty__" ? "" : value,
                },
            },
        });
    };

    const handleSave = async () => {
        if (!envelope) return;
        setSaving(true);
        try {
            const next = await saveConfigDomain<ComputerUseData>("computer-use", { data: envelope.data });
            setEnvelope(next);
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
        } finally {
            setSaving(false);
        }
    };

    const setRuntimeEnabled = async (enabled: boolean) => {
        setRuntimeSaving(true);
        try {
            await fetch("/api/runtime-capabilities/computer_use/policy", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    enabled,
                    autoRoute: enabled,
                    exposeDirectTools: enabled,
                    notes: enabled ? "Admin desktop-automation enabled" : "Admin desktop-automation disabled",
                }),
            });
            await loadData();
        } finally {
            setRuntimeSaving(false);
        }
    };

    if (loading || !envelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={"app.admin.dashboard.desktop.automation.page.k20b2ed6e"}
                description={"app.admin.dashboard.desktop.automation.page.k0269b376"}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void handleSave()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <MonitorSmartphone className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.desktop.automation.page.k6010e1ed")}
                        </Button>
                    </div>
                }
            />

            <StatusNotice
                title={"app.admin.dashboard.desktop.automation.page.k528406eb"}
                tone="info"
            />

            <ConfigCard title={"app.admin.dashboard.desktop.automation.page.kb6443896"} description={"app.admin.dashboard.desktop.automation.page.kc064340e"}>
                <div className="space-y-3">
                    <div className="grid gap-3 text-sm text-slate-600 sm:grid-cols-2">
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                            <div className="text-xs text-slate-500">{t("app.admin.dashboard.desktop.automation.page.k73b0e79c")}</div>
                            <div className="mt-1 text-base font-semibold text-slate-900">
                                {runtimeCapability?.policy?.enabled === false ? t("app.admin.dashboard.desktop.automation.page.kc6ff9900") : t("app.admin.dashboard.desktop.automation.page.kdb6c0cc1")}
                            </div>
                            <div className="mt-2 text-xs text-slate-500">
                                {runtimeCapability?.displayName || t("app.admin.dashboard.desktop.automation.page.k8e3b4847")} · {runtimeCapability?.summary || t("app.admin.dashboard.desktop.automation.page.k4039664e")}
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-xs leading-6 text-slate-600">
                            <div>{t("app.admin.dashboard.desktop.automation.page.k660c8c15")}{runtimeCapability?.policy?.autoRoute === false ? t("app.admin.dashboard.desktop.automation.page.k574ff3b2") : t("app.admin.dashboard.desktop.automation.page.k85549844")}</div>
                            <div>{t("app.admin.dashboard.desktop.automation.page.k320d306e")}{runtimeCapability?.policy?.exposeDirectTools === false ? t("app.admin.dashboard.desktop.automation.page.k574ff3b2") : t("app.admin.dashboard.desktop.automation.page.k85549844")}</div>
                            <div>{t("app.admin.dashboard.desktop.automation.page.k77ed4e5b")}</div>
                        </div>
                    </div>
                    <div className="flex flex-wrap gap-3">
                        <Button type="button" onClick={() => void setRuntimeEnabled(true)} disabled={runtimeSaving || runtimeCapability?.policy?.enabled !== false}>
                            {runtimeSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t("app.admin.dashboard.desktop.automation.page.kf0adb2b0")}
                        </Button>
                        <Button type="button" variant="outline" onClick={() => void setRuntimeEnabled(false)} disabled={runtimeSaving || runtimeCapability?.policy?.enabled === false}>
                            {runtimeSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t("app.admin.dashboard.desktop.automation.page.k415e3223")}
                        </Button>
                    </div>
                </div>
            </ConfigCard>

            <div className="grid gap-4 xl:grid-cols-2">
                <ConfigCard title={"app.admin.dashboard.desktop.automation.page.keb697d9b"} description={"app.admin.dashboard.desktop.automation.page.k2a55c8b3"}>
                    <div className="space-y-2">
                        <Label>{t("app.admin.dashboard.desktop.automation.page.k9d1ed51e")}</Label>
                        <Select value={envelope.data.modelBindings.plannerModel || "__empty__"} onValueChange={(value) => updateBinding("plannerModel", value)}>
                            <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.desktop.automation.page.k54745147")} /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="__empty__">{t("app.admin.dashboard.desktop.automation.page.k54745147")}</SelectItem>
                                {llmModels.map((model) => (
                                    <SelectItem key={model.modelId} value={model.modelId}>
                                        {model.name} {model.provider?.name ? `(${model.provider.name})` : ""}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-xs leading-6 text-slate-600">
                        <div className="font-medium text-slate-900">{t("app.admin.dashboard.desktop.automation.page.kf558439c")}</div>
                        <div className="mt-1">
                            {t("app.admin.dashboard.desktop.automation.page.ke2e6c721")}
                            {" "}
                            <Link href="/admin/supervisor#vision-media-model" className="font-medium text-sky-700 underline underline-offset-2">
                                {t("app.admin.dashboard.desktop.automation.page.kf45c6152")}
                            </Link>
                            {" "}
                            {t("app.admin.dashboard.desktop.automation.page.k2a90dceb")}
                        </div>
                        <div className="mt-2 text-slate-500">
                            {t("app.admin.dashboard.desktop.automation.page.k091d7083")}{envelope.data.modelBindings.ocrAssistModel || t("app.admin.dashboard.desktop.automation.page.k594ee6bf")}
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard title={"app.admin.dashboard.desktop.automation.page.ke9dbede4"} description={"app.admin.dashboard.desktop.automation.page.k47ac837b"}>
                        <div className="space-y-2">
                            <Label>{t("app.admin.dashboard.desktop.automation.page.k428ef950")}</Label>
                            <Select value={envelope.data.modelBindings.visualJudgeModel || "__empty__"} onValueChange={(value) => updateBinding("visualJudgeModel", value)}>
                            <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.desktop.automation.page.k54745147")} /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="__empty__">{t("app.admin.dashboard.desktop.automation.page.k54745147")}</SelectItem>
                                {llmModels.map((model) => (
                                    <SelectItem key={model.modelId} value={model.modelId}>
                                        {model.name} {model.provider?.name ? `(${model.provider.name})` : ""}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                        <div className="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                            <div className="space-y-1">
                                <Label>{t("app.admin.dashboard.desktop.automation.page.kb8f544bf")}</Label>
                                <p className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.desktop.automation.page.kdc8cc070")}</p>
                            </div>
                        <Switch
                            checked={Boolean(envelope.data.candidateRerankEnabled)}
                            onCheckedChange={(checked) =>
                                setEnvelope({
                                    ...envelope,
                                    data: {
                                        ...envelope.data,
                                        candidateRerankEnabled: checked,
                                    },
                                })
                            }
                        />
                    </div>
                    <div className="space-y-2">
                        <Label>{t("app.admin.dashboard.desktop.automation.page.kfef16620")}</Label>
                        <Select
                            value={envelope.data.modelBindings.candidateRerankerModel || "__empty__"}
                            onValueChange={(value) => updateBinding("candidateRerankerModel", value)}
                        >
                            <SelectTrigger><SelectValue placeholder={t("app.admin.dashboard.desktop.automation.page.k39830d18")} /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="__empty__">{t("app.admin.dashboard.desktop.automation.page.k39830d18")}</SelectItem>
                                {rerankModels.map((model) => (
                                    <SelectItem key={model.modelId} value={model.modelId}>
                                        {model.name} {(model.provider?.name || model.providerName) ? `(${model.provider?.name || model.providerName})` : ""}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs leading-5 text-slate-500">
                            {t("app.admin.dashboard.desktop.automation.page.k59f13024")}{envelope.data.modelBindings.fallbackRerankerModel || t("app.admin.dashboard.desktop.automation.page.k54745147")}{t("app.admin.dashboard.desktop.automation.page.k75b279f7")}
                        </p>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-sm leading-6 text-slate-600">
                        {t("app.admin.dashboard.desktop.automation.page.k2aa01992")}
                    </div>
                </ConfigCard>
            </div>

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />
        </AdminPageShell>
    );
}
