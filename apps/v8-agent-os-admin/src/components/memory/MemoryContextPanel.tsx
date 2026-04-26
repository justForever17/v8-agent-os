"use client";

import { useEffect, useMemo, useState } from "react";
import { AlignLeft, Loader2, Save } from "lucide-react";

import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { ModelSelect } from "@/components/models/ModelSelect";
import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { cn } from "@/lib/utils";

interface SysModel {
    id: string;
    modelRef?: string;
    providerId?: string;
    modelId: string;
    name: string;
    type: string;
    provider?: {
        id?: string;
        name?: string;
        icon?: string;
    };
    providerName?: string;
}

interface ContextPolicy {
    schema_version?: number;
    recursion_limit?: number;
    compression?: {
        enabled?: boolean;
        mode?: string;
        default_context_window_tokens?: number;
        trigger_ratio?: number;
        keep_recent_turns?: number;
        soft_trigger_ratio?: number;
        hard_trigger_ratio?: number;
        keep_recent_messages?: number;
        use_llm_summary?: boolean;
        max_summary_input_tokens?: number;
        max_summary_input_messages?: number;
        max_summary_output_tokens?: number;
        compression_model_safety_ratio?: number;
        noticeable_latency_ms?: number;
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
    schema_version: 3,
    recursion_limit: 100,
    compression: {
        enabled: true,
        mode: "persistent_baseline",
        default_context_window_tokens: 32000,
        trigger_ratio: 0.94,
        keep_recent_turns: 4,
        keep_recent_messages: 8,
        soft_trigger_ratio: 0.90,
        hard_trigger_ratio: 0.94,
        use_llm_summary: true,
        max_summary_input_tokens: 5000,
        max_summary_input_messages: 60,
        max_summary_output_tokens: 800,
        compression_model_safety_ratio: 0.90,
        noticeable_latency_ms: 800,
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
        title: "紧凑",
        description: "更早落 baseline，适合移动端和高频长会话。",
        compression: {
            enabled: true,
            mode: "persistent_baseline",
            trigger_ratio: 0.92,
            keep_recent_turns: 3,
            keep_recent_messages: 6,
            soft_trigger_ratio: 0.88,
            hard_trigger_ratio: 0.92,
            use_llm_summary: true,
            max_summary_input_tokens: 4000,
            max_summary_input_messages: 48,
            max_summary_output_tokens: 640,
            compression_model_safety_ratio: 0.88,
            noticeable_latency_ms: 600,
        },
        runtime_adapters: {
            plugin_host: { window_size: 12, max_summary_items: 6 },
            automation: { recent_run_limit: 2, job_memory_limit: 4 },
        },
    },
    {
        key: "balanced",
        title: "平衡",
        description: "推荐默认值，使用永久降水位 baseline 并保留最近 raw 对话。",
        compression: {
            enabled: true,
            mode: "persistent_baseline",
            trigger_ratio: 0.94,
            keep_recent_turns: 4,
            keep_recent_messages: 8,
            soft_trigger_ratio: 0.90,
            hard_trigger_ratio: 0.94,
            use_llm_summary: true,
            max_summary_input_tokens: 5000,
            max_summary_input_messages: 60,
            max_summary_output_tokens: 800,
            compression_model_safety_ratio: 0.90,
            noticeable_latency_ms: 800,
        },
        runtime_adapters: {
            plugin_host: { window_size: 15, max_summary_items: 8 },
            automation: { recent_run_limit: 3, job_memory_limit: 6 },
        },
    },
    {
        key: "high_fidelity",
        title: "高保真",
        description: "更晚触发压缩并保留更多最近 raw 轮次，适合复杂工程任务。",
        compression: {
            enabled: true,
            mode: "persistent_baseline",
            trigger_ratio: 0.95,
            keep_recent_turns: 6,
            keep_recent_messages: 12,
            soft_trigger_ratio: 0.91,
            hard_trigger_ratio: 0.95,
            use_llm_summary: true,
            max_summary_input_tokens: 8000,
            max_summary_input_messages: 80,
            max_summary_output_tokens: 1200,
            compression_model_safety_ratio: 0.92,
            noticeable_latency_ms: 1200,
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
    if (!preset) return false;
    const compression = policy.compression || {};
    const pluginHost = policy.runtime_adapters?.plugin_host || {};
    const automation = policy.runtime_adapters?.automation || {};
    return (
        Boolean(compression.enabled) === Boolean(preset.compression.enabled)
        && String(compression.mode || "") === String(preset.compression.mode || "")
        && Number(compression.trigger_ratio ?? 0) === preset.compression.trigger_ratio
        && Number(compression.keep_recent_turns ?? 0) === preset.compression.keep_recent_turns
        && Number(compression.keep_recent_messages ?? 0) === preset.compression.keep_recent_messages
        && Boolean(compression.use_llm_summary) === Boolean(preset.compression.use_llm_summary)
        && Number(compression.max_summary_input_tokens ?? 0) === preset.compression.max_summary_input_tokens
        && Number(compression.max_summary_input_messages ?? 0) === preset.compression.max_summary_input_messages
        && Number(compression.max_summary_output_tokens ?? 0) === preset.compression.max_summary_output_tokens
        && Number(compression.compression_model_safety_ratio ?? 0) === preset.compression.compression_model_safety_ratio
        && Number(compression.noticeable_latency_ms ?? 0) === preset.compression.noticeable_latency_ms
        && Number(pluginHost.window_size ?? 0) === preset.runtime_adapters.plugin_host.window_size
        && Number(pluginHost.max_summary_items ?? 0) === preset.runtime_adapters.plugin_host.max_summary_items
        && Number(automation.recent_run_limit ?? 0) === preset.runtime_adapters.automation.recent_run_limit
        && Number(automation.job_memory_limit ?? 0) === preset.runtime_adapters.automation.job_memory_limit
    );
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
    return PRESET_OPTIONS.find((item) => item.key === preset)?.title || "已自定义";
}

function describeSummaryStrategy(policy: ContextPolicy, bindings: ContextBindings): string {
    const compression = policy.compression || {};
    if (!compression.enabled) {
        return "已禁用压缩";
    }
    if (compression.use_llm_summary) {
        return bindings.summaryModel ? "LLM 压缩模型已绑定" : "使用当前默认 summary 角色";
    }
    return "规则压缩";
}

export function MemoryContextPanel() {
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
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadData();
    }, []);

    const llmModels = useMemo(
        () => models.filter((model) => ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes((model.type || "LLM").toUpperCase())),
        [models],
    );
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

    const updatePluginHostAdapter = (
        patch: Partial<NonNullable<NonNullable<ContextPolicy["runtime_adapters"]>["plugin_host"]>>,
    ) => {
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

    const updateAutomationAdapter = (
        patch: Partial<NonNullable<NonNullable<ContextPolicy["runtime_adapters"]>["automation"]>>,
    ) => {
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
        } finally {
            setSaving(false);
        }
    };

    if (loading || !envelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center rounded-2xl border border-border/60 bg-background/80">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                <div className="space-y-1">
                    <h2 className="text-2xl font-semibold">{t("app.admin.dashboard.context.page.ka87d39b2")}</h2>
                    <p className="text-sm text-muted-foreground">{t("app.admin.dashboard.context.page.k551040ab")}</p>
                </div>
                <div className="flex items-center gap-3">
                    <InlineSaveState saving={saving} saved={saved} label={"app.admin.dashboard.context.page.k34ca6ef0"} />
                    <Button onClick={() => void handleSave()} disabled={saving}>
                        {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                        {t("app.admin.dashboard.context.page.k6010e1ed")}
                    </Button>
                </div>
            </div>

            <DomainSummaryStrip
                items={[
                    {
                        label: "上下文预设",
                        value: presetLabel(currentPreset),
                        description: "永久降水位治理的推荐组合值。",
                    },
                    {
                        label: "压缩策略",
                        value: describeSummaryStrategy(policyForm, bindingsForm),
                        description: "压缩基线层由 summary 模型或规则压缩生成。",
                    },
                    {
                        label: "最近 raw 保留",
                        value: `${policyForm.compression?.keep_recent_turns ?? 4} 轮 / ${policyForm.compression?.keep_recent_messages ?? 8} 条消息`,
                        description: "最近对话保持原文，不进入 baseline 压缩。",
                    },
                    {
                        label: "触发阈值",
                        value: `${Math.round((policyForm.compression?.trigger_ratio ?? 0.94) * 100)}%`,
                        description: "建议 1M 窗口使用 92%-95% 安全线。",
                    },
                    {
                        label: "治理模式",
                        value: policyForm.compression?.mode === "persistent_baseline" ? "永久降水位" : (policyForm.compression?.mode || "未设置"),
                        description: "原始全文历史继续保留给 UI 和审计。",
                    },
                ]}
            />

            <div className="grid gap-4 lg:grid-cols-3">
                {PRESET_OPTIONS.map((option) => (
                    <button
                        key={option.key}
                        type="button"
                        className={cn(
                            "rounded-2xl border px-5 py-5 text-left shadow-sm transition-colors",
                            currentPreset === option.key
                                ? "border-sky-200 bg-sky-50 text-sky-900"
                                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300",
                        )}
                        onClick={() => handleApplyPreset(option.key)}
                    >
                        <div className="flex items-center justify-between gap-3">
                            <div className="text-base font-semibold">{option.title}</div>
                            <AlignLeft className="h-4 w-4 shrink-0" />
                        </div>
                        <div className="mt-2 text-sm leading-6 text-slate-500">{option.description}</div>
                        <div className="mt-4 text-xs leading-5 text-slate-500">
                            阈值 {Math.round((option.compression.trigger_ratio ?? 0.94) * 100)}% · 最近 raw {option.compression.keep_recent_turns} 轮 · 压缩输入 {option.compression.max_summary_input_tokens}
                        </div>
                    </button>
                ))}
            </div>

            <StatusNotice title={"app.admin.dashboard.context.page.kce9d4f4c"} tone="info" />

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />

            <AdvancedSection title={"app.admin.dashboard.context.page.k8d1286a2"} description={"app.admin.dashboard.context.page.k71a2c636"} defaultOpen={false}>
                <div className="grid gap-6 xl:grid-cols-2">
                    <ConfigCard title={"永久降水位治理"} description={"压缩主线改为 baseline + 最近 raw 对话保留，不再每轮从全量 raw 历史重新压缩。"}>
                        <div className="space-y-5">
                            <div className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="space-y-1">
                                    <div className="text-sm font-medium text-slate-900">启用上下文治理</div>
                                    <div className="text-xs leading-5 text-slate-500">关闭后不会生成 baseline，也不会在长会话进入永久降水位模式。</div>
                                </div>
                                <Switch checked={policyForm.compression?.enabled ?? true} onCheckedChange={(checked) => updateCompression({ enabled: checked })} />
                            </div>

                            <div className="grid gap-5 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>递归上限</Label>
                                    <Input
                                        type="number"
                                        min={10}
                                        max={5000}
                                        value={policyForm.recursion_limit ?? 100}
                                        onChange={(event) => setPolicyForm((prev) => normalizePolicy({
                                            ...prev,
                                            recursion_limit: Number(event.target.value),
                                        }))}
                                    />
                                    <p className="text-xs text-slate-500">保护复杂 prompt 组装链，避免递归式上下文治理失控。</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>主聊天模型窗口</Label>
                                    <Input
                                        type="number"
                                        min={2048}
                                        max={2000000}
                                        value={policyForm.compression?.default_context_window_tokens ?? 32000}
                                        onChange={(event) => updateCompression({ default_context_window_tokens: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">用于判断何时开始压缩。1M 模型建议与实际窗口保持一致。</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>压缩触发阈值</Label>
                                    <Input
                                        type="number"
                                        min={0.92}
                                        max={0.95}
                                        step={0.01}
                                        value={policyForm.compression?.trigger_ratio ?? 0.94}
                                        onChange={(event) => updateCompression({ trigger_ratio: Number(event.target.value), hard_trigger_ratio: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">推荐 0.94。达到安全线后立即更新 baseline，而不是继续蓄水。</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>最近 raw 保留轮数</Label>
                                    <Input
                                        type="number"
                                        min={1}
                                        max={40}
                                        value={policyForm.compression?.keep_recent_turns ?? 4}
                                        onChange={(event) => updateCompression({ keep_recent_turns: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">这些最近轮次保持原文输入，适合长流程任务保留细节。</p>
                                </div>
                            </div>
                        </div>
                    </ConfigCard>

                    <ConfigCard title={"压缩模型与 chunk 治理"} description={"当压缩模型窗口小于主模型时，自动分块压缩并递归汇总，保持压缩模型无状态。"}>
                        <div className="space-y-5">
                            <div className="grid gap-5 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>最近 raw 消息下限</Label>
                                    <Input
                                        type="number"
                                        min={1}
                                        max={100}
                                        value={policyForm.compression?.keep_recent_messages ?? 6}
                                        onChange={(event) => updateCompression({ keep_recent_messages: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">兼容极端消息结构，确保 raw 保留消息数不低于轮数下限。</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>单块压缩输入预算</Label>
                                    <Input
                                        type="number"
                                        min={512}
                                        max={200000}
                                        value={policyForm.compression?.max_summary_input_tokens ?? 5000}
                                        onChange={(event) => updateCompression({ max_summary_input_tokens: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">每个压缩块都会限制在该预算与压缩模型安全线以内。</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>单块消息上限</Label>
                                    <Input
                                        type="number"
                                        min={5}
                                        max={200}
                                        value={policyForm.compression?.max_summary_input_messages ?? 60}
                                        onChange={(event) => updateCompression({ max_summary_input_messages: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">超长流程会按块切分，再做多轮递归汇总。</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>压缩输出预算</Label>
                                    <Input
                                        type="number"
                                        min={128}
                                        max={8000}
                                        value={policyForm.compression?.max_summary_output_tokens ?? 800}
                                        onChange={(event) => updateCompression({ max_summary_output_tokens: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">用于生成新的 baseline 摘要块，避免汇总结果再次失控膨胀。</p>
                                </div>
                            </div>

                            <div className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="space-y-1">
                                    <div className="text-sm font-medium text-slate-900">启用 LLM 压缩</div>
                                    <div className="text-xs leading-5 text-slate-500">建议开启。规则压缩只适合非常短的旧历史。</div>
                                </div>
                                <Switch checked={policyForm.compression?.use_llm_summary ?? false} onCheckedChange={(checked) => updateCompression({ use_llm_summary: checked })} />
                            </div>

                            <div className="grid gap-5 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>压缩模型安全线</Label>
                                    <Input
                                        type="number"
                                        min={0.5}
                                        max={0.95}
                                        step={0.01}
                                        value={policyForm.compression?.compression_model_safety_ratio ?? 0.9}
                                        onChange={(event) => updateCompression({ compression_model_safety_ratio: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">主模型和压缩模型窗口不一致时，切块预算按这个安全线计算。</p>
                                </div>
                                <div className="space-y-1.5">
                                    <Label>显式治理节点阈值 (ms)</Label>
                                    <Input
                                        type="number"
                                        min={50}
                                        max={60000}
                                        step={50}
                                        value={policyForm.compression?.noticeable_latency_ms ?? 800}
                                        onChange={(event) => updateCompression({ noticeable_latency_ms: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">压缩过程超过该耗时，可在聊天流里显示 context governance 节点。</p>
                                </div>
                            </div>

                            <div className="space-y-1.5">
                                <Label>压缩模型绑定</Label>
                                <ModelSelect
                                    models={llmModels}
                                    value={bindingsForm.summaryModel || "__empty__"}
                                    emptyLabel="跟随默认 summary 角色"
                                    placeholder="未单独绑定时使用默认 summary 角色"
                                    onValueChange={(value) => {
                                        setBindingsForm((prev) => ({
                                            ...prev,
                                            summaryModel: value,
                                        }));
                                    }}
                                />
                                <p className="text-xs text-slate-500">如果压缩模型窗口小于主模型，系统会自动切块压缩后再递归汇总。</p>
                            </div>
                        </div>
                    </ConfigCard>
                </div>

                <div className="mt-6 grid gap-6 xl:grid-cols-2">
                    <ConfigCard title={"app.admin.dashboard.context.page.k499432c1"} description={"app.admin.dashboard.context.page.k1d384393"}>
                        <div className="grid gap-5 md:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.kf12cd060")}</Label>
                                <Input
                                    type="number"
                                    min={3}
                                    max={100}
                                    value={policyForm.runtime_adapters?.plugin_host?.window_size ?? 15}
                                    onChange={(event) => updatePluginHostAdapter({ window_size: Number(event.target.value) })}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.kcb2c9afb")}</Label>
                                <Input
                                    type="number"
                                    min={1}
                                    max={50}
                                    value={policyForm.runtime_adapters?.plugin_host?.max_summary_items ?? 8}
                                    onChange={(event) => updatePluginHostAdapter({ max_summary_items: Number(event.target.value) })}
                                />
                            </div>
                        </div>
                    </ConfigCard>

                    <ConfigCard title={"app.admin.dashboard.context.page.k949f0930"} description={"app.admin.dashboard.context.page.kb53e7ba5"}>
                        <div className="grid gap-5 md:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.kc8b1964d")}</Label>
                                <Input
                                    type="number"
                                    min={1}
                                    max={20}
                                    value={policyForm.runtime_adapters?.automation?.recent_run_limit ?? 3}
                                    onChange={(event) => updateAutomationAdapter({ recent_run_limit: Number(event.target.value) })}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.kc785dd7a")}</Label>
                                <Input
                                    type="number"
                                    min={1}
                                    max={50}
                                    value={policyForm.runtime_adapters?.automation?.job_memory_limit ?? 6}
                                    onChange={(event) => updateAutomationAdapter({ job_memory_limit: Number(event.target.value) })}
                                />
                            </div>
                        </div>
                    </ConfigCard>
                </div>
            </AdvancedSection>
        </div>
    );
}

export default MemoryContextPanel;
