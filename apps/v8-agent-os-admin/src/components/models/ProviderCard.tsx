"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
import { Settings, Power, Trash2 } from "lucide-react";
import type { ProviderOverview } from "@/components/models/control-plane-types";

interface ProviderCardProps {
    provider: {
        id: string;
        name: string;
        code: string;
        description?: string | null;
        icon?: string | null;
        type: string;
        isEnabled: boolean;
        models: { id: string }[];
    };
    health?: ProviderOverview | null;
    onEdit: (provider: ProviderCardProps['provider']) => void;
    onDelete: (id: string) => void;
    onToggle: (id: string, enabled: boolean) => void;
}

function getStatusLabel(status: string | undefined, t: (value: string | { "zh-CN": string; en: string }) => string) {
    if (status === "healthy") return t(lt("健康", "Healthy"));
    if (status === "attention") return t(lt("需关注", "Attention"));
    if (status === "disabled") return t(lt("停用", "Disabled"));
    return t(lt("未知", "Unknown"));
}

function getLocalVisionLabel(status: string | undefined, t: (value: string | { "zh-CN": string; en: string }) => string) {
    if (status === "supported") return t(lt("本地视觉可用", "Local vision ready"));
    if (status === "unsupported") return t(lt("本地视觉不可用", "Local vision unavailable"));
    if (status === "unknown") return t(lt("未探测到本地视觉能力", "Local vision not detected"));
    return "";
}

export function ProviderCard({ provider, health, onEdit, onDelete, onToggle }: ProviderCardProps) {
    const t = useT();
    const localProbe = health?.localCapabilityProbe;
    const localVisionLabel = provider.type === "LOCAL" ? getLocalVisionLabel(localProbe?.status, t) : "";
    const providerMark = (provider.name || provider.code || "P").trim().charAt(0).toUpperCase();

    return (
        <Card className="group relative h-full min-h-[184px] transition-shadow hover:shadow-md">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium flex items-center gap-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-sm font-semibold text-slate-600">
                        {provider.icon || providerMark}
                    </span>
                    <span className="line-clamp-2 break-words">{provider.name}</span>
                </CardTitle>
                <Badge variant={health?.status === "attention" ? "secondary" : provider.isEnabled ? "default" : "secondary"}>
                    {getStatusLabel(health?.status || (provider.isEnabled ? "healthy" : "disabled"), t)}
                </Badge>
            </CardHeader>
            <CardContent className="flex h-[calc(100%-4.5rem)] flex-col">
                <div className="mb-4 min-h-[2.75rem] text-xs text-muted-foreground line-clamp-2">
                    {health?.reason || provider.description || t("暂无说明。")}
                </div>
                <div className="mt-auto flex items-start justify-between gap-3">
                    <div className="space-y-1 text-xs font-medium">
                        <div>{provider.models.length} {t(lt("个模型", "models"))}</div>
                        {health && (
                            <>
                                <div className="text-muted-foreground">
                                    {health.assignedRoles.length} {t(lt("个绑定", "bindings"))} · {t(lt("已启用", "Enabled"))} {health.enabledModels}/{health.models}
                                </div>
                                <div className="text-muted-foreground">
                                    {health.events} {t(lt("次事件", "events"))} · {t(lt("错误率", "Error rate"))} {Math.round(health.errorRate * 100)}% · {Math.round(health.avgLatencyMs)}ms
                                </div>
                                {localVisionLabel ? (
                                    <div className="line-clamp-1 text-muted-foreground">
                                        {localVisionLabel}
                                    </div>
                                ) : null}
                            </>
                        )}
                    </div>
                    <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => onToggle(provider.id, !provider.isEnabled)}>
                            <Power className={`w-4 h-4 ${provider.isEnabled ? "text-green-500" : "text-muted-foreground"}`} />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => onEdit(provider)}>
                            <Settings className="w-4 h-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-8 w-8 hover:text-destructive" onClick={() => onDelete(provider.id)}>
                            <Trash2 className="w-4 h-4" />
                        </Button>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
