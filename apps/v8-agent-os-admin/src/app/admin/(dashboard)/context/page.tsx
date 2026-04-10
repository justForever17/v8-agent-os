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
import { lt, type LocalizedText } from "@/lib/locale";
import { cn } from "@/lib/utils";

interface SysModel {
    id: string;
    modelId: string;
    name: string;
    type: string;
    provider?: { name?: string; icon?: string };
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
        title: lt("节省", "Lean"),
        description: lt("尽量少调摘要模型，先用规则提炼守住成本和速度。", "Use rules first to keep cost and speed under control."),
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
        title: lt("平衡", "Balanced"),
        description: lt("保留当前默认体验，优先规则摘要，兼顾连续性和花费。", "Keep the default balance with rule-based compression first."),
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
        title: lt("强保真", "High fidelity"),
        description: lt("预算接近极限时允许 LLM 摘要，尽量保住长会话连续性。", "Allow LLM summaries near the limit to preserve long-session continuity."),
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
    if (!preset) return false;

    const compression = policy.compression || {};
    const pluginHost = policy.runtime_adapters?.plugin_host || {};
    const automation = policy.runtime_adapters?.automation || {};

    return (
        Boolean(compression.enabled) === Boolean(preset.compression.enabled) &&
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
        Number(automation.job_memory_limit ?? 0) === preset.runtime_adapters.automation.job_memory_limit
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

function presetLabel(preset: PresetKey | "custom"): LocalizedText | string {
    return PRESET_OPTIONS.find((item) => item.key === preset)?.title || lt("自定义", "Custom");
}

function describeSummaryStrategy(policy: ContextPolicy, bindings: ContextBindings): LocalizedText | string {
    const compression = policy.compression || {};
    if (!compression.enabled) {
        return lt("已关闭", "Disabled");
    }
    if (compression.use_llm_summary) {
        return bindings.summaryModel
            ? lt("高压时启用 LLM 摘要", "LLM summary at high pressure")
            : lt("高压时启用 LLM 摘要（待绑定模型）", "LLM summary at high pressure (bind a model)");
    }
    return lt("规则摘要优先", "Rule summary first");
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
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadData();
    }, []);

    const llmModels = useMemo(
        () => models.filter((model) => ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes((model.type || "LLM").toUpperCase())),
        [models]
    );

    const currentPreset = useMemo(() => detectPreset(policyForm), [policyForm]);

    const updateCompression = (patch: Partial<NonNullable<ContextPolicy["compression"]>>) => {
        setPolicyForm((prev) =>
            normalizePolicy({
                ...prev,
                compression: {
                    ...(prev.compression || {}),
                    ...patch,
                },
            })
        );
    };

    const updatePluginHostAdapter = (patch: Partial<NonNullable<NonNullable<ContextPolicy["runtime_adapters"]>["plugin_host"]>>) => {
        setPolicyForm((prev) =>
            normalizePolicy({
                ...prev,
                runtime_adapters: {
                    ...(prev.runtime_adapters || {}),
                    plugin_host: {
                        ...(prev.runtime_adapters?.plugin_host || {}),
                        ...patch,
                    },
                },
            })
        );
    };

