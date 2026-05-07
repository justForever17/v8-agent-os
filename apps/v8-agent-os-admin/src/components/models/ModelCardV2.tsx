"use client";

import { AlertCircle, Brain, CheckCircle2, Copy, Database, Edit2, Eye, Image as ImageIcon, ListOrdered, LoaderCircle, MessageCircle, Mic2, Music, PlugZap, Radio, Star, Trash2, Video, Volume2, Wrench, type LucideIcon } from "lucide-react";
import type { ControlPlaneModel } from "@/components/models/control-plane-types";
import { useT } from "@/components/providers/LocaleProvider";
import { resolveModelIcon } from "@/lib/models/model-assets";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
  };
  controlMeta?: ControlPlaneModel | null;
  isDefault?: boolean;
  onSetDefault?: (modelRef: string) => void;
  onTestConnection?: (modelRef: string) => Promise<void> | void;
  connectionStatus?: {
    status: "idle" | "testing" | "success" | "error";
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
  connectionStatus,
  onEdit,
  onDelete
}: ModelCardV2Props) {
  const t = useT();
  const capabilityTags = controlMeta?.capabilityTags || [];
  const assignedRoles = controlMeta?.assignedRoles || [];
  const providerMark = (model.provider?.name || model.modelId || "M").trim().charAt(0).toUpperCase();
  const currentStatus = connectionStatus?.status || "idle";
  const testing = currentStatus === "testing";
  const statusMessage = connectionStatus?.message || "";
  const modelRef = model.modelRef || model.id || model.modelId;
  const capabilityIconItems = buildCapabilityIconItems(model.type, capabilityTags, controlMeta?.capabilities);
  const pricing = controlMeta?.pricing || model.pricing || null;
  const registry = controlMeta?.capabilityRegistry || model.capabilityRegistry || null;
  const reasoningSurface = controlMeta?.reasoningSurface || null;
  const missingFields = registry?.missingFields || [];
  const roleDoctor = (controlMeta as ControlPlaneModelWithDoctor | null | undefined)?.roleDoctor || (model as ModelWithDoctor).roleDoctor || null;
  const roleDoctorIssues = Array.isArray(roleDoctor?.issues) ? roleDoctor.issues : [];
  const roleDoctorWarnings = Array.isArray(roleDoctor?.warnings) ? roleDoctor.warnings : [];
  const modelIcon = resolveModelIcon({
    modelId: model.modelId,
    providerId: model.provider?.id,
    providerName: model.provider?.name,
    explicitAsset: model.logoAsset || null
  });
  const details = [`ID: ${model.modelId}`, `Ref: ${modelRef}`, `Provider: ${model.provider?.name || "unknown"}`, `Type: ${model.type}`, typeof model.contextWindow === "number" ? `Context: ${model.contextWindow}` : "", typeof model.maxTokens === "number" ? `Max output: ${model.maxTokens}` : "", controlMeta?.capabilitySource ? `Capability source: ${controlMeta.capabilitySource}` : "", reasoningSurface ? `Reasoning surface: ${reasoningSurface.mode || "unknown"} / ${reasoningSurface.displayKind || "hidden"} / ${reasoningSurface.trust || "unknown"}` : "", registry?.canonicalModelId ? `Capability registry: ${registry.canonicalModelId} (${registry.confidence || "unknown"})` : "", pricing && (typeof pricing.inputPerMillionTokens === "number" || typeof pricing.outputPerMillionTokens === "number") ? `Price est.: $${pricing.inputPerMillionTokens ?? "?"} in / $${pricing.outputPerMillionTokens ?? "?"} out per 1M` : "", missingFields.length ? `Missing: ${missingFields.join(", ")}` : "", controlMeta?.parameterProfile ? `Parameter profile: ${controlMeta.parameterProfile}` : "", roleDoctorIssues.length ? `Role Doctor issues: ${roleDoctorIssues.map((item: RoleDoctorFinding) => item.code || item.message).join(", ")}` : "", roleDoctorWarnings.length ? `Role Doctor warnings: ${roleDoctorWarnings.map((item: RoleDoctorFinding) => item.code || item.message).join(", ")}` : "", capabilityTags.length ? `Capabilities: ${capabilityTags.join(", ")}` : "", assignedRoles.length ? `Roles: ${assignedRoles.map(role => ROLE_LABELS[role] ? t(ROLE_LABELS[role]) : role).join(", ")}` : "Roles: none", statusMessage ? `Status: ${statusMessage}` : ""].filter(Boolean);
  return <Card className={`group/card relative h-[128px] overflow-visible transition-colors ${isDefault ? "border-primary shadow-sm" : "hover:border-primary/50"}`}>
            <CardContent className="flex h-full flex-col p-3">
                <div className="flex min-w-0 items-start gap-2">
                    <AdminHoverInfo lines={details} triggerClassName="h-7 w-7 shrink-0 justify-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-600">

                        {modelIcon ? <img src={modelIcon} alt="" className="h-5 w-5 rounded object-contain" /> : model.provider?.icon || providerMark}
                    </AdminHoverInfo>
                    <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-1.5">
                            <span className="truncate text-sm font-semibold leading-5" title={`${model.modelId} · ${model.provider?.name || ""}`}>
                                {model.modelId}
                                <span className="font-normal text-muted-foreground"> · {model.provider?.name || t("components.models.ModelCardV2.k4f162e67")}</span>
                            </span>
                            {isDefault && <Badge className="h-5 shrink-0 border-none bg-primary/20 px-1.5 text-[10px] text-primary hover:bg-primary/30">
                                    <Star className="mr-1 h-3 w-3 fill-primary" />
                                    {t("components.models.ModelCardV2.k6509c658")}
                                </Badge>}
                        </div>
                        <div className="mt-1 flex min-w-0 items-center gap-1 overflow-hidden whitespace-nowrap text-muted-foreground">
                            {capabilityIconItems.map(({
              key,
              labelKey,
              Icon
            }) => <span key={`${modelRef}:${key}`} title={t(labelKey)} className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-600">
                                    <Icon className="h-2.5 w-2.5" />
                                </span>)}
                        </div>
                        <div className="mt-2 h-5">
                            {currentStatus !== "idle" ? <div className={`inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-[10px] ${currentStatus === "success" ? "bg-emerald-50 text-emerald-700" : currentStatus === "error" ? "bg-red-50 text-red-700" : "bg-slate-100 text-slate-600"}`} title={statusMessage}>

                                    {currentStatus === "success" ? <CheckCircle2 className="h-3 w-3 shrink-0" /> : currentStatus === "error" ? <AlertCircle className="h-3 w-3 shrink-0" /> : <LoaderCircle className="h-3 w-3 shrink-0 animate-spin" />}
                                    <span className="truncate">
                                        {currentStatus === "success" ? t("components.models.ModelCardV2.k40bd808e") : currentStatus === "error" ? t("components.models.ModelCardV2.k7f8e6bd9") : t("components.models.ModelCardV2.kc9e37984")}
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
                        {!isDefault && onSetDefault && <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-primary" onClick={() => onSetDefault(modelRef)} title={t("components.models.ModelCardV2.ka96c553d")}>
                                <Star className="h-3.5 w-3.5" />
                            </Button>}
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
