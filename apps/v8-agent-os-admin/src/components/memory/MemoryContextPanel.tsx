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
import { Slider } from "@/components/ui/slider";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { cn } from "@/lib/utils";
import { ik, tg, ti } from "@/i18n/admin-legacy";
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
  maxGraphContinuations?: number;
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
  maxGraphContinuations: 5,
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
    noticeable_latency_ms: 800
  },
  runtime_adapters: {
    automation: {
      recent_run_limit: 3,
      job_memory_limit: 6
    }
  }
};
const DEFAULT_BINDINGS: ContextBindings = {
  summaryModel: ""
};
const PRESET_OPTIONS = [{
  key: "saving",
  title: ik("kc837edc258"),
  description: ik("k5740d4d0b2"),
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
    noticeable_latency_ms: 600
  },
  runtime_adapters: {
    automation: {
      recent_run_limit: 2,
      job_memory_limit: 4
    }
  }
}, {
  key: "balanced",
  title: ik("kd86368eac0"),
  description: ik("kf9c62a976a"),
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
    noticeable_latency_ms: 800
  },
  runtime_adapters: {
    automation: {
      recent_run_limit: 3,
      job_memory_limit: 6
    }
  }
}, {
  key: "high_fidelity",
  title: ik("k2dd09405a4"),
  description: ik("kdf256430ef"),
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
    noticeable_latency_ms: 1200
  },
  runtime_adapters: {
    automation: {
      recent_run_limit: 4,
      job_memory_limit: 8
    }
  }
}] as const;
type PresetKey = (typeof PRESET_OPTIONS)[number]["key"];
function normalizePolicy(policy?: ContextPolicy): ContextPolicy {
  const maxGraphContinuations = Number(policy?.maxGraphContinuations ?? (policy as Record<string, unknown> | undefined)?.max_graph_continuations ?? DEFAULT_POLICY.maxGraphContinuations ?? 5);
  return {
    ...DEFAULT_POLICY,
    ...(policy || {}),
    maxGraphContinuations: Number.isFinite(maxGraphContinuations) ? Math.max(0, Math.min(20, Math.round(maxGraphContinuations))) : 5,
    compression: {
      ...DEFAULT_POLICY.compression,
      ...(policy?.compression || {})
    },
    runtime_adapters: {
      ...DEFAULT_POLICY.runtime_adapters,
      ...(policy?.runtime_adapters || {}),
      automation: {
        ...DEFAULT_POLICY.runtime_adapters?.automation,
        ...(policy?.runtime_adapters?.automation || {})
      }
    }
  };
}
function canonicalizePolicyForSave(policy: ContextPolicy): ContextPolicy {
  const normalized = normalizePolicy(policy);
  return {
    ...normalized,
    runtime_adapters: {
      automation: {
        ...(normalized.runtime_adapters?.automation || {})
      }
    }
  };
}
function matchesPreset(policy: ContextPolicy, presetKey: PresetKey) {
  const preset = PRESET_OPTIONS.find(item => item.key === presetKey);
  if (!preset) return false;
  const compression = policy.compression || {};
  const automation = policy.runtime_adapters?.automation || {};
  return Boolean(compression.enabled) === Boolean(preset.compression.enabled) && String(compression.mode || "") === String(preset.compression.mode || "") && Number(compression.trigger_ratio ?? 0) === preset.compression.trigger_ratio && Number(compression.keep_recent_turns ?? 0) === preset.compression.keep_recent_turns && Number(compression.keep_recent_messages ?? 0) === preset.compression.keep_recent_messages && Boolean(compression.use_llm_summary) === Boolean(preset.compression.use_llm_summary) && Number(compression.max_summary_input_tokens ?? 0) === preset.compression.max_summary_input_tokens && Number(compression.max_summary_input_messages ?? 0) === preset.compression.max_summary_input_messages && Number(compression.max_summary_output_tokens ?? 0) === preset.compression.max_summary_output_tokens && Number(compression.compression_model_safety_ratio ?? 0) === preset.compression.compression_model_safety_ratio && Number(compression.noticeable_latency_ms ?? 0) === preset.compression.noticeable_latency_ms && Number(automation.recent_run_limit ?? 0) === preset.runtime_adapters.automation.recent_run_limit && Number(automation.job_memory_limit ?? 0) === preset.runtime_adapters.automation.job_memory_limit;
}
function detectPreset(policy: ContextPolicy): PresetKey | "custom" {
  const matched = PRESET_OPTIONS.find(item => matchesPreset(policy, item.key));
  return matched?.key || "custom";
}
function applyPreset(policy: ContextPolicy, presetKey: PresetKey): ContextPolicy {
  const preset = PRESET_OPTIONS.find(item => item.key === presetKey) || PRESET_OPTIONS[1];
  return {
    ...policy,
    compression: {
      ...(policy.compression || {}),
      ...preset.compression
    },
    runtime_adapters: {
      ...(policy.runtime_adapters || {}),
      automation: {
        ...(policy.runtime_adapters?.automation || {}),
        ...preset.runtime_adapters.automation
      }
    }
  };
}
function presetLabel(t: ReturnType<typeof useT>, preset: PresetKey | "custom"): string {
  return PRESET_OPTIONS.find(item => item.key === preset)?.title ? t(PRESET_OPTIONS.find(item => item.key === preset)?.title || ik("k14f3ad1440")) : ti(t, "k14f3ad1440");
}
function describeSummaryStrategy(t: ReturnType<typeof useT>, policy: ContextPolicy, bindings: ContextBindings): string {
  const compression = policy.compression || {};
  if (!compression.enabled) {
    return ti(t, "k64cdf64595");
  }
  if (compression.use_llm_summary) {
    return bindings.summaryModel ? ti(t, "kc107440caa") : ti(t, "k69366dd540");
  }
  return ti(t, "ka5a4186031");
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
      const [contextEnvelope, modelList] = await Promise.all([fetchConfigDomain<ContextDomainData>("context"), fetch("/api/models", {
        cache: "no-store"
      }).then(response => response.json().catch(() => []))]);
      setEnvelope(contextEnvelope);
      setPolicyForm(normalizePolicy(contextEnvelope.data?.policy));
      setBindingsForm({
        ...DEFAULT_BINDINGS,
        ...(contextEnvelope.data?.modelBindings || {})
      });
      setModels(Array.isArray(modelList) ? modelList : []);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void loadData();
  }, []);
  const llmModels = useMemo(() => models.filter(model => ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes((model.type || "LLM").toUpperCase())), [models]);
  const currentPreset = useMemo(() => detectPreset(policyForm), [policyForm]);
  const updateCompression = (patch: Partial<NonNullable<ContextPolicy["compression"]>>) => {
    setPolicyForm(prev => normalizePolicy({
      ...prev,
      compression: {
        ...(prev.compression || {}),
        ...patch
      }
    }));
  };
  const updateAutomationAdapter = (patch: Partial<NonNullable<NonNullable<ContextPolicy["runtime_adapters"]>["automation"]>>) => {
    setPolicyForm(prev => normalizePolicy({
      ...prev,
      runtime_adapters: {
        ...(prev.runtime_adapters || {}),
        automation: {
          ...(prev.runtime_adapters?.automation || {}),
          ...patch
        }
      }
    }));
  };
  const handleApplyPreset = (presetKey: PresetKey) => {
    setPolicyForm(prev => normalizePolicy(applyPreset(prev, presetKey)));
  };
  const handleSave = async () => {
    setSaving(true);
    try {
      const next = await saveConfigDomain<ContextDomainData>("context", {
        data: {
          policy: canonicalizePolicyForSave(policyForm),
          modelBindings: bindingsForm
        }
      });
      setEnvelope(next);
      setPolicyForm(normalizePolicy(next.data?.policy));
      setBindingsForm({
        ...DEFAULT_BINDINGS,
        ...(next.data?.modelBindings || {})
      });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
    } finally {
      setSaving(false);
    }
  };
  if (loading || !envelope) {
    return <div className="flex min-h-[320px] items-center justify-center rounded-2xl border border-border/60 bg-background/80">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>;
  }
  return <div className="space-y-6">
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

            <DomainSummaryStrip items={[{
      label: tg(t, "6dee3a47"),
      value: presetLabel(t, currentPreset),
      description: tg(t, "60af6ce0")
    }, {
      label: tg(t, "4ffaf26a"),
      value: describeSummaryStrategy(t, policyForm, bindingsForm),
      description: tg(t, "70b33f52")
    }, {
      label: tg(t, "31b467d8"),
      value: tg(t, "81fb3b15", {
        value1: policyForm.compression?.keep_recent_turns ?? 4,
        value2: policyForm.compression?.keep_recent_messages ?? 8
      }),
      description: tg(t, "7c1d5691")
    }, {
      label: tg(t, "3c44d473"),
      value: `${Math.round((policyForm.compression?.trigger_ratio ?? 0.94) * 100)}%`,
      description: tg(t, "e96fe6e9")
    }, {
      label: tg(t, "81d60e7b"),
      value: policyForm.compression?.mode === "persistent_baseline" ? tg(t, "3d9158b9") : policyForm.compression?.mode || t("app.admin.dashboard.system.base.page.k6ed9c299"),
      description: tg(t, "2afde78b")
    }, {
      label: t("components.memory.MemoryContextPanel.maxGraphContinuationsSummaryLabel"),
      value: t("components.memory.MemoryContextPanel.maxGraphContinuationsSummaryValue", {
        count: policyForm.maxGraphContinuations ?? 5
      }),
      description: t("components.memory.MemoryContextPanel.maxGraphContinuationsSummaryDescription")
    }]} />


            <div className="grid gap-4 lg:grid-cols-3">
                {PRESET_OPTIONS.map(option => <button key={option.key} type="button" className={cn("rounded-2xl border px-5 py-5 text-left text-foreground shadow-sm transition-colors", currentPreset === option.key ? "border-primary/40 bg-primary/10" : "border-border bg-card hover:border-input")} onClick={() => handleApplyPreset(option.key)}>

                        <div className="flex items-center justify-between gap-3">
                            <div className="text-base font-semibold">{t(option.title)}</div>
                            <AlignLeft className="h-4 w-4 shrink-0" />
                        </div>
                        <div className="mt-2 text-sm leading-6 text-muted-foreground">{t(option.description)}</div>
                        <div className="mt-4 text-xs leading-5 text-muted-foreground">
                            {tg(t, "a05320e1")} {Math.round((option.compression.trigger_ratio ?? 0.94) * 100)}{tg(t, "63c5e541")} {option.compression.keep_recent_turns} {tg(t, "74fcafe5")} {option.compression.max_summary_input_tokens}
                        </div>
                    </button>)}
            </div>

            <StatusNotice title={"app.admin.dashboard.context.page.kce9d4f4c"} tone="info" />

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />

            <AdvancedSection title={"app.admin.dashboard.context.page.k8d1286a2"} description={"app.admin.dashboard.context.page.k71a2c636"} defaultOpen={false}>
                <div className="grid gap-6 xl:grid-cols-2">
                    <ConfigCard title={tg(t, "b7778df4")} description={tg(t, "b6ca53e1")}>
                        <div className="space-y-5">
                            <SettingToggleCard
                                title={tg(t, "f59328c7")}
                                description={tg(t, "7d9870f2")}
                                checked={policyForm.compression?.enabled ?? true}
                                onCheckedChange={checked => updateCompression({
                                    enabled: checked
                                })}
                                className="rounded-2xl border-border bg-muted/35 px-4 py-3"
                            />

                            <div className="grid gap-5 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>{tg(t, "8d68fefc")}</Label>
                                    <Input type="number" min={10} max={5000} value={policyForm.recursion_limit ?? 100} onChange={event => setPolicyForm(prev => normalizePolicy({
                  ...prev,
                  recursion_limit: Number(event.target.value)
                }))} />
                                </div>

                                <div className="space-y-2 rounded-2xl border border-border bg-card px-4 py-3 md:col-span-2">
                                    <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                                        <div className="space-y-1">
                                            <Label>{t("components.memory.MemoryContextPanel.maxGraphContinuationsLabel")}</Label>
                                        </div>
                                        <Input className="w-24" type="number" min={0} max={20} value={policyForm.maxGraphContinuations ?? 5} onChange={event => setPolicyForm(prev => normalizePolicy({
                  ...prev,
                  maxGraphContinuations: Number(event.target.value)
                }))} />
                                    </div>
                                    <Slider value={[policyForm.maxGraphContinuations ?? 5]} min={0} max={20} step={1} onValueChange={([value]) => setPolicyForm(prev => normalizePolicy({
                ...prev,
                maxGraphContinuations: value
              }))} />
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{tg(t, "726b4a24")}</Label>
                                    <Input type="number" min={2048} max={2000000} value={policyForm.compression?.default_context_window_tokens ?? 32000} onChange={event => updateCompression({
                  default_context_window_tokens: Number(event.target.value)
                })} />
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{tg(t, "5521e31d")}</Label>
                                    <Input type="number" min={0.92} max={0.95} step={0.01} value={policyForm.compression?.trigger_ratio ?? 0.94} onChange={event => updateCompression({
                  trigger_ratio: Number(event.target.value),
                  hard_trigger_ratio: Number(event.target.value)
                })} />
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{tg(t, "219d9b58")}</Label>
                                    <Input type="number" min={1} max={40} value={policyForm.compression?.keep_recent_turns ?? 4} onChange={event => updateCompression({
                  keep_recent_turns: Number(event.target.value)
                })} />
                                </div>
                            </div>
                        </div>
                    </ConfigCard>

                    <ConfigCard title={tg(t, "4fbe2a66")} description={tg(t, "a75910a7")}>
                        <div className="space-y-5">
                            <div className="grid gap-5 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>{tg(t, "d89c8154")}</Label>
                                    <Input type="number" min={1} max={100} value={policyForm.compression?.keep_recent_messages ?? 6} onChange={event => updateCompression({
                  keep_recent_messages: Number(event.target.value)
                })} />
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{tg(t, "72607929")}</Label>
                                    <Input type="number" min={512} max={200000} value={policyForm.compression?.max_summary_input_tokens ?? 5000} onChange={event => updateCompression({
                  max_summary_input_tokens: Number(event.target.value)
                })} />
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{tg(t, "b5d9653b")}</Label>
                                    <Input type="number" min={5} max={200} value={policyForm.compression?.max_summary_input_messages ?? 60} onChange={event => updateCompression({
                  max_summary_input_messages: Number(event.target.value)
                })} />
                                </div>

                                <div className="space-y-1.5">
                                    <Label>{tg(t, "fbfd44de")}</Label>
                                    <Input type="number" min={128} max={8000} value={policyForm.compression?.max_summary_output_tokens ?? 800} onChange={event => updateCompression({
                  max_summary_output_tokens: Number(event.target.value)
                })} />
                                </div>
                            </div>

                            <SettingToggleCard
                                title={tg(t, "b42d73ff")}
                                description={tg(t, "359daca3")}
                                checked={policyForm.compression?.use_llm_summary ?? false}
                                onCheckedChange={checked => updateCompression({
                                    use_llm_summary: checked
                                })}
                                className="rounded-2xl border-border bg-muted/35 px-4 py-3"
                            />

                            <div className="grid gap-5 md:grid-cols-2">
                                <div className="space-y-1.5">
                                    <Label>{tg(t, "328696ea")}</Label>
                                    <Input type="number" min={0.5} max={0.95} step={0.01} value={policyForm.compression?.compression_model_safety_ratio ?? 0.9} onChange={event => updateCompression({
                  compression_model_safety_ratio: Number(event.target.value)
                })} />
                                </div>
                                <div className="space-y-1.5">
                                    <Label>{tg(t, "beaa2f75")}</Label>
                                    <Input type="number" min={50} max={60000} step={50} value={policyForm.compression?.noticeable_latency_ms ?? 800} onChange={event => updateCompression({
                  noticeable_latency_ms: Number(event.target.value)
                })} />
                                </div>
                            </div>

                            <div className="space-y-1.5">
                                <Label>{tg(t, "d48bda26")}</Label>
                                <ModelSelect models={llmModels} value={bindingsForm.summaryModel || "__empty__"} emptyLabel={tg(t, "275de093")} placeholder={tg(t, "fd8f3a4f")} onValueChange={value => {
                setBindingsForm(prev => ({
                  ...prev,
                  summaryModel: value
                }));
              }} />
                            </div>
                        </div>
                    </ConfigCard>
                </div>

                <div className="mt-6 grid gap-6 xl:grid-cols-2">
                    <ConfigCard title={"app.admin.dashboard.context.page.k949f0930"} description={"app.admin.dashboard.context.page.kb53e7ba5"}>
                        <div className="grid gap-5 md:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.kc8b1964d")}</Label>
                                <Input type="number" min={1} max={20} value={policyForm.runtime_adapters?.automation?.recent_run_limit ?? 3} onChange={event => updateAutomationAdapter({
                recent_run_limit: Number(event.target.value)
              })} />

                            </div>
                            <div className="space-y-1.5">
                                <Label>{t("app.admin.dashboard.context.page.kc785dd7a")}</Label>
                                <Input type="number" min={1} max={50} value={policyForm.runtime_adapters?.automation?.job_memory_limit ?? 6} onChange={event => updateAutomationAdapter({
                job_memory_limit: Number(event.target.value)
              })} />

                            </div>
                        </div>
                    </ConfigCard>
                </div>
            </AdvancedSection>
        </div>;
}
export default MemoryContextPanel;
