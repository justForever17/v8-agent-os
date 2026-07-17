"use client";

import Image from "next/image";
import { AlertCircle, Brain, CheckCircle2, Copy, Database, Edit2, Eye, Image as ImageIcon, ListOrdered, LoaderCircle, MessageCircle, Mic2, Music, PlugZap, Radio, Star, Trash2, Video, Volume2, Wrench, type LucideIcon } from "lucide-react";
import type { ControlPlaneModel, ModelDefaultCategory } from "@/components/models/control-plane-types";
import { useT } from "@/components/providers/LocaleProvider";
import { resolveModelIcon } from "@/lib/models/model-assets";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ik, ir } from "@/i18n/admin-legacy";
import type { TranslationKey } from "@/lib/locale";
interface ModelCardV2Props {
  model: {
    id: string;
    modelRef?: string;
    modelId: string;
    type: string;
    provider?: {
      id?: string;
      name: string;
      icon?: string | null;
      logoAsset?: string | null;
      baseUrl?: string | null;
    } | null;
    logoAsset?: string | null;
    isEnabled: boolean;
    contextWindow?: number | null;
    maxTokens?: number | null;
    pricing?: {
      inputPerMillionTokens?: number | null;
      outputPerMillionTokens?: number | null;
      source?: string | null;
    } | null;
    capabilityRegistry?: {
      canonicalModelId?: string | null;
      displayName?: string | null;
      confidence?: string | null;
      missingFields?: string[];
    } | null;
    mediaLimits?: Record<string, unknown> | null;
  };
  controlMeta?: ControlPlaneModel | null;
  isDefault?: boolean;
  onSetDefault?: (modelRef: string, categoryKey?: string) => void;
  onTestConnection?: (modelRef: string) => Promise<void> | void;
  onRepairReasoning?: (modelRef: string) => Promise<void> | void;
  onToggleNoThink?: (disabled: boolean) => Promise<void> | void;
  connectionStatus?: {
    status: "idle" | "testing" | "success" | "warning" | "error";
    message?: string;
  } | null;
  reasoningRepairStatus?: {
    status: "idle" | "repairing" | "success" | "warning" | "error";
    message?: string;
  } | null;
  onEdit: (model: ModelCardV2Props["model"]) => void;
  onDelete: (model: ModelCardV2Props["model"]) => void;
}
const ROLE_LABELS: Record<string, string> = {
  default: "components.models.ModelCardV2.k868c5c5c",
  supervisor: "components.models.ModelCardV2.kf45c6152",
  summary: "components.models.ModelCardV2.k10c3fca6",
  extraction: "components.models.ModelCardV2.k97945aa9",
  vision: "components.models.ModelCardV2.k8e018918",
  embedding: "components.models.ModelCardV2.kc1798b61",
  reranker: "components.models.ModelCardV2.k81ac6b74",
  channel: "components.models.ModelCardV2.k28e95b3a",
  automation: "components.models.ModelCardV2.k890adc88",
  computer_use_planner: "components.models.ModelCardV2.k5cee5c2b",
  computer_use_visual_judge: "components.models.ModelCardV2.ke3c7666e",
  rpa_discovery: "components.models.ModelCardV2.k526071af"
};
const DEFAULT_CATEGORY_LABEL_KEYS: Record<string, TranslationKey> = {
  text_generation: "components.models.ModelCardV2.defaultCategory.text_generation",
  vision_multimodal: "components.models.ModelCardV2.defaultCategory.vision_multimodal",
  embedding: "components.models.ModelCardV2.defaultCategory.embedding",
  reranker: "components.models.ModelCardV2.defaultCategory.reranker"
};
const DEFAULT_CATEGORY_BADGE_CLASSES: Record<string, string> = {
  sky: "bg-sky-50 text-sky-700 ring-sky-200 hover:bg-sky-100",
  violet: "bg-violet-50 text-violet-700 ring-violet-200 hover:bg-violet-100",
  emerald: "bg-emerald-50 text-emerald-700 ring-emerald-200 hover:bg-emerald-100",
  amber: "bg-amber-50 text-amber-700 ring-amber-200 hover:bg-amber-100"
};
const DEFAULT_CATEGORY_STAR_CLASSES: Record<string, string> = {
  sky: "fill-sky-500 text-sky-500",
  violet: "fill-violet-500 text-violet-500",
  emerald: "fill-emerald-500 text-emerald-500",
  amber: "fill-amber-500 text-amber-500"
};
type CapabilityIconItem = {
  key: string;
  labelKey: TranslationKey;
  Icon: LucideIcon;
};
type RoleDoctorFinding = {
  code?: string;
  message?: string;
};
type RoleDoctorState = {
  issues?: RoleDoctorFinding[];
  warnings?: RoleDoctorFinding[];
};
type ControlPlaneModelWithDoctor = ControlPlaneModel & {
  roleDoctor?: RoleDoctorState | null;
};
type ModelWithDoctor = ModelCardV2Props["model"] & {
  roleDoctor?: RoleDoctorState | null;
};
type DefaultCategoryOption = {
  key: string;
  labelKey: TranslationKey;
};
function stringRecordValue(record: Record<string, unknown> | null | undefined, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value.trim() : "";
}
function resolveVisibleModelRoute(model: ModelCardV2Props["model"], controlMeta?: ControlPlaneModel | null) {
  const mediaLimits = {
    ...(model.mediaLimits || {}),
    ...(controlMeta?.mediaLimits || {}),
  };
  const displayModelId = stringRecordValue(mediaLimits, "displayModelId") || model.modelId;
  const providerModelId = stringRecordValue(mediaLimits, "providerModelId");
  const hasExplicitRoute = Boolean(providerModelId && displayModelId !== providerModelId);
  return {
    displayModelId,
    providerModelId,
    requestSuffix: hasExplicitRoute ? `/${displayModelId.replace(/^\/+/, "")}` : "",
    submitPath: stringRecordValue(mediaLimits, "submitPath"),
  };
}
function modelCategoryShape(modelType: string, controlMeta?: ControlPlaneModel | null) {
  const normalizedType = String(modelType || controlMeta?.type || "").toUpperCase();
  const capabilityClass = String(controlMeta?.capabilityClass || "").toLowerCase();
  const capabilities = controlMeta?.capabilities || null;
  return { normalizedType, capabilityClass, capabilities };
}
function isMediaOnlyModel(modelType: string, controlMeta?: ControlPlaneModel | null): boolean {
  const { normalizedType, capabilityClass, capabilities } = modelCategoryShape(modelType, controlMeta);
  const isMediaType = new Set(["MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"]).has(normalizedType);
  const mediaCapability = Boolean(
    capabilities?.image ||
    capabilities?.video ||
    capabilities?.audio ||
    capabilities?.voice ||
    capabilities?.music ||
    capabilities?.workflow ||
    capabilities?.model3d
  );
  return capabilityClass === "media_generation" || isMediaType || (mediaCapability && !capabilities?.chat && !capabilities?.vision && !capabilities?.multimodal);
}
function buildDefaultCategoryOptions(
  modelType: string,
  controlMeta?: ControlPlaneModel | null,
  assignedRoles: string[] = [],
): DefaultCategoryOption[] {
  const { normalizedType, capabilityClass, capabilities } = modelCategoryShape(modelType, controlMeta);
  const options: DefaultCategoryOption[] = [];
  const add = (key: string) => {
    const labelKey = DEFAULT_CATEGORY_LABEL_KEYS[key];
    if (labelKey && !options.some((option) => option.key === key)) {
      options.push({ key, labelKey });
    }
  };
  if (capabilityClass === "embedding" || normalizedType === "EMBEDDING" || capabilities?.embedding) {
    add("embedding");
    return options;
  }
  if (capabilityClass === "reranker" || capabilityClass === "rerank" || normalizedType === "RERANK" || normalizedType === "RERANKER" || capabilities?.rerank) {
    add("reranker");
    return options;
  }
  if (isMediaOnlyModel(modelType, controlMeta)) {
    return options;
  }
  const canUseAsGeneral = Boolean(
    !controlMeta ||
    capabilities?.chat ||
    capabilities?.reasoning ||
    capabilities?.toolCalling ||
    capabilities?.streaming ||
    capabilities?.vision ||
    capabilities?.multimodal ||
    ["TEXT", "CHAT", "VISION", "MULTIMODAL"].includes(normalizedType) ||
    ["chat_general", "chat_tool_calling", "chat_reasoning", "vision_multimodal"].includes(capabilityClass)
  );
  const canUseAsVision = Boolean(
    capabilities?.vision ||
    capabilities?.multimodal ||
    normalizedType === "VISION" ||
    normalizedType === "MULTIMODAL" ||
    capabilityClass === "vision_multimodal" ||
    assignedRoles.includes("vision")
  );
  if (canUseAsGeneral) {
    add("text_generation");
  }
  if (canUseAsVision) {
    add("vision_multimodal");
  }
  return options;
}
function defaultCategoryLabel(category: Pick<ModelDefaultCategory, "key" | "label">, t: (key: TranslationKey, params?: Record<string, string | number>) => string): string {
  const labelKey = DEFAULT_CATEGORY_LABEL_KEYS[String(category.key || "")];
  return labelKey ? t(labelKey) : category.label || String(category.key || "");
}
function buildCapabilityIconItems(modelType: string, capabilityTags: string[], capabilities?: ControlPlaneModel["capabilities"] | null): CapabilityIconItem[] {
  const source = `${modelType} ${capabilityTags.join(" ")}`.toLowerCase();
  const has = (...needles: string[]) => needles.some(needle => source.includes(needle.toLowerCase()));
  const cap = (key: keyof ControlPlaneModel["capabilities"]) => Boolean(capabilities?.[key]);
  const normalizedType = String(modelType || "").toUpperCase();
  const mediaType = new Set(["MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"]).has(normalizedType);
  const items: CapabilityIconItem[] = [];
  const add = (key: string, labelKey: TranslationKey, Icon: LucideIcon) => {
    if (!items.some(item => item.key === key)) items.push({
      key,
      labelKey,
      Icon
    });
  };
  if (cap("chat") || has("chat", "text", ir("k00d4cbafea"), ir("kf1926e9b33")) || !capabilityTags.length && !mediaType) add("chat", ik("k06ebe4c255"), MessageCircle);
  if (cap("toolCalling") || has("tool", "function", ir("ka72ef18d9a"))) add("tools", ik("k8adb5022e0"), Wrench);
  if (cap("streaming") || has("stream", ir("ke4d8c16cd2"))) add("streaming", ik("kf8dcd16381"), Radio);
  if (cap("vision") || has("vision", ir("k6e81cc46a2"))) add("vision", ik("kc92bedaae6"), Eye);
  if (cap("multimodal") || has("multimodal", ir("ka32fe198ed"))) add("multimodal", ik("k25e957df46"), Eye);
  if (cap("image") || has("image", ir("kbe8da62ea1"), ir("k0a0ce84dde"))) add("image", ik("k9cdef12b3b"), ImageIcon);
  if (cap("video") || has("video", ir("kfa4e33b698"))) add("video", ik("k0ce3738b06"), Video);
  if (cap("voice") || has("voice", ir("k7a73e125c1"), "tts", "speech")) add("voice", ik("k29b7ed2939"), Mic2);
  if (cap("music") || has("music", "song", ir("kafb3c40c39"))) add("music", ik("k9f5bd01411"), Music);
  if ((cap("audio") || has("audio", ir("k461189f186"))) && !items.some(item => item.key === "voice" || item.key === "music")) add("audio", ik("k958adb2f96"), Volume2);
  if (cap("embedding") || has("embedding", "vector", ir("kfae158475e"))) add("embedding", ik("k57b695c73d"), Database);
  if (cap("rerank") || has("rerank", ir("k675b0ee116"))) add("rerank", ik("k8864544e99"), ListOrdered);
  if (cap("reasoning") || has("reasoning", ir("kc9d3b085e2"))) add("reasoning", ik("kcc24fe7a77"), Brain);
  return items;
}
export function ModelCardV2({
  model,
  controlMeta,
  isDefault,
  onSetDefault,
  onTestConnection,
  onRepairReasoning,
  onToggleNoThink,
  connectionStatus,
  reasoningRepairStatus,
  onEdit,
  onDelete
}: ModelCardV2Props) {
  const t = useT();
  const capabilityTags = controlMeta?.capabilityTags || [];
  const assignedRoles = controlMeta?.assignedRoles || [];
  const providerMark = (model.provider?.name || model.modelId || "M").trim().charAt(0).toUpperCase();
  const currentStatus = connectionStatus?.status || "idle";
  const repairStatus = reasoningRepairStatus?.status || "idle";
  const repairActive = repairStatus !== "idle";
  const displayStatus = repairActive ? (repairStatus === "repairing" ? "testing" : repairStatus) : currentStatus;
  const testing = currentStatus === "testing";
  const repairing = repairStatus === "repairing";
  const statusMessage = (repairActive ? reasoningRepairStatus?.message : connectionStatus?.message) || "";
  const modelRef = model.modelRef || model.id || model.modelId;
  const canRepairReasoning = Boolean(controlMeta?.capabilities?.chat || String(model.type || "").toUpperCase() === "TEXT");
  const capabilityIconItems = buildCapabilityIconItems(model.type, capabilityTags, controlMeta?.capabilities);
  const pricing = controlMeta?.pricing || model.pricing || null;
  const registry = controlMeta?.capabilityRegistry || model.capabilityRegistry || null;
  const reasoningSurface = controlMeta?.reasoningSurface || null;
  const thinkingControl = controlMeta?.thinkingControl || null;
  const supportsNoThink = Boolean(thinkingControl?.supportsNoThink);
  const noThinkDisabled = Boolean(thinkingControl?.disabled);
  const visibleRoute = resolveVisibleModelRoute(model, controlMeta);
  const missingFields = registry?.missingFields || [];
  const roleDoctor = (controlMeta as ControlPlaneModelWithDoctor | null | undefined)?.roleDoctor || (model as ModelWithDoctor).roleDoctor || null;
  const roleDoctorIssues = Array.isArray(roleDoctor?.issues) ? roleDoctor.issues : [];
  const roleDoctorWarnings = Array.isArray(roleDoctor?.warnings) ? roleDoctor.warnings : [];
  const explicitDefaultCategories = controlMeta?.defaultCategories || [];
  const defaultBadges: ModelDefaultCategory[] = explicitDefaultCategories.length
    ? explicitDefaultCategories
    : isDefault
      ? [{
        key: "text_generation",
        label: t(DEFAULT_CATEGORY_LABEL_KEYS.text_generation),
        role: "default",
        badge: "sky"
      }]
      : [];
  const defaultCategoryKeys = new Set(defaultBadges.map((category) => String(category.key || "")));
  if (isDefault) {
    defaultCategoryKeys.add("text_generation");
  }
  const defaultCategoryOptions = buildDefaultCategoryOptions(model.type, controlMeta, assignedRoles)
    .filter((option) => !defaultCategoryKeys.has(option.key));
  const modelIcon = resolveModelIcon({
    modelId: model.modelId,
    providerId: model.provider?.id,
    providerName: model.provider?.name,
    explicitAsset: model.logoAsset || null
  });
  const details = [`ID: ${model.modelId}`, visibleRoute.requestSuffix ? `Request suffix: ${visibleRoute.requestSuffix}` : "", visibleRoute.providerModelId ? `Provider model ID: ${visibleRoute.providerModelId}` : "", visibleRoute.submitPath ? `Catalog submit path: ${visibleRoute.submitPath}` : "", `Ref: ${modelRef}`, `Provider: ${model.provider?.name || "unknown"}`, model.provider?.baseUrl ? `Base URL: ${model.provider.baseUrl}` : "", `Type: ${model.type}`, typeof model.contextWindow === "number" ? `Context: ${model.contextWindow}` : "", typeof model.maxTokens === "number" ? `Max output: ${model.maxTokens}` : "", controlMeta?.capabilitySource ? `Capability source: ${controlMeta.capabilitySource}` : "", reasoningSurface ? `Reasoning surface: ${reasoningSurface.mode || "unknown"} / ${reasoningSurface.displayKind || "hidden"} / ${reasoningSurface.trust || "unknown"}` : "", supportsNoThink ? `No-think control: ${noThinkDisabled ? "disabled reasoning on request" : "model default thinking"}` : "", registry?.canonicalModelId ? `Capability registry: ${registry.canonicalModelId} (${registry.confidence || "unknown"})` : "", pricing && (typeof pricing.inputPerMillionTokens === "number" || typeof pricing.outputPerMillionTokens === "number") ? `Price est.: $${pricing.inputPerMillionTokens ?? "?"} in / $${pricing.outputPerMillionTokens ?? "?"} out per 1M` : "", missingFields.length ? `Missing: ${missingFields.join(", ")}` : "", controlMeta?.parameterProfile ? `Parameter profile: ${controlMeta.parameterProfile}` : "", roleDoctorIssues.length ? `Role Doctor issues: ${roleDoctorIssues.map((item: RoleDoctorFinding) => item.code || item.message).join(", ")}` : "", roleDoctorWarnings.length ? `Role Doctor warnings: ${roleDoctorWarnings.map((item: RoleDoctorFinding) => item.code || item.message).join(", ")}` : "", capabilityTags.length ? `Capabilities: ${capabilityTags.join(", ")}` : "", assignedRoles.length ? `Roles: ${assignedRoles.map(role => ROLE_LABELS[role] ? t(ROLE_LABELS[role]) : role).join(", ")}` : "Roles: none", statusMessage ? `Status: ${statusMessage}` : ""].filter(Boolean);
  return <Card className={`group/card relative h-[128px] overflow-visible transition-colors ${defaultBadges.length ? "border-primary shadow-sm" : "hover:border-primary/50"}`}>
            <CardContent className="flex h-full flex-col p-3">
                <div className="flex min-w-0 items-start gap-2">
                    <AdminHoverInfo lines={details} triggerClassName="h-7 w-7 shrink-0 justify-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-muted dark:text-muted-foreground">

                        {modelIcon ? <Image src={modelIcon} alt="" width={20} height={20} className="h-5 w-5 rounded object-contain" unoptimized /> : model.provider?.icon || providerMark}
                    </AdminHoverInfo>
                    <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-1.5">
                            <span className="truncate text-sm font-semibold leading-5" title={`${visibleRoute.requestSuffix || model.modelId} · ${model.provider?.name || ""}`}>
                                {visibleRoute.requestSuffix || model.modelId}
                                <span className="font-normal text-muted-foreground"> · {model.provider?.name || t("components.models.ModelCardV2.k4f162e67")}</span>
                            </span>
                            {defaultBadges.map((category) => {
              const badgeTone = String(category.badge || "sky");
              const badgeClass = DEFAULT_CATEGORY_BADGE_CLASSES[badgeTone] || DEFAULT_CATEGORY_BADGE_CLASSES.sky;
              const starClass = DEFAULT_CATEGORY_STAR_CLASSES[badgeTone] || DEFAULT_CATEGORY_STAR_CLASSES.sky;
              return <Badge key={`${modelRef}:${category.key}`} className={`h-5 shrink-0 border-none px-1.5 text-[10px] ring-1 ${badgeClass}`}>
                                    <Star className={`mr-1 h-3 w-3 ${starClass}`} />
                                    {defaultCategoryLabel(category, t)}
                                </Badge>;
            })}
                        </div>
                        <div className="mt-1 flex min-w-0 items-center gap-1 overflow-hidden whitespace-nowrap text-muted-foreground">
                            {visibleRoute.providerModelId ? (
                                <span className="mr-1 truncate font-mono text-[10px]" title={t("components.models.ModelCardV2.providerModelId", { modelId: visibleRoute.providerModelId })}>
                                    {t("components.models.ModelCardV2.providerModelId", { modelId: visibleRoute.providerModelId })} · {t("components.models.ModelCardV2.routeFromCatalog")}
                                </span>
                            ) : null}
                            {capabilityIconItems.map(({
              key,
              labelKey,
              Icon
            }) => <span key={`${modelRef}:${key}`} title={t(labelKey)} className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-600 dark:bg-muted dark:text-muted-foreground">
                                    <Icon className="h-2.5 w-2.5" />
                                </span>)}
                        </div>
                        <div className="mt-2 h-5">
                            {displayStatus !== "idle" ? <div className={`inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-[10px] ${displayStatus === "success" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-200" : displayStatus === "warning" ? "bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200" : displayStatus === "error" ? "bg-red-50 text-red-700 dark:bg-red-500/15 dark:text-red-200" : "bg-slate-100 text-slate-600 dark:bg-muted dark:text-muted-foreground"}`} title={statusMessage}>

                                    {displayStatus === "success" ? <CheckCircle2 className="h-3 w-3 shrink-0" /> : displayStatus === "error" || displayStatus === "warning" ? <AlertCircle className="h-3 w-3 shrink-0" /> : <LoaderCircle className="h-3 w-3 shrink-0 animate-spin" />}
                                    <span className="truncate">
                                        {repairActive ? repairStatus === "success" ? t("components.models.ModelCardV2.reasoningRepairSuccess") : repairStatus === "warning" ? t("components.models.ModelCardV2.reasoningRepairNoField") : repairStatus === "error" ? t("components.models.ModelCardV2.reasoningRepairFailed") : t("components.models.ModelCardV2.reasoningRepairing") : displayStatus === "success" ? t("components.models.ModelCardV2.k40bd808e") : displayStatus === "warning" ? t("components.models.ModelCardV2.connectionWarning") : displayStatus === "error" ? t("components.models.ModelCardV2.k7f8e6bd9") : t("components.models.ModelCardV2.kc9e37984")}
                                    </span>
                                </div> : null}
                        </div>
                    </div>
                </div>

                <div className="mt-auto flex items-end justify-between gap-2">
                    <div className="min-w-0" />

                    <div className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover/card:opacity-100">
                        {onTestConnection && <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-primary" disabled={testing} onClick={async () => {
            await onTestConnection(modelRef);
          }} title={testing ? t("components.models.ModelCardV2.k60eba059") : t("components.models.ModelCardV2.kdf48b898")}>

                                <PlugZap className={`h-3.5 w-3.5 ${testing ? "animate-pulse" : ""}`} />
                            </Button>}
                        {onRepairReasoning && canRepairReasoning && <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-primary" disabled={testing || repairing} onClick={async () => {
            await onRepairReasoning(modelRef);
          }} title={repairing ? t("components.models.ModelCardV2.reasoningRepairing") : t("components.models.ModelCardV2.reasoningRepairTitle")}>

                                <Wrench className={`h-3.5 w-3.5 ${repairing ? "animate-pulse" : ""}`} />
                            </Button>}
                        {supportsNoThink && onToggleNoThink && <Button variant="ghost" size="icon" className={`h-7 w-7 ${noThinkDisabled ? "bg-sky-50 text-sky-700 hover:bg-sky-100 hover:text-sky-800" : "text-muted-foreground hover:text-primary"}`} onClick={async () => {
            await onToggleNoThink(!noThinkDisabled);
          }} title={noThinkDisabled ? t("components.models.ModelCardV2.thinkingDefaultRestoreTitle") : t("components.models.ModelCardV2.thinkingDisableTitle")}>
                                <Brain className="h-3.5 w-3.5" />
                            </Button>}
                        {defaultCategoryOptions.length > 0 && onSetDefault && <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button variant="ghost" size="icon" className="h-7 w-7 cursor-pointer text-muted-foreground hover:text-primary focus-visible:ring-2 focus-visible:ring-primary" title={defaultCategoryOptions.map((option) => t(option.labelKey)).join(" / ")}>
                                        <Star className="h-3.5 w-3.5" aria-hidden="true" />
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end" className="w-36">
                                    {defaultCategoryOptions.map((option) => <DropdownMenuItem key={`${modelRef}:${option.key}`} className="cursor-pointer" onClick={() => onSetDefault(modelRef, option.key)}>
                                            {t("components.models.ModelCardV2.setCategoryDefault", {
                                                category: t(option.labelKey),
                                            })}
                                        </DropdownMenuItem>)}
                                </DropdownMenuContent>
                            </DropdownMenu>}
                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => navigator.clipboard.writeText(model.modelId)} title={t("components.models.ModelCardV2.ke0b2f296")}>
                            <Copy className="h-3.5 w-3.5" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => onEdit(model)} title={t("components.models.ModelCardV2.k75997619")}>
                            <Edit2 className="h-3.5 w-3.5" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => onDelete(model)} title={t("components.models.ModelCardV2.k626f35dc")}>
                            <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                    </div>
                </div>
            </CardContent>
        </Card>;
}
