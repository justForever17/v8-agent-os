"use client";

import type { ReactNode } from "react";
import { AlertCircle, CheckCircle2, Copy, Edit2, LoaderCircle, PlugZap, Star, Trash2 } from "lucide-react";

import type { ControlPlaneModel } from "@/components/models/control-plane-types";
import { useT } from "@/components/providers/LocaleProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { lt } from "@/lib/locale";

type LocalizedKey = string | { "zh-CN": string; en: string };

interface ModelCardV2Props {
    model: {
        id: string;
        name: string;
        modelId: string;
        type: string;
        provider?: { name: string; icon?: string | null } | null;
        isEnabled: boolean;
        contextWindow?: number | null;
    };
    controlMeta?: ControlPlaneModel | null;
    isDefault?: boolean;
    onSetDefault?: (modelId: string) => void;
    onTestConnection?: (modelId: string) => Promise<void> | void;
    connectionStatus?: {
        status: "idle" | "testing" | "success" | "error";
        message?: string;
    } | null;
    onEdit: (model: ModelCardV2Props["model"]) => void;
    onDelete: (model: ModelCardV2Props["model"]) => void;
}

const ROLE_LABELS: Record<string, LocalizedKey> = {
    default: lt("默认聊天", "Default chat"),
    supervisor: lt("主理人", "Lead"),
    summary: lt("摘要", "Summary"),
    extraction: lt("记忆提取", "Extraction"),
    vision: lt("视觉", "Vision"),
    embedding: lt("向量", "Embedding"),
    reranker: lt("重排", "Rerank"),
    channel: lt("渠道", "Channel"),
    automation: lt("自动化", "Automation"),
    computer_use_planner: lt("CU 规划", "CU planner"),
    computer_use_visual_judge: lt("CU 裁判", "CU judge"),
    rpa_discovery: lt("RPA 摸索", "RPA scout"),
};