    const updateAutomationAdapter = (patch: Partial<NonNullable<NonNullable<ContextPolicy["runtime_adapters"]>["automation"]>>) => {
        setPolicyForm((prev) =>
            normalizePolicy({
                ...prev,
                runtime_adapters: {
                    ...(prev.runtime_adapters || {}),
                    automation: {
                        ...(prev.runtime_adapters?.automation || {}),
                        ...patch,
                    },
                },
            })
        );
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
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={lt("上下文预算与治理", "Context governance")}
                description={lt("配置 token 预算、历史压缩、运行时适配窗口与摘要模型绑定。", "Tune token budgets, history compaction, runtime adapter windows, and summary model bindings.")}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} label={lt("上下文治理配置", "Context governance config")} />
                        <Button onClick={() => void handleSave()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t(lt("保存", "Save"))}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip
                items={[
                    { label: t(lt("当前档位", "Preset")), value: t(presetLabel(currentPreset)), description: t(lt("不匹配标准档位时会显示为自定义。", "Shows Custom when no preset matches.")) },
                    { label: t(lt("摘要方式", "Summary mode")), value: t(describeSummaryStrategy(policyForm, bindingsForm)), description: t(lt("预算溢出时优先规则提炼，必要时再启用摘要模型。", "Use rules first and only switch to the summary model when pressure is high.")) },
                    { label: t(lt("最近保留", "Recent keep")), value: t(lt(`${policyForm.compression?.keep_recent_messages ?? 6} 条消息`, `${policyForm.compression?.keep_recent_messages ?? 6} messages`)), description: t(lt("压缩发生时，这部分最近历史会原样保留。", "These recent messages stay untouched during compression.")) },
                    { label: t(lt("治理范围", "Governance scope")), value: t(lt("预算 / 压缩 / 适配窗口", "Budgets / compaction / adapter windows")), description: t(lt("Scope、项目绑定和渠道归属仍由运行时主链判定。", "Scope, project binding, and channel ownership are still resolved by the runtime pipeline.")) },
                    { label: t(lt("配置来源", "Config source")), value: t(lt("统一配置 + 模型绑定", "Shared config + model binding")), description: t(lt("策略写入上下文配置，摘要模型仍来自角色模型绑定。", "Policy lives in context config. The summary model still comes from model bindings.")) },
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
                                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
                        )}
                        onClick={() => handleApplyPreset(option.key)}
                    >
                        <div className="flex items-center justify-between gap-3">
                        <div className="text-base font-semibold">{t(option.title)}</div>
                            <AlignLeft className="h-4 w-4 shrink-0" />
                        </div>
                        <div className="mt-2 text-sm leading-6 text-slate-500">{t(option.description)}</div>
                        <div className="mt-4 text-xs leading-5 text-slate-500">
                            {t(
                                lt(
                                    `触发 ${option.compression.soft_trigger_ratio}/${option.compression.hard_trigger_ratio} · 最近保留 ${option.compression.keep_recent_messages} 条 · 输入预算 ${option.compression.max_summary_input_tokens} tokens`,
                                    `Trigger ${option.compression.soft_trigger_ratio}/${option.compression.hard_trigger_ratio} · Keep ${option.compression.keep_recent_messages} · Input ${option.compression.max_summary_input_tokens} tokens`,
                                ),
                            )}
                        </div>
                    </button>
                ))}
            </div>

            <StatusNotice title={lt("系统优先按 token 预算决定何时压缩旧上下文。", "Compression is driven by token pressure, not message count first.")} tone="info" />

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />

            <AdvancedSection
                title={lt("自定义控制", "Advanced controls")}
                description={lt("只有需要手动微调预算、摘要模型或运行时窗口时再展开。", "Expand only when you need manual budget, summary-model, or window tuning.")}
                defaultOpen={false}
            >
                <div className="grid gap-6 xl:grid-cols-2">
                    <ConfigCard title={lt("基础预算与边界", "Budgets & limits")} description={lt("设置压缩触发点和整体预算。", "Set compression triggers and overall limits.")}>
                        <div className="space-y-5">
                            <div className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="space-y-1">
                                    <div className="text-sm font-medium text-slate-900">{t(lt("预算压缩总开关", "Compression switch"))}</div>
                                    <div className="text-xs leading-5 text-slate-500">{t(lt("关闭后系统不会主动提炼旧上下文，长会话更容易吃满输入窗口。", "When off, the system will not proactively compress old context and long sessions will hit the context limit sooner."))}</div>
                                </div>
                                <Switch
                                    checked={policyForm.compression?.enabled ?? true}
                                    onCheckedChange={(checked) => updateCompression({ enabled: checked })}
                                />
                            </div>

                            <div className="grid gap-5 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>{t(lt("最大递归/反思步数", "Max reflections"))}</Label>
                                    <Input
                                        type="number"
                                        min={10}
                                        max={5000}
                                        value={policyForm.recursion_limit ?? 100}
                                        onChange={(event) =>
                                            setPolicyForm((prev) =>
                                                normalizePolicy({
                                                    ...prev,
                                                    recursion_limit: Number(event.target.value),
                                                })
                                            )
                                        }
                                    />
                                    <p className="text-xs text-slate-500">{t(lt("用来阻断失控反思和死循环，不参与预设档位判断。", "Prevents runaway reflection loops. This is not part of preset matching."))}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t(lt("回退窗口 token", "Fallback context window"))}</Label>
                                    <Input
                                        type="number"
                                        min={2048}
                                        max={2000000}
                                        value={policyForm.compression?.default_context_window_tokens ?? 32000}
                                        onChange={(event) => updateCompression({ default_context_window_tokens: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">{t(lt("目标模型没有声明 context window 时，系统会使用这个预算基线。", "Used when the target model does not expose a context window."))}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t(lt("软触发比率", "Soft trigger"))}</Label>
                                    <Input
                                        type="number"
                                        min={0.1}
                                        max={0.95}
                                        step={0.01}
                                        value={policyForm.compression?.soft_trigger_ratio ?? 0.55}
                                        onChange={(event) => updateCompression({ soft_trigger_ratio: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">{t(lt("达到这个预算水位后，系统会优先尝试压缩旧上下文。", "The system starts trying to compress old context at this pressure level."))}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t(lt("硬触发比率", "Hard trigger"))}</Label>
                                    <Input
                                        type="number"
                                        min={0.15}
                                        max={0.99}
                                        step={0.01}
                                        value={policyForm.compression?.hard_trigger_ratio ?? 0.75}
                                        onChange={(event) => updateCompression({ hard_trigger_ratio: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">{t(lt("达到更高水位后，会更积极地提炼旧上下文来保护主执行链。", "At a higher threshold, the system compresses more aggressively to protect the main execution path."))}</p>
                                </div>
                            </div>
                        </div>
                    </ConfigCard>

                    <ConfigCard title={lt("摘要策略", "Summary policy")} description={lt("设置保留策略和摘要模型。", "Choose what to preserve and when to use a summary model.")}>
                        <div className="space-y-5">
                            <div className="grid gap-5 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>{t(lt("最近保留消息数", "Recent messages to keep"))}</Label>
                                    <Input
                                        type="number"
                                        min={1}
                                        max={100}
                                        value={policyForm.compression?.keep_recent_messages ?? 6}
                                        onChange={(event) => updateCompression({ keep_recent_messages: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">{t(lt("压缩发生时，这部分最近消息会保持原样，最后一条用户消息会被强制保留。", "These messages stay intact during compression. The last user message is always preserved."))}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t(lt("摘要输入 token 预算", "Summary input budget"))}</Label>
                                    <Input
                                        type="number"
                                        min={512}
                                        max={200000}
                                        value={policyForm.compression?.max_summary_input_tokens ?? 5000}
                                        onChange={(event) => updateCompression({ max_summary_input_tokens: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">{t(lt("历史摘要优先按 token 预算选样，再决定用规则摘要还是 LLM 摘要。", "History is sampled by token budget first, then summarized by rules or an LLM."))}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t(lt("输入条数保险丝", "Message-count fuse"))}</Label>
                                    <Input
                                        type="number"
                                        min={5}
                                        max={200}
                                        value={policyForm.compression?.max_summary_input_messages ?? 60}
                                        onChange={(event) => updateCompression({ max_summary_input_messages: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">{t(lt("只在极端情况下防止候选消息过多，不再作为主策略。", "Only used as a final guard when candidate messages explode."))}</p>
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{t(lt("摘要输出上限（tokens）", "Summary output cap"))}</Label>
                                    <Input
                                        type="number"
                                        min={128}
                                        max={8000}
                                        value={policyForm.compression?.max_summary_output_tokens ?? 800}
                                        onChange={(event) => updateCompression({ max_summary_output_tokens: Number(event.target.value) })}
                                    />
                                    <p className="text-xs text-slate-500">{t(lt("限制历史摘要块的体积，避免压缩结果反过来挤占主上下文。", "Caps summary size so the compressed result does not crowd out live context."))}</p>
                                </div>
                            </div>

                            <div className="flex items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                                <div className="space-y-1">
                                    <div className="text-sm font-medium text-slate-900">{t(lt("智能 LLM 摘要", "LLM summary"))}</div>
                                    <div className="text-xs leading-5 text-slate-500">{t(lt("只有在高压水位时，系统才会考虑调用摘要模型。关闭后始终使用规则提炼。", "The summary model is only used at high pressure. When off, rules are always used."))}</div>
                                </div>
                                <Switch
                                    checked={policyForm.compression?.use_llm_summary ?? false}
                                    onCheckedChange={(checked) => updateCompression({ use_llm_summary: checked })}
                                />
                            </div>

                            <div className="space-y-1.5">
                                <Label>{t(lt("摘要模型绑定", "Summary model"))}</Label>
                                <Select
                                    value={bindingsForm.summaryModel || "__empty__"}
                                    onValueChange={(value) => {
                                        setBindingsForm((prev) => ({
                                            ...prev,
                                            summaryModel: value === "__empty__" ? "" : value,
                                        }));
                                    }}
                                >
                                    <SelectTrigger className="w-full">
                                        <SelectValue placeholder={t(lt("未绑定摘要模型", "No summary model bound"))} />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="__empty__">{t(lt("未绑定", "Unbound"))}</SelectItem>
                                        {llmModels.map((model) => (
                                            <SelectItem key={modelValue(model)} value={modelValue(model)}>
                                                {model.name || modelValue(model)} {model.provider?.name ? `(${model.provider.name})` : ""}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <p className="text-xs text-slate-500">{t(lt("这个字段来自模型绑定，不会写回上下文策略文件。", "This field comes from model bindings and is not written into the context policy file."))}</p>
                            </div>
                        </div>
                    </ConfigCard>
                </div>

                <div className="mt-6 grid gap-6 xl:grid-cols-2">
                    <ConfigCard title={lt("PluginHost 适配", "PluginHost adaptation")} description={lt("设置 PluginHost / 渠道运行时的上下文窗口。", "Tune the context window for PluginHost and channel-backed runtimes.")}>
                        <div className="grid gap-5 md:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label>{t(lt("上下文窗口消息数", "Context window size"))}</Label>
                                <Input
                                    type="number"
                                    min={3}
                                    max={100}
                                    value={policyForm.runtime_adapters?.plugin_host?.window_size ?? 15}
                                    onChange={(event) => updatePluginHostAdapter({ window_size: Number(event.target.value) })}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label>{t(lt("最多保留摘要条目", "Max summary items"))}</Label>
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

                    <ConfigCard title={lt("Automation 适配", "Automation adaptation")} description={lt("设置自动化的上下文窗口。", "Tune the context window for automation runs.")}>
                        <div className="grid gap-5 md:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label>{t(lt("近期运行摘要数量", "Recent run summaries"))}</Label>
                                <Input
                                    type="number"
                                    min={1}
                                    max={20}
                                    value={policyForm.runtime_adapters?.automation?.recent_run_limit ?? 3}
                                    onChange={(event) => updateAutomationAdapter({ recent_run_limit: Number(event.target.value) })}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label>{t(lt("Job Memory 保留数量", "Job memory keep"))}</Label>
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
        </AdminPageShell>
    );
}
