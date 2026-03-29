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
import { lt } from "@/lib/locale";

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
                title={lt("桌面操作", "Desktop automation")}
                description={lt("配置桌面操作的规划、识别和回退。", "Configure planning, recognition, and recovery for desktop automation.")}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void handleSave()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <MonitorSmartphone className="mr-2 h-4 w-4" />}
                            {t(lt("保存", "Save"))}
                        </Button>
                    </div>
                }
            />

            <StatusNotice
                title={lt("只有阻塞目标的变化才会升级成中断。", "Only target-blocking changes escalate into interrupts.")}
                tone="info"
            />

            <ConfigCard title={lt("Runtime 状态", "Runtime status")} description={lt("这里只影响桌面自动化 runtime。", "This only affects the desktop automation runtime.")}>
                <div className="space-y-3">
                    <div className="grid gap-3 text-sm text-slate-600 sm:grid-cols-2">
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                            <div className="text-xs text-slate-500">{t(lt("当前状态", "Current state"))}</div>
                            <div className="mt-1 text-base font-semibold text-slate-900">
                                {runtimeCapability?.policy?.enabled === false ? t(lt("已禁用", "Disabled")) : t(lt("已启用", "Enabled"))}
                            </div>
                            <div className="mt-2 text-xs text-slate-500">
                                {runtimeCapability?.displayName || t(lt("桌面自动化", "Desktop automation"))} · {runtimeCapability?.summary || t(lt("负责桌面自动化执行。", "Handles desktop automation execution."))}
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-xs leading-6 text-slate-600">
                            <div>{t(lt("自动路由：", "Auto-route:"))}{runtimeCapability?.policy?.autoRoute === false ? t(lt("关闭", "Off")) : t(lt("开启", "On"))}</div>
                            <div>{t(lt("直连工具暴露：", "Direct tools:"))}{runtimeCapability?.policy?.exposeDirectTools === false ? t(lt("关闭", "Off")) : t(lt("开启", "On"))}</div>
                            <div>{t(lt("影响范围：仅影响 `computer_use` runtime，其他模块继续正常运行。", "Scope: only affects the `computer_use` runtime. Other modules keep running normally."))}</div>
                        </div>
                    </div>
                    <div className="flex flex-wrap gap-3">
                        <Button type="button" onClick={() => void setRuntimeEnabled(true)} disabled={runtimeSaving || runtimeCapability?.policy?.enabled !== false}>
                            {runtimeSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t(lt("启用桌面自动化", "Enable desktop automation"))}
                        </Button>
                        <Button type="button" variant="outline" onClick={() => void setRuntimeEnabled(false)} disabled={runtimeSaving || runtimeCapability?.policy?.enabled === false}>
                            {runtimeSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            {t(lt("禁用桌面自动化", "Disable desktop automation"))}
                        </Button>
                    </div>
                </div>
            </ConfigCard>

            <div className="grid gap-4 xl:grid-cols-2">
                <ConfigCard title={lt("界面识别方式", "Recognition stack")} description={lt("配置规划和识别模型。", "Configure planning and recognition models.")}>
                    <div className="space-y-2">
                        <Label>{t(lt("规划模型", "Planner model"))}</Label>
                        <Select value={envelope.data.modelBindings.plannerModel || "__empty__"} onValueChange={(value) => updateBinding("plannerModel", value)}>
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
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-xs leading-6 text-slate-600">
                        <div className="font-medium text-slate-900">{t(lt("视觉媒体分析模型", "Vision media model"))}</div>
                        <div className="mt-1">
                            {t(lt("`vision_media_analyzer` 的模型现在改由", "The `vision_media_analyzer` model is now managed from"))}
                            {" "}
                            <Link href="/admin/supervisor#vision-media-model" className="font-medium text-sky-700 underline underline-offset-2">
                                {t(lt("主理人", "Lead"))}
                            </Link>
                            {" "}
                            {t(lt("页面统一管理，这里不再单独维护第二份配置。", "settings, so this page no longer keeps a second source of truth."))}
                        </div>
                        <div className="mt-2 text-slate-500">
                            {t(lt("当前绑定：", "Current binding:"))}{envelope.data.modelBindings.ocrAssistModel || t(lt("跟随视觉角色默认模型", "Follow the default vision-role model"))}
                        </div>
                    </div>
                </ConfigCard>

                <ConfigCard title={lt("歧义处理与回退", "Disambiguation & fallback")} description={lt("配置验真、重排和回退判断。", "Configure verification, rerank, and fallback decisions.")}>
                        <div className="space-y-2">
                            <Label>{t(lt("视觉裁判模型", "Vision judge model"))}</Label>
                            <Select value={envelope.data.modelBindings.visualJudgeModel || "__empty__"} onValueChange={(value) => updateBinding("visualJudgeModel", value)}>
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
                        <div className="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                            <div className="space-y-1">
                                <Label>{t(lt("候选文本重排", "Candidate rerank"))}</Label>
                                <p className="text-xs leading-5 text-slate-500">{t(lt("只在多候选歧义场景里触发，用于精排文本化候选，不替代视觉裁判。", "Used only when multiple text candidates compete. It does not replace the vision judge."))}</p>
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
                        <Label>{t(lt("候选重排模型", "Candidate reranker"))}</Label>
                        <Select
                            value={envelope.data.modelBindings.candidateRerankerModel || "__empty__"}
                            onValueChange={(value) => updateBinding("candidateRerankerModel", value)}
                        >
                            <SelectTrigger><SelectValue placeholder={t(lt("未指定，回退全局重排模型", "Unset, fall back to the global reranker"))} /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="__empty__">{t(lt("未指定，回退全局重排模型", "Unset, fall back to the global reranker"))}</SelectItem>
                                {rerankModels.map((model) => (
                                    <SelectItem key={model.modelId} value={model.modelId}>
                                        {model.name} {(model.provider?.name || model.providerName) ? `(${model.provider?.name || model.providerName})` : ""}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs leading-5 text-slate-500">
                            {t(lt("当前全局回退模型：", "Global fallback reranker:"))}{envelope.data.modelBindings.fallbackRerankerModel || t(lt("未指定", "Unset"))}{t(lt("。只有出现多个文本候选且需要裁决时，才会额外调用这一路重排。", ". This is only used when multiple text candidates require a final ranking."))}
                        </p>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-sm leading-6 text-slate-600">
                        {t(lt("当前策略会先做本地观察和恢复；只有遇到真正阻塞目标的环境变化，才会上升为运行中断。", "The current policy attempts local observation and recovery first. Only target-blocking changes escalate into runtime interrupts."))}
                    </div>
                </ConfigCard>
            </div>

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />
        </AdminPageShell>
    );
}