function FadeOverflowRow({ children }: { children: ReactNode }) {
    return (
        <div className="relative mt-2 h-7 overflow-hidden">
            <div className="flex h-7 min-w-0 flex-nowrap items-center gap-1.5 overflow-hidden whitespace-nowrap pr-10">
                {children}
            </div>
            <div className="pointer-events-none absolute inset-y-0 right-0 w-12 bg-gradient-to-l from-card via-card/95 to-transparent" />
        </div>
    );
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

    return (
        <Card className={`group h-full min-h-[208px] transition-colors ${isDefault ? "border-primary shadow-sm" : "hover:border-primary/50"}`}>
            <CardContent className="flex h-full items-start justify-between gap-4 p-4">
                <div className="flex min-w-0 flex-1 items-start gap-4 overflow-hidden">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-sm font-semibold text-slate-600">
                        {model.provider?.icon || providerMark}
                    </div>
                    <div className="flex min-w-0 flex-1 flex-col">
                        <div className="flex min-h-[3.25rem] flex-wrap items-start gap-2 font-semibold">
                            <span className="line-clamp-2 break-words" title={model.name}>{model.name}</span>
                            <Badge variant="outline" className="h-5 shrink-0 text-[10px]">
                                {model.type}
                            </Badge>
                            {isDefault && (
                                <Badge className="h-5 shrink-0 border-none bg-primary/20 text-[10px] text-primary hover:bg-primary/30">
                                    <Star className="mr-1 h-3 w-3 fill-primary" />
                                    {t(lt("全局默认", "Default"))}
                                </Badge>
                            )}
                        </div>

                        {capabilityTags.length > 0 && (
                            <FadeOverflowRow>
                                {capabilityTags.slice(0, 4).map((tag) => (
                                    <Badge key={`${model.modelId}:${tag}`} variant="secondary" className="h-5 shrink-0 rounded-full px-2 text-[10px] font-medium">
                                        {tag}
                                    </Badge>
                                ))}
                                {capabilityTags.length > 4 && (
                                    <Badge variant="secondary" className="h-5 shrink-0 rounded-full px-2 text-[10px] font-medium">
                                        +{capabilityTags.length - 4}
                                    </Badge>
                                )}
                            </FadeOverflowRow>
                        )}

                        <div className="mt-2 min-h-[2.5rem] space-y-1 text-xs text-muted-foreground">
                            <span className="max-w-[150px] truncate rounded bg-muted px-1 font-mono" title={model.modelId}>{model.modelId}</span>
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                <span className="max-w-[120px] truncate" title={model.provider?.name}>{model.provider?.name || t("未知供应商")}</span>
                                {model.contextWindow && (
                                    <>
                                        <span className="shrink-0">·</span>
                                        <span className="shrink-0">{Math.round(model.contextWindow / 1000)}k {t(lt("上下文", "ctx"))}</span>
                                    </>
                                )}
                            </div>
                        </div>

                        {assignedRoles.length > 0 && (
                            <FadeOverflowRow>
                                {assignedRoles.slice(0, 3).map((role) => (
                                    <Badge key={`${model.modelId}:${role}`} variant="outline" className="h-5 shrink-0 rounded-full px-2 text-[10px]">
                                        {ROLE_LABELS[role] ? t(ROLE_LABELS[role]) : role}
                                    </Badge>
                                ))}
                                {assignedRoles.length > 3 && (
                                    <Badge variant="outline" className="h-5 shrink-0 rounded-full px-2 text-[10px]">
                                        +{assignedRoles.length - 3}
                                    </Badge>
                                )}
                            </FadeOverflowRow>
                        )}

                        <div className="mt-2 min-h-[1.75rem]">
                            {currentStatus !== "idle" ? (
                                <div
                                    className={`inline-flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] ${
                                        currentStatus === "success"
                                            ? "bg-emerald-50 text-emerald-700"
                                            : currentStatus === "error"
                                                ? "bg-red-50 text-red-700"
                                                : "bg-slate-100 text-slate-600"
                                    }`}
                                    title={statusMessage}
                                >
                                    {currentStatus === "success" ? (
                                        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                                    ) : currentStatus === "error" ? (
                                        <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                                    ) : (
                                        <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin" />
                                    )}
                                    <span className="truncate">
                                        {currentStatus === "success"
                                            ? `${t(lt("连接成功", "Connected"))}${statusMessage ? ` · ${statusMessage}` : ""}`
                                            : currentStatus === "error"
                                                ? `${t(lt("连接失败", "Failed"))}${statusMessage ? ` · ${statusMessage}` : ""}`
                                                : `${t(lt("测试连接中", "Testing"))}${statusMessage ? ` · ${statusMessage}` : ""}`}
                                    </span>
                                </div>
                            ) : null}
                        </div>
                    </div>
                </div>

                <div className="flex shrink-0 items-start gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                    {onTestConnection && (
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-muted-foreground hover:text-primary"
                            disabled={testing}
                            onClick={async () => {
                                await onTestConnection(model.modelId);
                            }}
                            title={testing ? t(lt("测试中...", "Testing...")) : t(lt("测试连接", "Test connection"))}
                        >
                            <PlugZap className={`h-4 w-4 ${testing ? "animate-pulse" : ""}`} />
                        </Button>
                    )}
                    {!isDefault && onSetDefault && (
                        <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-primary" onClick={() => onSetDefault(model.modelId)} title={t(lt("设为默认", "Set default"))}>
                            <Star className="h-4 w-4" />
                        </Button>
                    )}
                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => navigator.clipboard.writeText(model.modelId)} title={t(lt("复制模型 ID", "Copy model ID"))}>
                        <Copy className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => onEdit(model)} title={t(lt("编辑", "Edit"))}>
                        <Edit2 className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-8 w-8 hover:text-destructive" onClick={() => onDelete(model)} title={t(lt("删除", "Delete"))}>
                        <Trash2 className="h-4 w-4" />
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
}
