"use client";
import { useEffect, useMemo, useState } from "react";
import { AlignLeft, Loader2, Save } from "lucide-react";
import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { cn } from "@/lib/utils";
interface SysModel {
    id: string;
    modelId: string;
    name: string;
    type: string;
    provider?: {
        name?: string;
        icon?: string;
    };
}
interface ContextPolicy {
    schema_version?: number;
    recursion_limit?: number;
    compression?: {
        enabled?: boolean;
        default_context_window_tokens?: number;
        soft_trigger_ratio?: number;
        hard_trigger_ratio?: number;
        keep_recent_messages?: number;
        use_llm_summary?: boolean;
        max_summary_input_tokens?: number;
        max_summary_input_messages?: number;
        max_summary_output_tokens?: number;
    };
    runtime_adapters?: {
        plugin_host?: {
            window_size?: number;
            max_summary_items?: number;
        };
        channel?: {
            window_size?: number;
            max_summary_items?: number;
        };
        automation?: {
            recent_run_limit?: number;
            job_memory_limit?: number;
        };
    };
}
interface ContextBindings {
    summaryModel?: string;
}
interface ContextDomainData {
    policy?: ContextPolicy;
    modelBindings?: ContextBindings;
}
const DEFAULT_POLICY: ContextPolicy = {
    schema_version: 2,
    recursion_limit: 100,
    compression: {
        enabled: true,
        default_context_window_tokens: 32000,
        soft_trigger_ratio: 0.55,
        hard_trigger_ratio: 0.75,
        keep_recent_messages: 6,
        use_llm_summary: false,
        max_summary_input_tokens: 5000,
        max_summary_input_messages: 60,
        max_summary_output_tokens: 800,
    },
    runtime_adapters: {
        plugin_host: {
            window_size: 15,
            max_summary_items: 8,
        },
        automation: {
            recent_run_limit: 3,
            job_memory_limit: 6,
        },
    },
};
const DEFAULT_BINDINGS: ContextBindings = {
    summaryModel: "",
};
const PRESET_OPTIONS = [
    {
        key: "saving",
        title: "app.admin.dashboard.context.page.ke3c16acc",
        description: "app.admin.dashboard.context.page.k1be9d9c1",
        compression: {
            enabled: true,
            soft_trigger_ratio: 0.45,
            hard_trigger_ratio: 0.62,
            keep_recent_messages: 4,
            use_llm_summary: false,
            max_summary_input_tokens: 3000,
            max_summary_input_messages: 36,
            max_summary_output_tokens: 500,
        },
        runtime_adapters: {
            plugin_host: { window_size: 12, max_summary_items: 6 },
            automation: { recent_run_limit: 2, job_memory_limit: 4 },
        },
    },
    {
        key: "balanced",
        title: "app.admin.dashboard.context.page.k0f34dd0d",
        description: "app.admin.dashboard.context.page.k1cee126d",
        compression: {
            enabled: true,
            soft_trigger_ratio: 0.55,
            hard_trigger_ratio: 0.75,
            keep_recent_messages: 6,
            use_llm_summary: false,
            max_summary_input_tokens: 5000,
            max_summary_input_messages: 60,
            max_summary_output_tokens: 800,
        },
        runtime_adapters: {
            plugin_host: { window_size: 15, max_summary_items: 8 },
            automation: { recent_run_limit: 3, job_memory_limit: 6 },
        },
    },
    {
        key: "high_fidelity",
        title: "app.admin.dashboard.context.page.kba7671ad",
        description: "app.admin.dashboard.context.page.k53e0a764",
        compression: {
            enabled: true,
            soft_trigger_ratio: 0.68,
            hard_trigger_ratio: 0.85,
            keep_recent_messages: 8,
            use_llm_summary: true,
            max_summary_input_tokens: 8000,
            max_summary_input_messages: 80,
            max_summary_output_tokens: 1200,
        },
        runtime_adapters: {
            plugin_host: { window_size: 20, max_summary_items: 10 },
            automation: { recent_run_limit: 4, job_memory_limit: 8 },
        },
    },
] as const;
type PresetKey = (typeof PRESET_OPTIONS)[number]["key"];
function normalizePolicy(policy?: ContextPolicy): ContextPolicy {
    const legacyChannel = policy?.runtime_adapters?.channel || {};
    const pluginHost = policy?.runtime_adapters?.plugin_host || legacyChannel;
    return {
        ...DEFAULT_POLICY,
        ...(policy || {}),
        compression: {
            ...DEFAULT_POLICY.compression,
            ...(policy?.compression || {}),
        },
        runtime_adapters: {
            ...DEFAULT_POLICY.runtime_adapters,
            ...(policy?.runtime_adapters || {}),
            plugin_host: {
                ...DEFAULT_POLICY.runtime_adapters?.plugin_host,
                ...pluginHost,
            },
            automation: {
                ...DEFAULT_POLICY.runtime_adapters?.automation,
                ...(policy?.runtime_adapters?.automation || {}),
            },
        },
    };
}
function canonicalizePolicyForSave(policy: ContextPolicy): ContextPolicy {
    const normalized = normalizePolicy(policy);
    return {
        ...normalized,
        runtime_adapters: {
            plugin_host: {
                ...(normalized.runtime_adapters?.plugin_host || {}),
            },
            automation: {
                ...(normalized.runtime_adapters?.automation || {}),
            },
        },
    };
}
function matchesPreset(policy: ContextPolicy, presetKey: PresetKey) {
    const preset = PRESET_OPTIONS.find((item) => item.key === presetKey);
    if (!preset)
        return false;
    const compression = policy.compression || {};
    const pluginHost = policy.runtime_adapters?.plugin_host || {};
    const automation = policy.runtime_adapters?.automation || {};
    return (Boolean(compression.enabled) === Boolean(preset.compression.enabled) &&
        Number(compression.soft_trigger_ratio ?? 0) === preset.compression.soft_trigger_ratio &&
        Number(compression.hard_trigger_ratio ?? 0) === preset.compression.hard_trigger_ratio &&
        Number(compression.keep_recent_messages ?? 0) === preset.compression.keep_recent_messages &&
        Boolean(compression.use_llm_summary) === Boolean(preset.compression.use_llm_summary) &&
        Number(compression.max_summary_input_tokens ?? 0) === preset.compression.max_summary_input_tokens &&
        Number(compression.max_summary_input_messages ?? 0) === preset.compression.max_summary_input_messages &&
        Number(compression.max_summary_output_tokens ?? 0) === preset.compression.max_summary_output_tokens &&
        Number(pluginHost.window_size ?? 0) === preset.runtime_adapters.plugin_host.window_size &&
        Number(pluginHost.max_summary_items ?? 0) === preset.runtime_adapters.plugin_host.max_summary_items &&
        Number(automation.recent_run_limit ?? 0) === preset.runtime_adapters.automation.recent_run_limit &&
        Number(automation.job_memory_limit ?? 0) === preset.runtime_adapters.automation.job_memory_limit);
}
function detectPreset(policy: ContextPolicy): PresetKey | "custom" {
    const matched = PRESET_OPTIONS.find((item) => matchesPreset(policy, item.key));
    return matched?.key || "custom";
}
function applyPreset(policy: ContextPolicy, presetKey: PresetKey): ContextPolicy {
    const preset = PRESET_OPTIONS.find((item) => item.key === presetKey) || PRESET_OPTIONS[1];
    return {
        ...policy,
        compression: {
            ...(policy.compression || {}),
            ...preset.compression,
        },
        runtime_adapters: {
            ...(policy.runtime_adapters || {}),
            plugin_host: {
                ...(policy.runtime_adapters?.plugin_host || {}),
                ...preset.runtime_adapters.plugin_host,
            },
            automation: {
                ...(policy.runtime_adapters?.automation || {}),
                ...preset.runtime_adapters.automation,
            },
        },
    };
}
function presetLabel(preset: PresetKey | "custom"): string {
    return PRESET_OPTIONS.find((item) => item.key === preset)?.title || "app.admin.dashboard.context.page.kf1007633";
}
function describeSummaryStrategy(policy: ContextPolicy, bindings: ContextBindings): string {
    const compression = policy.compression || {};
    if (!compression.enabled) {
        return "app.admin.dashboard.context.page.k12b31ba6";
    }
    if (compression.use_llm_summary) {
        return bindings.summaryModel
            ? "app.admin.dashboard.context.page.kde493157"
            : "app.admin.dashboard.context.page.k87ef0778";
    }
    return "app.admin.dashboard.context.page.k68f93b7a";
}
function modelValue(model: SysModel) {
    return String(model.modelId || model.id || "").trim();
}
export default function ContextConfigPage() {
    const t = useT();
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<ContextDomainData> | null>(null);
    const [policyForm, setPolicyForm] = useState<ContextPolicy>(DEFAULT_POLICY);
    const [bindingsForm, setBindingsForm] = useState<ContextBindings>(DEFAULT_BINDINGS);
    const [models, setModels] = useState<SysModel[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const loadData = async () => {
        setLoading(true);
        try {
            const [contextEnvelope, modelList] = await Promise.all([
                fetchConfigDomain<ContextDomainData>("context"),
                fetch("/api/models", { cache: "no-store" }).then((response) => response.json().catch(() => [])),
            ]);
            setEnvelope(contextEnvelope);
            setPolicyForm(normalizePolicy(contextEnvelope.data?.policy));
            setBindingsForm({
                ...DEFAULT_BINDINGS,
                ...(contextEnvelope.data?.modelBindings || {}),
            });
            setModels(Array.isArray(modelList) ? modelList : []);
        }
        finally {
            setLoading(false);
        }
    };
    useEffect(() => {
        void loadData();
    }, []);
    const llmModels = useMemo(() => models.filter((model) => ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes((model.type || "LLM").toUpperCase())), [models]);
    const currentPreset = useMemo(() => detectPreset(policyForm), [policyForm]);
    const updateCompression = (patch: Partial<NonNullable<ContextPolicy["compression"]>>) => {
        setPolicyForm((prev) => normalizePolicy({
            ...prev,
            compression: {
                ...(prev.compression || {}),
                ...patch,
            },
        }));
    };
    const updatePluginHostAdapter = (patch: Partial<NonNullable<NonNullable<ContextPolicy["runtime_adapters"]>["plugin_host"]>>) => {
        setPolicyForm((prev) => normalizePolicy({
            ...prev,
            runtime_adapters: {
                ...(prev.runtime_adapters || {}),
                plugin_host: {
                    ...(prev.runtime_adapters?.plugin_host || {}),
                    ...patch,
                },
            },
        }));
    };
    const updateAutomationAdapter = (patch: Partial<NonNullable<NonNullable<ContextPolicy["runtime_adapters"]>["automation"]>>) => {
        setPolicyForm((prev) => normalizePolicy({
            ...prev,
            runtime_adapters: {
                ...(prev.runtime_adapters || {}),
                automation: {
                    ...(prev.runtime_adapters?.automation || {}),
                    ...patch,
                },
            },
        }));
    };
    const handleApplyPreset = (presetKey: PresetKey) => {
        setPolicyForm((prev) => normalizePolicy(applyPreset(prev, presetKey)));
    };
    const handleSave = async () => {
        setSaving(true);
        try {
            const next = await saveConfigDomain<ContextDomainData>("context", {
                data: {
                    policy: canonicalizePolicyForSave(policyForm),
                    modelBindings: bindingsForm,
                },
            });
            setEnvelope(next);
            setPolicyForm(normalizePolicy(next.data?.policy));
            setBindingsForm({
                ...DEFAULT_BINDINGS,
                ...(next.data?.modelBindings || {}),
            });
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
        }
        finally {
            setSaving(false);
        }
    };
    if (loading || !envelope) {
        return (<div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400"/>
            </div>);
    }
    return (<AdminPageShell>
            <AdminPageHeader title={"app.admin.dashboard.context.page.ka87d39b2"} description={"app.admin.dashboard.context.page.k551040ab"} actions={<div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} label={"app.admin.dashboard.context.page.k34ca6ef0"}/>
                        <Button onClick={() => void handleSave()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <Save className="mr-2 h-4 w-4"/>}
                            {t("app.admin.dashboard.context.page.k6010e1ed")}
                        </Button>
                    </div>}/>

            <DomainSummaryStrip items={[
            { label: t("app.admin.dashboard.context.page.kd66fab3a"), value: t(presetLabel(currentPreset)), description: t("app.admin.dashboard.context.page.k29954063") },
            { label: t("app.admin.dashboard.context.page.ka861f42b"), value: t(describeSummaryStrategy(policyForm, bindingsForm)), description: t("app.admin.dashboard.context.page.k201edf4a") },
            { label: t("app.admin.dashboard.context.page.ka1052f03"), value: t("app.admin.dashboard.context.page.k3b49881d", {
                    policyForm_compression_keep_recent_messages_6: policyForm.compression?.keep_recent_messages ?? 6
                }), description: t("app.admin.dashboard.context.page.k8554f00b") },
            { label: t("app.admin.dashboard.context.page.kffd7c829"), value: t("app.admin.dashboard.context.page.kfea0a470"), description: t("app.admin.dashboard.context.page.k683aa2e6") },
            { label: t("app.admin.dashboard.context.page.k3a3d61a9"), value: t("app.admin.dashboard.context.page.k7f410813"), description: t("app.admin.dashboard.context.page.kddc2925b") },
        ]}/>

            <div className="grid gap-4 lg:grid-cols-3">
                {PRESET_OPTIONS.map((option) => (<button key={option.key} type="button" className={cn("rounded-2xl border px-5 py-5 text-left shadow-sm transition-colors", currentPreset === option.key
                ? "border-sky-200 bg-sky-50 text-sky-900"
                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300")} onClick={() => handleApplyPreset(option.key)}>
                        <div className="flex items-center justify-between gap-3">
                        <div className="text-base font-semibold">{t(option.title)}</div>
                            <AlignLeft className="h-4 w-4 shrink-0"/>
                        </div>
                        <div className="mt-2 text-sm leading-6 text-slate-500">{t(option.description)}</div>
                        <div className="mt-4 text-xs leading-5 text-slate-500">
                            {t("app.admin.dashboard.context.page.k2be2a736", {
                option_compression_soft_trigger_ratio: option.compression.soft_trigger_ratio,
                option_compression_hard_trigger_ratio: option.compression.hard_trigger_ratio,
                option_compression_keep_recent_messages: option.compression.keep_recent_messages,
                option_compression_max_summary_input_tokens: option.compression.max_summary_input_tokens
            })}
                        </div>
                    </button>))}
            </div>

            <StatusNotice title={"app.admin.dashboard.context.page.kce9d4f4c"} tone="info"/>

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired}/>

            <AdvancedSection title={"app.admin.dashboard.context.page.k8d1286a2"} description={"app.admin.dashboard.context.page.k71a2c636"} defaultOpen={false}>
                <div className="grid gap-6 xl:grid-cols-2">
                    <ConfigCard title={"app.admin.dashboard.context.page.k24e23c9b"} description={"app.admin.dashboard.context.page.ka12f2cec"}>
                        <div className="space-y-5">
                            <div className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="space-y-1">
                                    <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.context.page.k699fdecf")}</div>
                                    <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.context.page.k65b57dfb")}</div>
                                </div>
                                <Switch checked={policyForm.compression?.enabled ?? true} onCheckedChange={(checked) => updateCompression({ enabled: checked })}/>
                            </div>

                            <div className="grid gap-5 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>{t("app.admin.dashboard.context.page.k1ac467d7")}</Label>
                                    <Input type="number" min={10} max={5000} value={policyForm.recursion_limit ?? 100} onChange={(event) => setPolicyForm((prev) => normalizePolicy({
            ...prev,
            recursion_limit: Number(event.target.value),
        }))}/>
                                    <p className="text-xs text-slate-500">{t("app.admin.dashboard.context.page.k7201ba84")}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t("app.admin.dashboard.context.page.k2145b4bb")}</Label>
                                    <Input type="number" min={2048} max={2000000} value={policyForm.compression?.default_context_window_tokens ?? 32000} onChange={(event) => updateCompression({ default_context_window_tokens: Number(event.target.value) })}/>
                                    <p className="text-xs text-slate-500">{t("app.admin.dashboard.context.page.k020074fe")}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t("app.admin.dashboard.context.page.k0ba8abed")}</Label>
                                    <Input type="number" min={0.1} max={0.95} step={0.01} value={policyForm.compression?.soft_trigger_ratio ?? 0.55} onChange={(event) => updateCompression({ soft_trigger_ratio: Number(event.target.value) })}/>
                                    <p className="text-xs text-slate-500">{t("app.admin.dashboard.context.page.k9bfaf2bd")}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t("app.admin.dashboard.context.page.k88e4b454")}</Label>
                                    <Input type="number" min={0.15} max={0.99} step={0.01} value={policyForm.compression?.hard_trigger_ratio ?? 0.75} onChange={(event) => updateCompression({ hard_trigger_ratio: Number(event.target.value) })}/>
                                    <p className="text-xs text-slate-500">{t("app.admin.dashboard.context.page.kd41de26d")}</p>
                                </div>
                            </div>
                        </div>
                    </ConfigCard>

                    <ConfigCard title={"app.admin.dashboard.context.page.k67675fc3"} description={"app.admin.dashboard.context.page.k797f385c"}>
                        <div className="space-y-5">
                            <div className="grid gap-5 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>{t("app.admin.dashboard.context.page.k5eb3c1cb")}</Label>
                                    <Input type="number" min={1} max={100} value={policyForm.compression?.keep_recent_messages ?? 6} onChange={(event) => updateCompression({ keep_recent_messages: Number(event.target.value) })}/>
                                    <p className="text-xs text-slate-500">{t("app.admin.dashboard.context.page.ke7822966")}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t("app.admin.dashboard.context.page.k72b4a7a6")}</Label>
                                    <Input type="number" min={512} max={200000} value={policyForm.compression?.max_summary_input_tokens ?? 5000} onChange={(event) => updateCompression({ max_summary_input_tokens: Number(event.target.value) })}/>
                                    <p className="text-xs text-slate-500">{t("app.admin.dashboard.context.page.ka999be4e")}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t("app.admin.dashboard.context.page.kf20fe3a0")}</Label>
                                    <Input type="number" min={5} max={200} value={policyForm.compression?.max_summary_input_messages ?? 60} onChange={(event) => updateCompression({ max_summary_input_messages: Number(event.target.value) })}/>
                                    <p className="text-xs text-slate-500">{t("app.admin.dashboard.context.page.kc7417d2c")}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t("app.admin.dashboard.context.page.k93eb3185")}</Label>
                                    <Input type="number" min={128} max={8000} value={policyForm.compression?.max_summary_output_tokens ?? 800} onChange={(event) => updateCompression({ max_summary_output_tokens: Number(event.target.value) })}/>
                                    <p className="text-xs text-slate-500">{t("app.admin.dashboard.context.page.k93f2744e")}</p>
                                </div>
                            </div>

                            <div className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="space-y-1">
                                    <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.context.page.k3e11964e")}</div>
                                    <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.context.page.k45b19907")}</div>
                                </div>
                                <Switch checked={policyForm.compression?.use_llm_summary ?? false} onCheckedChange={(checked) => updateCompression({ use_llm_summary: checked })}/>
                            </div>

                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.k6a51cc17")}</Label>
                                <Select value={bindingsForm.summaryModel || "__empty__"} onValueChange={(value) => {
            setBindingsForm((prev) => ({
                ...prev,
                summaryModel: value === "__empty__" ? "" : value,
            }));
        }}>
                                    <SelectTrigger className="w-full">
                                        <SelectValue placeholder={t("app.admin.dashboard.context.page.kc233116e")}/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="__empty__">{t("app.admin.dashboard.context.page.kc03621ca")}</SelectItem>
                                        {llmModels.map((model) => (<SelectItem key={modelValue(model)} value={modelValue(model)}>
                                                {model.name || modelValue(model)} {model.provider?.name ? `(${model.provider.name})` : ""}
                                            </SelectItem>))}
                                    </SelectContent>
                                </Select>
                                <p className="text-xs text-slate-500">{t("app.admin.dashboard.context.page.kbca31aa4")}</p>
                            </div>
                        </div>
                    </ConfigCard>
                </div>

                <div className="mt-6 grid gap-6 xl:grid-cols-2">
                    <ConfigCard title={"app.admin.dashboard.context.page.k499432c1"} description={"app.admin.dashboard.context.page.k1d384393"}>
                        <div className="grid gap-5 md:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.kf12cd060")}</Label>
                                <Input type="number" min={3} max={100} value={policyForm.runtime_adapters?.plugin_host?.window_size ?? 15} onChange={(event) => updatePluginHostAdapter({ window_size: Number(event.target.value) })}/>
                            </div>
                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.kcb2c9afb")}</Label>
                                <Input type="number" min={1} max={50} value={policyForm.runtime_adapters?.plugin_host?.max_summary_items ?? 8} onChange={(event) => updatePluginHostAdapter({ max_summary_items: Number(event.target.value) })}/>
                            </div>
                        </div>
                    </ConfigCard>

                    <ConfigCard title={"app.admin.dashboard.context.page.k949f0930"} description={"app.admin.dashboard.context.page.kb53e7ba5"}>
                        <div className="grid gap-5 md:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.kc8b1964d")}</Label>
                                <Input type="number" min={1} max={20} value={policyForm.runtime_adapters?.automation?.recent_run_limit ?? 3} onChange={(event) => updateAutomationAdapter({ recent_run_limit: Number(event.target.value) })}/>
                            </div>
                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.kc785dd7a")}</Label>
                                <Input type="number" min={1} max={50} value={policyForm.runtime_adapters?.automation?.job_memory_limit ?? 6} onChange={(event) => updateAutomationAdapter({ job_memory_limit: Number(event.target.value) })}/>
                            </div>
                        </div>
                    </ConfigCard>
                </div>
            </AdvancedSection>
        </AdminPageShell>);
}
