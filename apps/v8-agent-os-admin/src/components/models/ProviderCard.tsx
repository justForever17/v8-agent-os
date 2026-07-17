"use client";

import Image from "next/image";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { useT } from "@/components/providers/LocaleProvider";
import { Settings, Power, Trash2 } from "lucide-react";
import type { ProviderOverview } from "@/components/models/control-plane-types";
import { resolveProviderLogo } from "@/lib/models/model-assets";

interface ProviderCardProps {
    provider: {
        id: string;
        name: string;
        code: string;
        description?: string | null;
        icon?: string | null;
        baseUrl?: string | null;
        apiStandard?: string | null;
        type: string;
        logoAsset?: string | null;
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
    const providerLogo = resolveProviderLogo({
        providerId: provider.id || provider.code,
        providerName: provider.name,
        explicitAsset: provider.logoAsset || null,
    });
    const status = health?.status || (provider.isEnabled ? "healthy" : "disabled");
    const statusLabel = getStatusLabel(status, t);
    const details = [
        `Provider: ${provider.name}`,
        `Code: ${provider.code}`,
        `Type: ${provider.type}${provider.apiStandard ? ` / ${provider.apiStandard}` : ""}`,
        provider.baseUrl ? `Base URL: ${provider.baseUrl}` : "",
        health?.reason || provider.description || "",
        health ? `Models: ${health.enabledModels}/${health.models} enabled` : `Models: ${provider.models.length}`,
        health ? `Roles: ${health.assignedRoles.join(", ") || "none"}` : "",
        health ? `Events: ${health.events}, errors: ${Math.round(health.errorRate * 100)}%, avg latency: ${Math.round(health.avgLatencyMs)}ms` : "",
        localVisionLabel ? `Local vision: ${localVisionLabel}` : "",
    ].filter(Boolean);

    return (
        <Card className="group/card relative h-[128px] overflow-visible transition-shadow hover:shadow-md">
            <CardContent className="flex h-full flex-col p-3">
                <div className="flex items-start justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                        <AdminHoverInfo
                            lines={details}
                            triggerClassName="h-7 w-7 shrink-0 justify-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-muted dark:text-muted-foreground"
                        >
                            {providerLogo ? (
                                <Image src={providerLogo} alt="" width={20} height={20} className="h-5 w-5 rounded object-contain" unoptimized />
                            ) : (
                                providerMark
                            )}
                        </AdminHoverInfo>
                        <div className="min-w-0">
                            <div className="truncate text-sm font-semibold" title={provider.name}>{provider.name}</div>
                            <div className="truncate text-[11px] text-muted-foreground" title={provider.code}>
                                {provider.code}
                            </div>
                        </div>
                    </div>
                    <Badge variant={status === "attention" ? "secondary" : provider.isEnabled ? "default" : "secondary"} className="h-5 shrink-0 px-2 text-[10px]">
                        {statusLabel}
                    </Badge>
                </div>
                <div className="mt-auto flex items-end justify-between gap-2">
                    <div className="min-w-0 space-y-1 text-[11px] font-medium">
                        <div className="truncate font-mono text-[10px] text-muted-foreground" title={provider.baseUrl || provider.code}>
                            {provider.baseUrl || provider.code}
                        </div>
                        <div className="truncate">{provider.models.length} {t("components.models.ProviderCard.k5503fbe2")}</div>
                    </div>
                    <div className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover/card:opacity-100">
                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => onToggle(provider.id, !provider.isEnabled)}>
                            <Power className={`h-3.5 w-3.5 ${provider.isEnabled ? "text-green-500" : "text-muted-foreground"}`} />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => onEdit(provider)}>
                            <Settings className="h-3.5 w-3.5" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => onDelete(provider.id)}>
                            <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
