"use client";

import {
    AlertCircle,
    Brain,
    CheckCircle2,
    Copy,
    Database,
    Edit2,
    Eye,
    Image as ImageIcon,
    ListOrdered,
    LoaderCircle,
    MessageCircle,
    PlugZap,
    Radio,
    Star,
    Trash2,
    Video,
    Volume2,
    Wrench,
    type LucideIcon,
} from "lucide-react";

import type { ControlPlaneModel } from "@/components/models/control-plane-types";
import { useT } from "@/components/providers/LocaleProvider";
import { resolveModelIcon } from "@/lib/models/model-assets";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

interface ModelCardV2Props {
    model: {
        id: string;
        modelRef?: string;
        modelId: string;
        type: string;
        provider?: { id?: string; name: string; icon?: string | null; logoAsset?: string | null } | null;
        isEnabled: boolean;
        contextWindow?: number | null;
        maxTokens?: number | null;
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
    rpa_discovery: "components.models.ModelCardV2.k526071af",
};

type CapabilityIconItem = {
    key: string;
    label: string;
    Icon: LucideIcon;
};

function buildCapabilityIconItems(modelType: string, capabilityTags: string[], capabilities?: ControlPlaneModel["capabilities"] | null): CapabilityIconItem[] {
    const source = `${modelType} ${capabilityTags.join(" ")}`.toLowerCase();
    const has = (...needles: string[]) => needles.some((needle) => source.includes(needle.toLowerCase()));
    const cap = (key: keyof ControlPlaneModel["capabilities"]) => Boolean(capabilities?.[key]);
    const items: CapabilityIconItem[] = [];
    const add = (key: string, label: string, Icon: LucideIcon) => {
        if (!items.some((item) => item.key === key)) items.push({ key, label, Icon });
    };

    if (cap("chat") || has("chat", "text", "对话", "文本") || !capabilityTags.length) add("chat", "Chat / 对话", MessageCircle);
    if (cap("toolCalling") || has("tool", "function", "工具")) add("tools", "Tools / 工具", Wrench);
    if (cap("streaming") || has("stream", "流式")) add("streaming", "Streaming / 流式", Radio);
    if (cap("vision") || has("vision", "视觉")) add("vision", "Vision / 视觉", Eye);
    if (cap("multimodal") || has("multimodal", "多模态")) add("multimodal", "Multimodal / 多模态", Eye);
    if (cap("image") || has("image", "图片", "图像")) add("image", "Image / 图片", ImageIcon);
    if (cap("video") || has("video", "视频")) add("video", "Video / 视频", Video);
    if (cap("audio") || has("audio", "音频", "voice", "语音")) add("audio", "Audio / 音频", Volume2);
    if (cap("embedding") || has("embedding", "vector", "向量")) add("embedding", "Embedding / 向量", Database);
    if (cap("rerank") || has("rerank", "重排")) add("rerank", "Rerank / 重排", ListOrdered);
    if (cap("reasoning") || has("reasoning", "推理")) add("reasoning", "Reasoning / 推理", Brain);

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
    onDelete,
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
    const modelIcon = resolveModelIcon({
        modelId: model.modelId,
        providerId: model.provider?.id,
        providerName: model.provider?.name,
        explicitAsset: null,
    });
    const details = [
        `ID: ${model.modelId}`,
        `Ref: ${modelRef}`,
        `Provider: ${model.provider?.name || "unknown"}`,
        `Type: ${model.type}`,
        typeof model.contextWindow === "number" ? `Context: ${model.contextWindow}` : "",
        typeof model.maxTokens === "number" ? `Max output: ${model.maxTokens}` : "",
        controlMeta?.capabilitySource ? `Capability source: ${controlMeta.capabilitySource}` : "",
        controlMeta?.parameterProfile ? `Parameter profile: ${controlMeta.parameterProfile}` : "",
        capabilityTags.length ? `Capabilities: ${capabilityTags.join(", ")}` : "",
        assignedRoles.length ? `Roles: ${assignedRoles.map((role) => ROLE_LABELS[role] ? t(ROLE_LABELS[role]) : role).join(", ")}` : "Roles: none",
        statusMessage ? `Status: ${statusMessage}` : "",
    ].filter(Boolean);

    return (
        <Card className={`group/card relative h-[128px] overflow-visible transition-colors ${isDefault ? "border-primary shadow-sm" : "hover:border-primary/50"}`}>
            <CardContent className="flex h-full flex-col p-3">
                <div className="flex min-w-0 items-start gap-2">
                    <div className="group/info relative flex h-7 w-7 shrink-0 items-center justify-center overflow-visible rounded-lg bg-slate-100 text-xs font-semibold text-slate-600">
                        {modelIcon ? (
                            <img src={modelIcon} alt="" className="h-5 w-5 rounded object-contain" />
                        ) : (
                            model.provider?.icon || providerMark
                        )}
                        <div className="pointer-events-none absolute left-0 top-9 z-50 w-80 rounded-xl bg-slate-950 p-3 text-left text-[11px] font-normal leading-5 text-white opacity-0 shadow-2xl transition-opacity group-hover/info:opacity-100">
                            {details.map((item) => (
                                <div key={item} className="truncate">{item}</div>
                            ))}
                        </div>
                    </div>
                    <div className="min-w-0 flex-1">
                        <div className="flex min-w-0 items-center gap-1.5">
                            <span className="truncate text-sm font-semibold leading-5" title={`${model.modelId} · ${model.provider?.name || ""}`}>
                                {model.modelId}
                                <span className="font-normal text-muted-foreground"> · {model.provider?.name || t("components.models.ModelCardV2.k4f162e67")}</span>
                            </span>
                            {isDefault && (
                                <Badge className="h-5 shrink-0 border-none bg-primary/20 px-1.5 text-[10px] text-primary hover:bg-primary/30">
                                    <Star className="mr-1 h-3 w-3 fill-primary" />
                                    {t("components.models.ModelCardV2.k6509c658")}
                                </Badge>
                            )}
                        </div>
                        <div className="mt-1 flex min-w-0 items-center gap-1 overflow-hidden whitespace-nowrap text-muted-foreground">
                            {capabilityIconItems.map(({ key, label, Icon }) => (
                                <span key={`${modelRef}:${key}`} title={label} className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-600">
                                    <Icon className="h-2.5 w-2.5" />
                                </span>
                            ))}
                        </div>
                        <div className="mt-2 h-5">
                            {currentStatus !== "idle" ? (
                                <div
                                    className={`inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-[10px] ${
                                        currentStatus === "success"
                                            ? "bg-emerald-50 text-emerald-700"
                                            : currentStatus === "error"
                                                ? "bg-red-50 text-red-700"
                                                : "bg-slate-100 text-slate-600"
                                    }`}
                                    title={statusMessage}
                                >
                                    {currentStatus === "success" ? (
                                        <CheckCircle2 className="h-3 w-3 shrink-0" />
                                    ) : currentStatus === "error" ? (
                                        <AlertCircle className="h-3 w-3 shrink-0" />
                                    ) : (
                                        <LoaderCircle className="h-3 w-3 shrink-0 animate-spin" />
                                    )}
                                    <span className="truncate">
                                        {currentStatus === "success"
                                            ? t("components.models.ModelCardV2.k40bd808e")
                                            : currentStatus === "error"
                                                ? t("components.models.ModelCardV2.k7f8e6bd9")
                                                : t("components.models.ModelCardV2.kc9e37984")}
                                    </span>
                                </div>
                            ) : null}
                        </div>
                    </div>
                </div>

                <div className="mt-auto flex items-end justify-between gap-2">
                    <div className="min-w-0" />

                    <div className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover/card:opacity-100">
                        {onTestConnection && (
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-muted-foreground hover:text-primary"
                                disabled={testing}
                                onClick={async () => {
                                    await onTestConnection(modelRef);
                                }}
                                title={testing ? t("components.models.ModelCardV2.k60eba059") : t("components.models.ModelCardV2.kdf48b898")}
                            >
                                <PlugZap className={`h-3.5 w-3.5 ${testing ? "animate-pulse" : ""}`} />
                            </Button>
                        )}
                        {!isDefault && onSetDefault && (
                            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-primary" onClick={() => onSetDefault(modelRef)} title={t("components.models.ModelCardV2.ka96c553d")}>
                                <Star className="h-3.5 w-3.5" />
                            </Button>
                        )}
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
        </Card>
    );
}
