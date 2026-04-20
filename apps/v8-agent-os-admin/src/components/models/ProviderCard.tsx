"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
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

function getStatusLabel(status: string | undefined, t: (value: string) => string) {
    if (status === "healthy") return t("components.models.ProviderCard.k57c1ee90");
    if (status === "attention") return t("components.models.ProviderCard.ke8a492aa");
    if (status === "disabled") return t("components.models.ProviderCard.k31ff46bd");
    return t("components.models.ProviderCard.k76ebff7c");
}

function getLocalVisionLabel(status: string | undefined, t: (value: string) => string) {
    if (status === "supported") return t("components.models.ProviderCard.k4c03d4f0");
    if (status === "unsupported") return t("components.models.ProviderCard.k9ef4d508");
    if (status === "unknown") return t("components.models.ProviderCard.k82f3edf9");
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
                    {health?.reason || provider.description || t("components.models.ProviderCard.k86e9a787")}
                </div>
                <div className="mt-auto flex items-start justify-between gap-3">
                    <div className="space-y-1 text-xs font-medium">
                        <div>{provider.models.length} {t("components.models.ProviderCard.k5503fbe2")}</div>
                        {health && (
                            <>
                                <div className="text-muted-foreground">
                                    {health.assignedRoles.length} {t("components.models.ProviderCard.k5295e7fe")} · {t("components.models.ProviderCard.kdb6c0cc1")} {health.enabledModels}/{health.models}
                                </div>
                                <div className="text-muted-foreground">
                                    {health.events} {t("components.models.ProviderCard.kd457901c")} · {t("components.models.ProviderCard.k30f11e61")} {Math.round(health.errorRate * 100)}% · {Math.round(health.avgLatencyMs)}ms
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
