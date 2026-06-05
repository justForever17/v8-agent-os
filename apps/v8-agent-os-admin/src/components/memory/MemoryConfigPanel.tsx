"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { SettingToggleCard } from "@/components/admin-shell/SettingToggleCard";
import { Loader2, Save } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";
import { ModelSelect } from "@/components/models/ModelSelect";
import { ik, tg } from "@/i18n/admin-legacy";
interface SysModel {
  id: string;
  modelRef?: string;
  providerId?: string;
  modelId: string;
  name: string;
  type: string;
  capabilityClass?: string | null;
  capabilities?: Record<string, boolean> | string[] | null;
  provider: {
    id?: string;
    name: string;
    icon?: string;
  };
  providerName?: string;
}
function modelHasCapability(model: SysModel, key: string): boolean {
  const caps = model.capabilities;
  if (Array.isArray(caps)) return caps.map(item => String(item).toLowerCase()).includes(key.toLowerCase());
  if (caps && typeof caps === "object") return Boolean(caps[key]);
  return false;
}
function isEmbeddingModel(model: SysModel): boolean {
  return String(model.type || "").toUpperCase() === "EMBEDDING" || String(model.capabilityClass || "").toLowerCase() === "embedding" || modelHasCapability(model, "embedding");
}
function isRerankModel(model: SysModel): boolean {
  const type = String(model.type || "").toUpperCase();
  const capabilityClass = String(model.capabilityClass || "").toLowerCase();
  return type === "RERANK" || type === "RERANKER" || capabilityClass === "rerank" || capabilityClass === "reranker" || modelHasCapability(model, "rerank");
}
interface MemoryConfig {
  extraction_model?: string;
  extraction_temperature?: number;
  embedding_model?: string;
  reranker_model?: string;
  recall_strategy?: string;
  recall_top_k?: number;
  retrieval_threshold?: number;
  passive_injection_enabled?: boolean;
  passive_context_profile?: string;
  passive_summary_enabled?: boolean;
  passive_memory_map_enabled?: boolean;
  passive_recent_activity_teaser_enabled?: boolean;
  passive_recent_activity_teaser_limit?: number;
  passive_memory_map_node_limit?: number;
  max_recent_days?: number;
  max_context_tokens?: number;
  extraction_enabled?: boolean;
  graph_enabled?: boolean;
  fts_enabled?: boolean;
  workflowMemory?: {
    enabled?: boolean;
    hintInjectionEnabled?: boolean;
    progressiveHintsEnabled?: boolean;
    minSuccessCount?: number;
    errorfulSuccessRequiresUserAcceptance?: boolean;
    maxInjectedHints?: number;
    maxHintChars?: number;
    maxActiveWorkflowGuidesPerRun?: number;
    quarantineOnNegativeFeedback?: boolean;
    requireApprovalForSideEffects?: boolean;
    riskTierActivationPolicy?: Record<string, string>;
  };
  preference_importance_threshold?: number;
  preference_confidence_threshold?: number;
  knowledge_importance_threshold?: number;
  knowledge_confidence_threshold?: number;
  global_knowledge_importance_threshold?: number;
  global_knowledge_confidence_threshold?: number;
  global_operational_importance_threshold?: number;
  global_operational_confidence_threshold?: number;
  recommended_retrieval_threshold?: number;
  retrieval_threshold_source?: string;
  retrieval_threshold_is_default?: boolean;
}
interface DurablePolicyDefaults {
  preference_importance_threshold: number;
  preference_confidence_threshold: number;
  knowledge_importance_threshold: number;
  knowledge_confidence_threshold: number;
  global_knowledge_importance_threshold: number;
  global_knowledge_confidence_threshold: number;
  global_operational_importance_threshold: number;
  global_operational_confidence_threshold: number;
}
type DurablePolicyPresets = Record<string, DurablePolicyDefaults>;
interface MemoryConfigResponse extends MemoryConfig {
  durable_policy_defaults?: DurablePolicyDefaults;
  durable_policy_presets?: DurablePolicyPresets;
  recommended_durable_policy_preset?: string;
}
const MEMORY_DURABLE_POLICY_DEFAULTS: DurablePolicyDefaults = {
  preference_importance_threshold: 35,
  preference_confidence_threshold: 0.45,
  knowledge_importance_threshold: 35,
  knowledge_confidence_threshold: 0.45,
  global_knowledge_importance_threshold: 50,
  global_knowledge_confidence_threshold: 0.6,
  global_operational_importance_threshold: 45,
  global_operational_confidence_threshold: 0.55
};
const DURABLE_POLICY_PRESET_LABELS: Record<string, string> = {
  learning_first: ik("keb508e0d0e"),
  balanced: ik("kd86368eac0"),
  quality_first: ik("k7ee63982da")
};
const DURABLE_POLICY_PRESET_DESCRIPTIONS: Record<string, string> = {
  learning_first: ik("k486828ac73"),
  balanced: ik("k304bf33a9f"),
  quality_first: ik("k26e0f53971")
};
const DURABLE_POLICY_KEYS: Array<keyof DurablePolicyDefaults> = ["preference_importance_threshold", "preference_confidence_threshold", "knowledge_importance_threshold", "knowledge_confidence_threshold", "global_knowledge_importance_threshold", "global_knowledge_confidence_threshold", "global_operational_importance_threshold", "global_operational_confidence_threshold"];
function buildMemoryConfigSavePayload(config: MemoryConfig): MemoryConfig {
  const {
    recommended_retrieval_threshold,
    retrieval_threshold_source,
    retrieval_threshold_is_default,
    ...editable
  } = config;
  void recommended_retrieval_threshold;
  void retrieval_threshold_source;
  void retrieval_threshold_is_default;
  return editable;
}
function detectDurablePolicyPreset(config: MemoryConfig, defaults: DurablePolicyDefaults, presets: DurablePolicyPresets): string | "custom" {
  const currentValue = (key: keyof DurablePolicyDefaults) => config[key] as number | undefined ?? defaults[key];
  const entries = Object.entries(presets);
  for (const [presetKey, presetValues] of entries) {
    const matched = DURABLE_POLICY_KEYS.every(key => {
      const current = currentValue(key);
      const expected = presetValues[key];
      if (typeof current === "number" && typeof expected === "number") {
        return Math.abs(current - expected) < 0.0001;
      }
      return current === expected;
    });
    if (matched) {
      return presetKey;
    }
  }
  return "custom";
}
interface RecallPreviewItem {
  id: string;
  fact: string;
  source?: string;
  scope?: string;
  category?: string;
  raw_relevance_score?: number;
  final_relevance_score?: number;
  accepted?: boolean;
  reject_reason?: string;
}
interface MemoryInjectionPackItem {
  id?: string;
  content?: string;
  category?: string;
  scope?: string;
  source?: string;
  confidence?: number;
  whySelected?: string;
  doNotInjectReason?: string;
}
interface MemoryInjectionPack {
  version?: string;
  mode?: string;
  selectedMemory?: MemoryInjectionPackItem[];
  rejectedMemory?: MemoryInjectionPackItem[];
  doNotInjectReasons?: Array<{
    reason?: string;
    count?: number;
    detail?: string;
  }>;
  stats?: {
    selectedCount?: number;
    rejectedPreviewCount?: number;
    candidateCount?: number;
    latencyTier?: string;
    visualEvidenceCount?: number;
  };
}
interface RecallPreviewResponse {
  query?: string;
  threshold_snapshot?: number;
  effective_acceptance_threshold?: number;
  threshold_source?: string;
  recommended_retrieval_threshold?: number;
  retrieval_threshold_is_default?: boolean;
  diagnostics?: {
    graph_allowed?: boolean;
    graph_reject_reason?: string;
    graph_entities?: string[];
    recall_strategy?: string;
    accepted_count?: number;
    rejected_count?: number;
  };
  items?: RecallPreviewItem[];
  memoryInjectionPack?: MemoryInjectionPack;
}
export default function MemoryConfigPanel() {
  const t = useT();
  const [config, setConfig] = useState<MemoryConfig>({});
  const [durableDefaults, setDurableDefaults] = useState<DurablePolicyDefaults>(MEMORY_DURABLE_POLICY_DEFAULTS);
  const [durablePresets, setDurablePresets] = useState<DurablePolicyPresets>({
    balanced: MEMORY_DURABLE_POLICY_DEFAULTS
  });
  const [recommendedDurablePreset, setRecommendedDurablePreset] = useState<string>("balanced");
  const [models, setModels] = useState<SysModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showAdvancedContextSettings, setShowAdvancedContextSettings] = useState(false);
  const [recallQuery, setRecallQuery] = useState("");
  const [recallPreviewLoading, setRecallPreviewLoading] = useState(false);
  const [recallPreviewError, setRecallPreviewError] = useState<string | null>(null);
  const [recallPreview, setRecallPreview] = useState<RecallPreviewResponse | null>(null);
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [confRes, modRes] = await Promise.all([fetch("/api/settings/memory-config"), fetch("/api/models")]);
      if (confRes.ok) {
        const fetchedConfig: MemoryConfigResponse = await confRes.json();
        const {
          durable_policy_defaults,
          durable_policy_presets,
          recommended_durable_policy_preset,
          ...editableConfig
        } = fetchedConfig;
        if (durable_policy_defaults) {
          setDurableDefaults(durable_policy_defaults);
        }
        if (durable_policy_presets && Object.keys(durable_policy_presets).length > 0) {
          setDurablePresets(durable_policy_presets);
        }
        if (recommended_durable_policy_preset) {
          setRecommendedDurablePreset(recommended_durable_policy_preset);
        }
        setConfig(editableConfig);
      }
      if (modRes.ok) setModels(await modRes.json());
    } catch {/* ignore */} finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    loadData();
  }, [loadData]);
  const handleRecallPreview = useCallback(async () => {
    const normalizedQuery = recallQuery.trim();
    if (!normalizedQuery) {
      setRecallPreview(null);
      setRecallPreviewError(t("components.memory.MemoryConfigPanel.recallPreview.emptyQuery"));
      return;
    }
    setRecallPreviewLoading(true);
    setRecallPreviewError(null);
    try {
      const params = new URLSearchParams({
        q: normalizedQuery
      });
      const response = await fetch(`/api/memory/recall-preview?${params.toString()}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof payload.error === "string" ? payload.error : `Request failed (${response.status})`);
      }
      setRecallPreview(payload);
    } catch (error) {
      setRecallPreview(null);
      setRecallPreviewError(error instanceof Error ? error.message : String(error));
    } finally {
      setRecallPreviewLoading(false);
    }
  }, [recallQuery, t]);
  const handleSave = async () => {
    setSaving(true);
    try {
      await fetch("/api/settings/memory-config", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(buildMemoryConfigSavePayload(config))
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  };
  const currentDurablePreset = detectDurablePolicyPreset(config, durableDefaults, durablePresets);
  const applyDurablePreset = (presetKey: string) => {
    const preset = durablePresets[presetKey];
    if (!preset) {
      return;
    }
    setConfig(prev => ({
      ...prev,
      ...Object.fromEntries(DURABLE_POLICY_KEYS.map(key => [key, preset[key]]))
    }));
  };
  const renderImportanceSlider = (key: keyof DurablePolicyDefaults, label: string, description: string) => <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
                <Label>{label}</Label>
                <span className="text-sm font-mono text-muted-foreground">
                    {Math.round(config[key] as number | undefined ?? durableDefaults[key])}
                    <span className="ml-2 text-xs text-muted-foreground/80">
                        {t("components.memory.MemoryConfigPanel.k5e4b837d")} {durableDefaults[key]}
                    </span>
                </span>
            </div>
            <Slider value={[Math.round(config[key] as number | undefined ?? durableDefaults[key])]} onValueChange={([v]) => setConfig(prev => ({
      ...prev,
      [key]: v
    }))} min={0} max={100} step={1} className="w-full" />
            <p className="text-xs text-muted-foreground">{description}</p>
        </div>;
  const renderConfidenceSlider = (key: keyof DurablePolicyDefaults, label: string, description: string) => <div className="space-y-2">
            <div className="flex items-center justify-between gap-3">
                <Label>{label}</Label>
                <span className="text-sm font-mono text-muted-foreground">
                    {((config[key] as number | undefined ?? durableDefaults[key]) as number).toFixed(2)}
                    <span className="ml-2 text-xs text-muted-foreground/80">
                        {t("components.memory.MemoryConfigPanel.k5e4b837d")} {durableDefaults[key].toFixed(2)}
                    </span>
                </span>
            </div>
            <Slider value={[(config[key] as number | undefined ?? durableDefaults[key]) as number]} onValueChange={([v]) => setConfig(prev => ({
      ...prev,
      [key]: Number(v.toFixed(2))
    }))} min={0} max={1} step={0.01} className="w-full" />
            <p className="text-xs text-muted-foreground">{description}</p>
        </div>;
  if (loading) {
    return <div className="flex items-center justify-center h-48">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>;
  }
  const llmModels = models.filter(m => ['TEXT', 'MULTIMODAL', 'chat', 'LLM'].includes(m.type.toUpperCase() || 'LLM'));
  const embedModels = models.filter(isEmbeddingModel);
  const rerankModels = models.filter(m => isRerankModel(m) || isEmbeddingModel(m));
  return <div className="space-y-4">
            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t("components.memory.MemoryConfigPanel.k92cabc30")}</CardTitle>
                    <CardDescription>{t("components.memory.MemoryConfigPanel.k6b835e95")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="space-y-1.5">
                        <Label>{t("components.memory.MemoryConfigPanel.k3fced67a")}</Label>
                        <ModelSelect models={llmModels} value={config.extraction_model || ""} emptyLabel={t("components.memory.MemoryConfigPanel.k92ef9586")} placeholder={t("components.memory.MemoryConfigPanel.k92ef9586")} onValueChange={val => setConfig(prev => ({
            ...prev,
            extraction_model: val
          }))} />

                        <p className="text-xs text-muted-foreground">{t("components.memory.MemoryConfigPanel.ke4b6e27a")}</p>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center justify-between">
                            <Label>{t("components.memory.MemoryConfigPanel.kd147e797")}</Label>
                            <span className="text-sm font-mono text-muted-foreground">
                                {(config.extraction_temperature ?? 0.3).toFixed(2)}
                            </span>
                        </div>
                        <Slider value={[config.extraction_temperature ?? 0.3]} onValueChange={([v]) => setConfig(prev => ({
            ...prev,
            extraction_temperature: v
          }))} min={0} max={1} step={0.05} className="w-full" />
                        <p className="text-xs text-muted-foreground">{t("components.memory.MemoryConfigPanel.k16320e48")}</p>
                    </div>

                    <div className="space-y-1.5">
                        <Label>{t("components.memory.MemoryConfigPanel.k20b56b59")}</Label>
                        <ModelSelect models={embedModels} value={config.embedding_model || ""} emptyLabel={t("components.memory.MemoryConfigPanel.k32d0f545")} placeholder={t("components.memory.MemoryConfigPanel.k32d0f545")} onValueChange={val => setConfig(prev => ({
            ...prev,
            embedding_model: val
          }))} />

                        <p className="text-xs text-muted-foreground">{t("components.memory.MemoryConfigPanel.k2c0de4e9")}</p>
                    </div>

                    <div className="space-y-1.5">
                        <Label>{t("components.memory.MemoryConfigPanel.k8476c9a1")}</Label>
                        <ModelSelect models={rerankModels} value={config.reranker_model || "none"} emptyValue="none" emptyLabel={t("components.memory.MemoryConfigPanel.k2bc5c99c")} emptyOutputValue="none" placeholder={t("components.memory.MemoryConfigPanel.k5dd610de")} onValueChange={val => setConfig(prev => ({
            ...prev,
            reranker_model: val
          }))} />

                        <p className="text-xs text-muted-foreground">{t("components.memory.MemoryConfigPanel.kfc781832")}</p>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t("components.memory.MemoryConfigPanel.k81935040")}</CardTitle>
                    <CardDescription>{t("components.memory.MemoryConfigPanel.k2014a965")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1.5">
                            <Label>{t("components.memory.MemoryConfigPanel.ke7c165e2")}</Label>
                            <Select value={config.recall_strategy || "balanced"} onValueChange={val => setConfig(prev => ({
              ...prev,
              recall_strategy: val
            }))}>
                                <SelectTrigger className="w-full">
                                    <SelectValue placeholder={t("components.memory.MemoryConfigPanel.k605cbf3e")} />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="balanced">{t("components.memory.MemoryConfigPanel.k4caf35c0")}</SelectItem>
                                    <SelectItem value="semantic">{t("components.memory.MemoryConfigPanel.k49c8bf94")}</SelectItem>
                                    <SelectItem value="keyword">{t("components.memory.MemoryConfigPanel.ke56e6800")}</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-1.5">
                            <Label>{t("components.memory.MemoryConfigPanel.recallTopKLabel")}</Label>
                            <Input type="number" value={config.recall_top_k ?? 3} onChange={e => setConfig(prev => ({
              ...prev,
              recall_top_k: Number(e.target.value)
            }))} min={1} max={10} />
                        </div>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center justify-between">
                            <Label>{t("components.memory.MemoryConfigPanel.k01b7cb9e")}</Label>
                            <span className="text-sm font-mono text-muted-foreground">
                                {(config.retrieval_threshold ?? 0).toFixed(2)}
                            </span>
                        </div>
                        <Slider value={[config.retrieval_threshold ?? 0]} onValueChange={([v]) => setConfig(prev => ({
            ...prev,
            retrieval_threshold: v
          }))} min={0} max={1} step={0.05} className="w-full" />
                        <p className="text-xs text-muted-foreground">{t("components.memory.MemoryConfigPanel.k57fafa03")}</p>
                        <p className="text-xs text-muted-foreground">
                            {config.retrieval_threshold_source === "user" ? t("components.memory.MemoryConfigPanel.ke60351be") : t("components.memory.MemoryConfigPanel.retrievalThreshold.recommendedDefault", {
              threshold: (config.recommended_retrieval_threshold ?? 0.2).toFixed(2)
            })}
                        </p>
                    </div>

                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                        <SettingToggleCard
                            title={t("components.memory.MemoryConfigPanel.k034122e3")}
                            description={t("components.memory.MemoryConfigPanel.k4346976f")}
                            checked={config.passive_injection_enabled ?? true}
                            onCheckedChange={checked => setConfig(prev => ({
                                ...prev,
                                passive_injection_enabled: checked
                            }))}
                            className="rounded-lg border p-3 bg-white"
                        />
                        <SettingToggleCard
                            title={t("components.memory.MemoryConfigPanel.k2fda3796")}
                            description={t("components.memory.MemoryConfigPanel.k77cf7080")}
                            checked={config.graph_enabled ?? true}
                            onCheckedChange={checked => setConfig(prev => ({
                                ...prev,
                                graph_enabled: checked
                            }))}
                            className="rounded-lg border p-3 bg-white"
                        />
                        <SettingToggleCard
                            title={t("components.memory.MemoryConfigPanel.k1b7acecd")}
                            description={t("components.memory.MemoryConfigPanel.kd8da426a")}
                            checked={config.fts_enabled ?? true}
                            onCheckedChange={checked => setConfig(prev => ({
                                ...prev,
                                fts_enabled: checked
                            }))}
                            className="rounded-lg border p-3 bg-white"
                        />
                        <SettingToggleCard
                            title={t("components.memory.MemoryConfigPanel.k70809028")}
                            description={t("components.memory.MemoryConfigPanel.k60bfdf7d")}
                            checked={config.extraction_enabled ?? true}
                            onCheckedChange={checked => setConfig(prev => ({
                                ...prev,
                                extraction_enabled: checked
                            }))}
                            className="rounded-lg border p-3 bg-white"
                        />
                    </div>

                    <div className="space-y-4 rounded-lg border p-4">
                        <div className="space-y-1">
                            <h3 className="text-sm font-semibold">{t("components.memory.MemoryConfigPanel.workflowMemory.title")}</h3>
                            <p className="text-xs text-muted-foreground">{t("components.memory.MemoryConfigPanel.workflowMemory.description")}</p>
                        </div>
                        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                            <SettingToggleCard
                                title={t("components.memory.MemoryConfigPanel.workflowMemory.extraction")}
                                description={t("components.memory.MemoryConfigPanel.workflowMemory.extractionDesc")}
                                checked={config.workflowMemory?.enabled ?? true}
                                onCheckedChange={checked => setConfig(prev => ({
                                    ...prev,
                                    workflowMemory: {
                                        ...(prev.workflowMemory || {}),
                                        enabled: checked
                                    }
                                }))}
                                className="rounded-lg border p-3 bg-white"
                            />
                            <SettingToggleCard
                                title={t("components.memory.MemoryConfigPanel.workflowMemory.hints")}
                                description={t("components.memory.MemoryConfigPanel.workflowMemory.hintsDesc")}
                                checked={config.workflowMemory?.hintInjectionEnabled ?? true}
                                onCheckedChange={checked => setConfig(prev => ({
                                    ...prev,
                                    workflowMemory: {
                                        ...(prev.workflowMemory || {}),
                                        hintInjectionEnabled: checked
                                    }
                                }))}
                                className="rounded-lg border p-3 bg-white"
                            />
                            <SettingToggleCard
                                title={t("components.memory.MemoryConfigPanel.workflowMemory.quarantine")}
                                description={t("components.memory.MemoryConfigPanel.workflowMemory.quarantineDesc")}
                                checked={config.workflowMemory?.quarantineOnNegativeFeedback ?? true}
                                onCheckedChange={checked => setConfig(prev => ({
                                    ...prev,
                                    workflowMemory: {
                                        ...(prev.workflowMemory || {}),
                                        quarantineOnNegativeFeedback: checked
                                    }
                                }))}
                                className="rounded-lg border p-3 bg-white"
                            />
                        </div>
                        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                            <SettingToggleCard
                                title={t("components.memory.MemoryConfigPanel.workflowMemory.progressive")}
                                description={t("components.memory.MemoryConfigPanel.workflowMemory.progressiveDesc")}
                                checked={config.workflowMemory?.progressiveHintsEnabled ?? true}
                                onCheckedChange={checked => setConfig(prev => ({
                                    ...prev,
                                    workflowMemory: {
                                        ...(prev.workflowMemory || {}),
                                        progressiveHintsEnabled: checked
                                    }
                                }))}
                                className="rounded-lg border p-3 bg-white"
                            />
                            <SettingToggleCard
                                title={t("components.memory.MemoryConfigPanel.workflowMemory.sideEffectApproval")}
                                description={t("components.memory.MemoryConfigPanel.workflowMemory.sideEffectApprovalDesc")}
                                checked={config.workflowMemory?.requireApprovalForSideEffects ?? true}
                                onCheckedChange={checked => setConfig(prev => ({
                                    ...prev,
                                    workflowMemory: {
                                        ...(prev.workflowMemory || {}),
                                        requireApprovalForSideEffects: checked
                                    }
                                }))}
                                className="rounded-lg border p-3 bg-white"
                            />
                            <div className="space-y-1.5">
                                <Label>{t("components.memory.MemoryConfigPanel.workflowMemory.maxGuides")}</Label>
                                <Input type="number" value={config.workflowMemory?.maxActiveWorkflowGuidesPerRun ?? 2} onChange={e => setConfig(prev => ({
                                ...prev,
                                workflowMemory: {
                                  ...(prev.workflowMemory || {}),
                                  maxActiveWorkflowGuidesPerRun: Number(e.target.value)
                                }
                              }))} min={0} max={10} />
                            </div>
                        </div>
                        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                            <div className="space-y-1.5">
                                <Label>{t("components.memory.MemoryConfigPanel.workflowMemory.minSuccess")}</Label>
                                <Input type="number" value={config.workflowMemory?.minSuccessCount ?? 2} onChange={e => setConfig(prev => ({
                                ...prev,
                                workflowMemory: {
                                  ...(prev.workflowMemory || {}),
                                  minSuccessCount: Number(e.target.value)
                                }
                              }))} min={1} max={10} />
                            </div>
                            <div className="space-y-1.5">
                                <Label>{t("components.memory.MemoryConfigPanel.workflowMemory.maxHints")}</Label>
                                <Input type="number" value={config.workflowMemory?.maxInjectedHints ?? 2} onChange={e => setConfig(prev => ({
                                ...prev,
                                workflowMemory: {
                                  ...(prev.workflowMemory || {}),
                                  maxInjectedHints: Number(e.target.value)
                                }
                              }))} min={0} max={5} />
                            </div>
                            <div className="space-y-1.5">
                                <Label>{t("components.memory.MemoryConfigPanel.workflowMemory.maxChars")}</Label>
                                <Input type="number" value={config.workflowMemory?.maxHintChars ?? 900} onChange={e => setConfig(prev => ({
                                ...prev,
                                workflowMemory: {
                                  ...(prev.workflowMemory || {}),
                                  maxHintChars: Number(e.target.value)
                                }
                              }))} min={240} max={2400} step={50} />
                            </div>
                        </div>
                        <SettingToggleCard
                            title={t("components.memory.MemoryConfigPanel.workflowMemory.errorfulSuccess")}
                            description={t("components.memory.MemoryConfigPanel.workflowMemory.errorfulSuccessDesc")}
                            checked={config.workflowMemory?.errorfulSuccessRequiresUserAcceptance ?? true}
                            onCheckedChange={checked => setConfig(prev => ({
                                ...prev,
                                workflowMemory: {
                                    ...(prev.workflowMemory || {}),
                                    errorfulSuccessRequiresUserAcceptance: checked
                                }
                            }))}
                            className="rounded-lg border p-3 bg-white"
                        />
                    </div>

                    <div className="space-y-3 rounded-lg border p-4">
                        <div className="space-y-1">
                            <Label>{t("components.memory.MemoryConfigPanel.k24b3561d")}</Label>
                            <p className="text-xs text-muted-foreground">
                                {t("components.memory.MemoryConfigPanel.k868ec385")}
                            </p>
                        </div>
                        <div className="flex gap-2">
                            <Input value={recallQuery} onChange={e => setRecallQuery(e.target.value)} placeholder={t("components.memory.MemoryConfigPanel.ka33b5be2")} />
                            <Button onClick={handleRecallPreview} disabled={recallPreviewLoading}>
                                {recallPreviewLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : t("components.memory.MemoryConfigPanel.ke1192063")}
                            </Button>
                        </div>
                        {recallPreviewError ? <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                                {recallPreviewError}
                            </div> : null}
                        {recallPreview ? <div className="space-y-3">
                                <div className="grid grid-cols-1 gap-2 text-xs text-muted-foreground md:grid-cols-2">
                                    <div>{t("components.memory.MemoryConfigPanel.k834a3e94")}: {(recallPreview.threshold_snapshot ?? 0).toFixed(2)}</div>
                                    <div>{t("components.memory.MemoryConfigPanel.k37780c01")}: {(recallPreview.effective_acceptance_threshold ?? 0).toFixed(2)}</div>
                                    <div>{t("components.memory.MemoryConfigPanel.k23555f99")}: {recallPreview.threshold_source === "user" ? t("components.memory.MemoryConfigPanel.k68b26cc9") : t("components.memory.MemoryConfigPanel.k99035a27")}</div>
                                    <div>{t("components.memory.MemoryConfigPanel.k501e6a4b")}: {recallPreview.diagnostics?.recall_strategy || "balanced"}</div>
                                    <div>{t("components.memory.MemoryConfigPanel.kc52ea7d5")}: {recallPreview.diagnostics?.graph_allowed ? t("components.memory.MemoryConfigPanel.k6cccddd7") : recallPreview.diagnostics?.graph_reject_reason || t("components.memory.MemoryConfigPanel.k2634a52d")}</div>
                                </div>
                                {recallPreview.memoryInjectionPack ? <div className="rounded-md border bg-muted/20 p-3 text-xs">
                                    <div className="mb-2 flex flex-wrap items-center gap-2 font-medium">
                                        <span>MemoryInjectionPack</span>
                                        <span className="rounded-full bg-background px-2 py-0.5 text-muted-foreground">{recallPreview.memoryInjectionPack.mode || "balanced"}</span>
                                        <span className="text-muted-foreground">
                                            selected {recallPreview.memoryInjectionPack.stats?.selectedCount ?? recallPreview.memoryInjectionPack.selectedMemory?.length ?? 0}
                                            {" · "}
                                            rejected {recallPreview.memoryInjectionPack.stats?.rejectedPreviewCount ?? recallPreview.memoryInjectionPack.rejectedMemory?.length ?? 0}
                                        </span>
                                    </div>
                                    <div className="max-h-48 space-y-2 overflow-auto">
                                        {(recallPreview.memoryInjectionPack.selectedMemory || []).slice(0, 4).map(item => <div key={`selected-${item.id || item.content}`} className="rounded border border-emerald-200 bg-emerald-50/40 p-2">
                                            <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                                                <span>{item.source || "memory"}</span>
                                                <span>{item.scope || "global"}</span>
                                                <span>{(item.confidence ?? 0).toFixed(3)}</span>
                                            </div>
                                            <div className="mt-1 line-clamp-2 text-foreground">{item.content || item.id}</div>
                                            <div className="mt-1 text-[11px] text-emerald-700">{item.whySelected}</div>
                                        </div>)}
                                        {(recallPreview.memoryInjectionPack.rejectedMemory || []).slice(0, 4).map(item => <div key={`rejected-${item.id || item.content}`} className="rounded border p-2">
                                            <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                                                <span>{item.source || "memory"}</span>
                                                <span>{item.scope || "global"}</span>
                                                <span>{(item.confidence ?? 0).toFixed(3)}</span>
                                            </div>
                                            <div className="mt-1 line-clamp-2">{item.content || item.id}</div>
                                            <div className="mt-1 text-[11px] text-muted-foreground">{item.doNotInjectReason}</div>
                                        </div>)}
                                        {!(recallPreview.memoryInjectionPack.selectedMemory || []).length && !(recallPreview.memoryInjectionPack.rejectedMemory || []).length ? <div className="text-muted-foreground">No memory candidates.</div> : null}
                                    </div>
                                </div> : null}
                                <div className="max-h-72 space-y-2 overflow-auto rounded-md border p-3">
                                    {(recallPreview.items || []).length ? recallPreview.items?.map(item => <div key={item.id} className="rounded-md border p-3 text-sm">
                                                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                                    <span>{item.source || "unknown"}</span>
                                                    <span>{item.scope || "global"}</span>
                                                    <span>{item.category || "general"}</span>
                                                    <span>{t("components.memory.MemoryConfigPanel.scoreRaw")} {(item.raw_relevance_score ?? 0).toFixed(4)}</span>
                                                    <span>{t("components.memory.MemoryConfigPanel.scoreFinal")} {(item.final_relevance_score ?? 0).toFixed(4)}</span>
                                                    <span>{item.accepted ? t("components.memory.MemoryConfigPanel.k05278b16") : item.reject_reason ? t("components.memory.MemoryConfigPanel.recallPreview.rejectedWithReason", {
                      reason: item.reject_reason
                    }) : t("components.memory.MemoryConfigPanel.recallPreview.rejected")}</span>
                                                </div>
                                                <p className="mt-2 whitespace-pre-wrap break-words text-sm">{item.fact}</p>
                                            </div>) : <div className="text-xs text-muted-foreground">{t("components.memory.MemoryConfigPanel.k7282aa62")}</div>}
                                </div>
                            </div> : null}
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t("components.memory.MemoryConfigPanel.k4a2f7fd5")}</CardTitle>
                <CardDescription>
                    {t("components.memory.MemoryConfigPanel.k93c31269")}
                </CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="space-y-3 rounded-lg border p-4">
                        <div className="space-y-1">
                            <Label>{tg(t, "5076490a")}</Label>
                            <p className="text-xs text-muted-foreground">
                                {tg(t, "0f7616e0")}{t(DURABLE_POLICY_PRESET_LABELS[recommendedDurablePreset] || "components.memory.MemoryConfigPanel.k0f34dd0d")}”。
                            </p>
                        </div>
                        <div className="grid gap-3 md:grid-cols-3">
                            {Object.entries(durablePresets).map(([presetKey, presetValues]) => <button key={presetKey} type="button" onClick={() => applyDurablePreset(presetKey)} className={`rounded-lg border p-3 text-left transition-colors ${currentDurablePreset === presetKey ? "border-sky-300 bg-sky-50" : "border-border hover:border-slate-300"}`}>

                                    <div className="flex items-center justify-between gap-3">
                                        <span className="text-sm font-semibold">{t(DURABLE_POLICY_PRESET_LABELS[presetKey] || presetKey)}</span>
                                        {presetKey === recommendedDurablePreset ? <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">{tg(t, "62b46f24")}</span> : null}
                                    </div>
                                    <p className="mt-2 text-xs leading-5 text-muted-foreground">
                                        {DURABLE_POLICY_PRESET_DESCRIPTIONS[presetKey] ? t(DURABLE_POLICY_PRESET_DESCRIPTIONS[presetKey]) : tg(t, "88ee9dc4")}
                                    </p>
                                    <div className="mt-2 text-[11px] leading-5 text-muted-foreground">
                                        pref {presetValues.preference_importance_threshold}/{presetValues.preference_confidence_threshold.toFixed(2)} · global {presetValues.global_knowledge_importance_threshold}/{presetValues.global_knowledge_confidence_threshold.toFixed(2)}
                                    </div>
                                </button>)}
                        </div>
                        <p className="text-xs text-muted-foreground">
                            {tg(t, "70f73798")}{currentDurablePreset === "custom" ? tg(t, "14f3ad14") : t(DURABLE_POLICY_PRESET_LABELS[currentDurablePreset] || currentDurablePreset)}
                        </p>
                    </div>
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                        <div className="space-y-4 rounded-lg border p-4">
                            <div className="space-y-1">
                                <h3 className="text-sm font-semibold">{t("components.memory.MemoryConfigPanel.k13814d19")}</h3>
                                <p className="text-xs text-muted-foreground">
                                    {t("components.memory.MemoryConfigPanel.k194d6f93")}
                                </p>
                            </div>
                            {renderImportanceSlider("preference_importance_threshold", t("components.memory.MemoryConfigPanel.k28a3a8a2"), t("components.memory.MemoryConfigPanel.kd98a6548"))}
                            {renderConfidenceSlider("preference_confidence_threshold", t("components.memory.MemoryConfigPanel.k2f476541"), t("components.memory.MemoryConfigPanel.ke96288d4"))}
                        </div>

                        <div className="space-y-4 rounded-lg border p-4">
                            <div className="space-y-1">
                                <h3 className="text-sm font-semibold">{t("components.memory.MemoryConfigPanel.k055bce93")}</h3>
                                <p className="text-xs text-muted-foreground">
                                    {t("components.memory.MemoryConfigPanel.kfff873a0")}
                                </p>
                            </div>
                            {renderImportanceSlider("knowledge_importance_threshold", t("components.memory.MemoryConfigPanel.k54bff4ae"), t("components.memory.MemoryConfigPanel.k3401ec31"))}
                            {renderConfidenceSlider("knowledge_confidence_threshold", t("components.memory.MemoryConfigPanel.k6a1e5fd9"), t("components.memory.MemoryConfigPanel.kbc8fb8f0"))}
                        </div>

                        <div className="space-y-4 rounded-lg border p-4">
                            <div className="space-y-1">
                                <h3 className="text-sm font-semibold">{t("components.memory.MemoryConfigPanel.k2bb10277")}</h3>
                                <p className="text-xs text-muted-foreground">
                                    {t("components.memory.MemoryConfigPanel.kf3bf6006")}
                                </p>
                            </div>
                            {renderImportanceSlider("global_knowledge_importance_threshold", t("components.memory.MemoryConfigPanel.kc251c0a0"), t("components.memory.MemoryConfigPanel.kf6e4c5df"))}
                            {renderConfidenceSlider("global_knowledge_confidence_threshold", t("components.memory.MemoryConfigPanel.k245dddf5"), t("components.memory.MemoryConfigPanel.k360c01ec"))}
                        </div>

                        <div className="space-y-4 rounded-lg border p-4">
                            <div className="space-y-1">
                                <h3 className="text-sm font-semibold">{t("components.memory.MemoryConfigPanel.k8eafb2f4")}</h3>
                                <p className="text-xs text-muted-foreground">
                                    {t("components.memory.MemoryConfigPanel.k507998d7")}
                                </p>
                            </div>
                            {renderImportanceSlider("global_operational_importance_threshold", t("components.memory.MemoryConfigPanel.k9ced832d"), t("components.memory.MemoryConfigPanel.kd2b2f0db"))}
                            {renderConfidenceSlider("global_operational_confidence_threshold", t("components.memory.MemoryConfigPanel.k8c7fa58e"), t("components.memory.MemoryConfigPanel.k5c76a920"))}
                        </div>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t("components.memory.MemoryConfigPanel.k20e21cd2")}</CardTitle>
                <CardDescription>
                    {t("components.memory.MemoryConfigPanel.k918c5973")}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="rounded-xl border border-border/60 bg-muted/30 px-4 py-3 text-xs leading-6 text-muted-foreground">
                        {t("components.memory.MemoryConfigPanel.kb9fe428e")}
                    </div>
                    <div className="space-y-1.5">
                        <Label>{t("components.memory.MemoryConfigPanel.k4948bac2")}</Label>
                        <Select value={config.passive_context_profile ?? "balanced"} onValueChange={value => setConfig(prev => ({
            ...prev,
            passive_context_profile: value
          }))}>
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder={t("components.memory.MemoryConfigPanel.k0bc78b0f")} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="light">{t("components.memory.MemoryConfigPanel.kf6d4fc8c")}</SelectItem>
                                <SelectItem value="balanced">{t("components.memory.MemoryConfigPanel.k0f34dd0d")}</SelectItem>
                                <SelectItem value="detailed">{t("components.memory.MemoryConfigPanel.kc837fba0")}</SelectItem>
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {config.passive_context_profile === "light" ? t("components.memory.MemoryConfigPanel.k6c54d9f2") : config.passive_context_profile === "detailed" ? t("components.memory.MemoryConfigPanel.k06f07893") : t("components.memory.MemoryConfigPanel.k4a975a64")}
                        </p>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1.5">
                            <Label>{t("components.memory.MemoryConfigPanel.kc04a35ed")}</Label>
                            <Input type="number" value={config.max_recent_days ?? 1} onChange={e => setConfig(prev => ({
              ...prev,
              max_recent_days: Number(e.target.value)
            }))} min={1} max={14} />
                        </div>
                        <div className="space-y-1.5">
                            <Label>{t("components.memory.MemoryConfigPanel.k48033213")}</Label>
                            <Input type="number" value={config.max_context_tokens ?? 2000} onChange={e => setConfig(prev => ({
              ...prev,
              max_context_tokens: Number(e.target.value)
            }))} min={500} max={8000} step={100} />
                        </div>
                    </div>
                    <div className="flex justify-end">
                        <Button type="button" variant="outline" size="sm" onClick={() => setShowAdvancedContextSettings(prev => !prev)}>
                            {showAdvancedContextSettings ? t("components.memory.MemoryConfigPanel.k3d91ba2e") : t("components.memory.MemoryConfigPanel.kb9e394ac")}
                        </Button>
                    </div>
                    {showAdvancedContextSettings ? <div className="space-y-4 rounded-lg border p-4">
                            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                                <SettingToggleCard
                                    title={t("components.memory.MemoryConfigPanel.k3d2ee76a")}
                                    description={t("components.memory.MemoryConfigPanel.k36c29574")}
                                    checked={config.passive_summary_enabled ?? true}
                                    onCheckedChange={checked => setConfig(prev => ({
                                        ...prev,
                                        passive_summary_enabled: checked
                                    }))}
                                />
                                <SettingToggleCard
                                    title={t("components.memory.MemoryConfigPanel.ka8377e16")}
                                    description={t("components.memory.MemoryConfigPanel.k1551bd08")}
                                    checked={config.passive_memory_map_enabled ?? true}
                                    onCheckedChange={checked => setConfig(prev => ({
                                        ...prev,
                                        passive_memory_map_enabled: checked
                                    }))}
                                />
                                <SettingToggleCard
                                    title={t("components.memory.MemoryConfigPanel.k924f6ae8")}
                                    description={t("components.memory.MemoryConfigPanel.k84ef5137")}
                                    checked={config.passive_recent_activity_teaser_enabled ?? true}
                                    onCheckedChange={checked => setConfig(prev => ({
                                        ...prev,
                                        passive_recent_activity_teaser_enabled: checked
                                    }))}
                                />
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-1.5">
                                    <Label>{t("components.memory.MemoryConfigPanel.k30ffc3e7")}</Label>
                                    <Input type="number" value={config.passive_recent_activity_teaser_limit ?? 2} onChange={e => setConfig(prev => ({
                ...prev,
                passive_recent_activity_teaser_limit: Number(e.target.value)
              }))} min={1} max={12} />
                                </div>
                                <div className="space-y-1.5">
                                    <Label>{t("components.memory.MemoryConfigPanel.ke1aa629a")}</Label>
                                    <Input type="number" value={config.passive_memory_map_node_limit ?? 4} onChange={e => setConfig(prev => ({
                ...prev,
                passive_memory_map_node_limit: Number(e.target.value)
              }))} min={1} max={12} />
                                </div>
                            </div>
                        </div> : null}
                </CardContent>
            </Card>

            <div className="flex justify-end">
                <Button onClick={handleSave} disabled={saving}>
                    {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                    {saved ? t("components.memory.MemoryConfigPanel.k8b7fa48e") : t("components.memory.MemoryConfigPanel.kaf9b5430")}
                </Button>
            </div>
        </div>;
}
