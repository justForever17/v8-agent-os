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
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { lt } from "@/lib/locale";

const RPAWorkbench = dynamic(
    () => import("@/components/rpa/RPAWorkbench").then((mod) => mod.RPAWorkbench),
    {
        loading: () => (
            <div className="flex min-h-[280px] items-center justify-center rounded-2xl border border-slate-200 bg-white shadow-sm">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        ),
    }
);

type ModelOption = {
    modelId: string;
    name: string;
    type: string;
    provider?: { name?: string };
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
    policy?: {
        enabled?: boolean;
        autoRoute?: boolean;
        exposeDirectTools?: boolean;
    };
};

export default function RpaRuntimePage() {
    const t = useT();
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<RpaData> | null>(null);
    const [models, setModels] = useState<ModelOption[]>([]);
    const [runtimeCapability, setRuntimeCapability] = useState<RuntimeCapabilityEntry | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [runtimeSaving, setRuntimeSaving] = useState(false);

    const loadData = async () => {
        setLoading(true);
        try {
            const [config, modelList, capabilitySnapshot] = await Promise.all([
                fetchConfigDomain<RpaData>("rpa"),
                fetch("/api/models", { cache: "no-store" }).then((response) => response.json().catch(() => [])),
                fetch("/api/runtime-capabilities", { cache: "no-store" }).then((response) => response.json().catch(() => ({}))),
            ]);
            setEnvelope(config);
            setModels(Array.isArray(modelList) ? modelList : []);
            const runtimes = Array.isArray(capabilitySnapshot?.runtimes) ? capabilitySnapshot.runtimes : [];
            setRuntimeCapability(runtimes.find((item: RuntimeCapabilityEntry) => item.kind === "rpa") || null);
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
                title={lt("RPA Runtime", "RPA Runtime")}
                description={lt("管理流程发现、执行策略与回退方式。", "Manage discovery, execution policy, and fallback behavior for RPA.")} 
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void handleSave()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Workflow className="mr-2 h-4 w-4" />}
                            {t(lt("保存", "Save"))}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip
                items={[
                    { label: lt("流程发现模型", "Discovery model"), value: envelope.data.modelBindings.discoveryModel ? t(lt("已指定", "Bound")) : t(lt("未指定", "Unset")), description: lt("用于流程摸索、变量抽取和脚本生成。", "Used for discovery, variable extraction, and script generation.") },
                    { label: lt("执行策略", "Execution policy"), value: envelope.data.executionPolicy.runtimeFirst ? t(lt("先本地恢复", "Local recovery first")) : t(lt("直接上报", "Escalate directly")), description: lt("先由运行时本地修复，再决定是否升级。", "Try local runtime recovery before escalation.") },
                    { label: lt("副作用保护", "Side-effect guard"), value: envelope.data.executionPolicy.sideEffectIdempotency ? t(lt("已开启", "On")) : t(lt("未开启", "Off")), description: lt("避免重复执行高风险外部动作。", "Avoid replaying risky external side effects.") },
                    { label: lt("恢复方式", "Recovery mode"), value: envelope.data.executionPolicy.localRecoveryPreferred ? t(lt("优先自动恢复", "Auto recovery first")) : t(lt("需人工判断", "Manual review")), description: lt("失败后优先本地修补并继续。", "Prefer local repair and continue after failures.") },
                ]}
            />

            <ConfigCard title={lt("流程发现模型", "Discovery model")} description={lt("配置流程发现模型。", "Configure the model used for RPA discovery.")}>
                <div className="space-y-2">
                    <Label>{t(lt("发现模型", "Discovery model"))}</Label>
                    <Select
                        value={envelope.data.modelBindings.discoveryModel || "__empty__"}
                        onValueChange={(value) =>
                            setEnvelope({
                                ...envelope,
                                data: {
                                    ...envelope.data,
                                    modelBindings: {
                                        discoveryModel: value === "__empty__" ? "" : value,
                                    },
                                },
                            })
                        }
                    >
                        <SelectTrigger><SelectValue placeholder={t(lt("未指定", "Unset"))} /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="__empty__">{t(lt("未指定", "Unset"))}</SelectItem>
                            {llmModels.map((model) => (
                                <SelectItem key={model.modelId} value={model.modelId}>
                                    {model.name} {model.provider?.name ? `(${model.provider.name})` : ""}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            </ConfigCard>

            <StatusNotice title={lt("只有本地无法继续时才会上升为中断或人工确认。", "Escalate to interrupt or manual review only when local recovery cannot continue.")} tone="info" />

            <ConfigCard title={lt("Runtime 状态", "Runtime status")} description={lt("这里只影响 RPA runtime。", "This only affects the RPA runtime.")}>
                <div className="space-y-3">
                    <div className="grid gap-3 text-sm text-slate-600 sm:grid-cols-2">
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                            <div className="text-xs text-slate-500">{t(lt("当前状态", "Current state"))}</div>
                            <div className="mt-1 text-base font-semibold text-slate-900">
                                {runtimeCapability?.policy?.enabled === false ? t(lt("已禁用", "Disabled")) : t(lt("已启用", "Enabled"))}
                            </div>
                            <div className="mt-2 text-xs text-slate-500">
                            {runtimeCapability?.displayName || "RPA Runtime"} · {runtimeCapability?.summary || t(lt("负责流程发现、.robot 执行与失败回退。", "Handles discovery, .robot execution, and fallback recovery."))}
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-xs leading-6 text-slate-600">
                            <div>{t(lt("自动路由：", "Auto-route:"))}{runtimeCapability?.policy?.autoRoute === false ? t(lt("关闭", "Off")) : t(lt("开启", "On"))}</div>
                            <div>{t(lt("直连工具暴露：", "Direct tools:"))}{runtimeCapability?.policy?.exposeDirectTools === false ? t(lt("关闭", "Off")) : t(lt("开启", "On"))}</div>
                            <div>{t(lt("影响范围：仅影响 `rpa` runtime，其他模块继续正常运行。", "Scope: only affects the `rpa` runtime. Other modules continue as usual."))}</div>
                        </div>
                    </div>
                    <div className="flex flex-wrap gap-3">
                        <Button type="button" onClick={() => void setRuntimeEnabled(true)} disabled={runtimeSaving || runtimeCapability?.policy?.enabled !== false}>
                            {runtimeSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t(lt("启用 RPA", "Enable RPA"))}
                        </Button>
                        <Button type="button" variant="outline" onClick={() => void setRuntimeEnabled(false)} disabled={runtimeSaving || runtimeCapability?.policy?.enabled === false}>
                            {runtimeSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t(lt("禁用 RPA", "Disable RPA"))}
                        </Button>
                    </div>
                </div>
            </ConfigCard>

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />

            <AdvancedSection
                description={lt("只有在需要调试流程、模板或脚本细节时再展开。", "Expand only when you need to inspect flows, templates, or script details.")}
                defaultOpen={false}
            >
                <RPAWorkbench />
            </AdvancedSection>
        </AdminPageShell>
    );
}
