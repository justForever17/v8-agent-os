"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useT } from "@/components/providers/LocaleProvider";
import { ti } from "@/i18n/admin-legacy";
export type AdminModelSelectOption = {
  id?: string;
  modelRef?: string;
  providerId?: string;
  modelId?: string;
  name?: string;
  type?: string;
  capabilityClass?: string | null;
  contextWindow?: number | null;
  capabilities?: Record<string, boolean> | string[] | null;
  provider?: {
    id?: string;
    name?: string | null;
  } | null;
  providerName?: string | null;
};
const MIN_TEXT_CONTEXT_WINDOW_TOKENS = 262144;
const NON_TEXT_TYPES = new Set(["IMAGE", "VIDEO", "VOICE", "MUSIC", "MODEL3D", "WORKFLOW", "EMBEDDING", "RERANK", "VECTOR"]);
const NON_TEXT_CAPABILITY_CLASSES = new Set(["media_generation", "embedding", "reranker", "rerank", "workflow", "model3d"]);
function hasCapability(model: AdminModelSelectOption, key: string): boolean {
  const caps = model.capabilities;
  if (Array.isArray(caps)) return caps.map(item => String(item).toLowerCase()).includes(key.toLowerCase());
  if (caps && typeof caps === "object") return Boolean(caps[key]);
  return false;
}
function isTextGenerationOption(model: AdminModelSelectOption): boolean {
  const type = String(model.type || "").toUpperCase();
  if (NON_TEXT_TYPES.has(type)) return false;
  const capabilityClass = String(model.capabilityClass || "").toLowerCase();
  if (NON_TEXT_CAPABILITY_CLASSES.has(capabilityClass)) return false;
  const nonTextCaps = ["image", "video", "voice", "music", "embedding", "rerank", "workflow", "model3d"];
  const textCaps = ["chat", "text", "reasoning", "toolCalling", "vision", "multimodal"];
  if (nonTextCaps.some(key => hasCapability(model, key)) && !textCaps.some(key => hasCapability(model, key))) {
    return false;
  }
  return true;
}
function contextWindowInvalidReason(model: AdminModelSelectOption, minimum: number): string {
  if (!isTextGenerationOption(model)) return "";
  const contextWindow = typeof model.contextWindow === "number" ? model.contextWindow : null;
  if (!contextWindow) return "Context window is not configured; this model cannot be used for long-context text roles";
  if (contextWindow < minimum) return `context window ${contextWindow} < ${minimum}`;
  return "";
}
type ResolvedModelValue = {
  selectValue: string;
  status: "empty" | "exact" | "legacy_unique" | "legacy_ambiguous" | "stale";
  message: string;
};
export function modelOptionValue(model: AdminModelSelectOption): string {
  const direct = String(model.modelRef || model.id || "").trim();
  if (direct) return direct;
  const providerId = String(model.providerId || model.provider?.id || model.providerName || "").trim();
  const modelId = String(model.modelId || "").trim();
  if (providerId && modelId) return `${providerId}::${encodeURIComponent(modelId)}`;
  return modelId;
}
export function modelOptionLabel(model: AdminModelSelectOption): string {
  const modelId = String(model.modelId || model.name || model.id || "").trim();
  const providerName = String(model.provider?.name || model.providerName || model.providerId || "").trim();
  return providerName ? `${modelId} (${providerName})` : modelId;
}
export function resolveModelSelectValue(value: string | null | undefined, models: AdminModelSelectOption[], emptyValue: string): ResolvedModelValue {
  const raw = String(value || "").trim();
  if (!raw || raw === emptyValue) {
    return {
      selectValue: emptyValue,
      status: "empty",
      message: ""
    };
  }
  const exact = models.find(model => modelOptionValue(model) === raw);
  if (exact) {
    return {
      selectValue: raw,
      status: "exact",
      message: ""
    };
  }
  const legacyMatches = models.filter(model => String(model.modelId || "").trim() === raw);
  if (legacyMatches.length === 1) {
    return {
      selectValue: modelOptionValue(legacyMatches[0]),
      status: "legacy_unique",
      message: `Current config uses legacy model name ${raw}; it matched one provider. Saving again will write a provider-qualified modelRef.`
    };
  }
  if (legacyMatches.length > 1) {
    return {
      selectValue: emptyValue,
      status: "legacy_ambiguous",
      message: `Current config uses legacy model name ${raw}, but ${legacyMatches.length} models share that name. Select the provider-qualified model again.`
    };
  }
  return {
    selectValue: raw,
    status: "stale",
    message: `Configured model ${raw} is not in the model catalog. Confirm it still exists or select another model.`
  };
}
export function ModelSelect({
  models,
  value,
  onValueChange,
  placeholder,
  emptyValue = "__empty__",
  emptyLabel,
  emptyOutputValue = "",
  showCompatibilityHint = true,
  enforceTextContextWindow = true,
  minimumContextWindow = MIN_TEXT_CONTEXT_WINDOW_TOKENS,
  className
}: {
  models: AdminModelSelectOption[];
  value?: string | null;
  onValueChange: (value: string) => void;
  placeholder?: string;
  emptyValue?: string;
  emptyLabel?: string;
  emptyOutputValue?: string;
  showCompatibilityHint?: boolean;
  enforceTextContextWindow?: boolean;
  minimumContextWindow?: number;
  className?: string;
}) {
  const t = useT();
  const resolved = resolveModelSelectValue(value, models, emptyValue);
  const seen = new Set<string>();
  const resolvedPlaceholder = placeholder || ti(t, "k4e769dd289");
  const options = models.map(model => ({
    model,
    value: modelOptionValue(model),
    label: modelOptionLabel(model),
    invalidReason: enforceTextContextWindow ? contextWindowInvalidReason(model, minimumContextWindow) : ""
  })).filter(item => {
    if (!item.value || seen.has(item.value)) return false;
    seen.add(item.value);
    return true;
  });
  const hasStaleItem = resolved.status === "stale" && resolved.selectValue && !seen.has(resolved.selectValue);
  const resolvedModel = options.find(item => item.value === resolved.selectValue);
  const resolvedInvalidReason = resolvedModel?.invalidReason || "";
  return <div className={className || "space-y-2"}>
            <Select value={resolved.selectValue} onValueChange={next => onValueChange(next === emptyValue ? emptyOutputValue : next)}>

                <SelectTrigger className="w-full">
                    <SelectValue placeholder={resolvedPlaceholder} />
                </SelectTrigger>
                <SelectContent>
                    {emptyLabel ? <SelectItem value={emptyValue}>{emptyLabel}</SelectItem> : null}
                    {hasStaleItem ? <SelectItem value={resolved.selectValue} disabled>
                            {ti(t, "k5f8877b2d7")}{resolved.selectValue}
                        </SelectItem> : null}
                    {options.map(item => <SelectItem key={item.value} value={item.value} disabled={Boolean(item.invalidReason)}>
                            {item.label}
                            {item.invalidReason ? <span className="ml-2 text-xs text-amber-600">({item.invalidReason})</span> : null}
                        </SelectItem>)}
                </SelectContent>
            </Select>
            {resolvedInvalidReason ? <p className="text-xs leading-5 text-amber-700">
                    {ti(t, "ka0af8f7df5")} {resolved.selectValue} {resolvedInvalidReason}{ti(t, "k89878d272c")} {minimumContextWindow} {ti(t, "k9dea91123a")}
                </p> : null}
            {showCompatibilityHint && resolved.message ? <p className="text-xs leading-5 text-amber-700">{resolved.message}</p> : null}
        </div>;
}
