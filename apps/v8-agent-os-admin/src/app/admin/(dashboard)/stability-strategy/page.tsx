"use client";

import { useEffect, useState } from "react";
import { Gauge, Loader2 } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { RuntimeStabilityPanel } from "@/components/runtime/RuntimeStabilityPanel";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { cn } from "@/lib/utils";

type StabilityData = {
    strictSupervisorDurability?: boolean;
    sessionLanePolicy?: "queue" | "reject" | "interrupt_then_replace";
    paths?: Record<string, string>;
    summaries?: Record<string, unknown>;
};

const MODES = [
    { key: "fast", title:"app.admin.dashboard.stability.strategy.page.kfd324ea2", description:"app.admin.dashboard.stability.strategy.page.kbe7d8e91", lane: "reject" as const },
    { key: "balanced", title:"app.admin.dashboard.stability.strategy.page.k01e41059", description:"app.admin.dashboard.stability.strategy.page.k2b7cb710", lane: "queue" as const },
    { key: "durable", title:"app.admin.dashboard.stability.strategy.page.k9a5e1d3b", description:"app.admin.dashboard.stability.strategy.page.k97d5e288", lane: "queue" as const },
];

export default function StabilityStrategyPage() {
    const t = useT();
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<StabilityData> | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [mode, setMode] = useState("balanced");
    const [durability, setDurability] = useState(true);

    const loadConfig = async () => {
        setLoading(true);
        try {
            const next = await fetchConfigDomain<StabilityData>("runtime-stability");
            setEnvelope(next);
            setDurability(Boolean(next.data.strictSupervisorDurability ?? true));
            setMode(next.data.sessionLanePolicy === "reject" ? "fast" : "balanced");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadConfig();
    }, []);

    const handleSave = async () => {
        if (!envelope) return;
        setSaving(true);
        try {
            const selectedMode = MODES.find((item) => item.key === mode) || MODES[1];
            const next = await saveConfigDomain<StabilityData>("runtime-stability", {
                data: {
                    ...envelope.data,
                    strictSupervisorDurability: durability,
                    sessionLanePolicy: selectedMode.lane,
                },
            });
            setEnvelope(next);
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
        } finally {
            setSaving(false);
        }
    };

    if (loading || !envelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground/80" />
            </div>
        );
    }

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="app.admin.dashboard.stability.strategy.page.kb80fddf8"
                description="app.admin.dashboard.stability.strategy.page.k64ae0dd1"
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void handleSave()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Gauge className="mr-2 h-4 w-4" />}
                            {t("app.admin.dashboard.stability.strategy.page.k6010e1ed")}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip
                items={[
                    { label:"app.admin.dashboard.stability.strategy.page.k2837705a", value: MODES.find((item) => item.key === mode)?.title || "app.admin.dashboard.stability.strategy.page.mode.balancedFallback", description:"app.admin.dashboard.stability.strategy.page.k93931b27" },
                    { label:"app.admin.dashboard.stability.strategy.page.kb3e12f3b", value: durability ? "app.admin.dashboard.stability.strategy.page.state.enabled" : "app.admin.dashboard.stability.strategy.page.state.disabled", description:"app.admin.dashboard.stability.strategy.page.kddaaa8bf" },
                    { label:"app.admin.dashboard.stability.strategy.page.k1700829f", value: envelope.data.sessionLanePolicy === "reject" ? "app.admin.dashboard.stability.strategy.page.lane.reject" : "app.admin.dashboard.stability.strategy.page.lane.queue", description:"app.admin.dashboard.stability.strategy.page.k4038289f" },
                    { label:"app.admin.dashboard.stability.strategy.page.kee73bb6b", value: "config.json", description:"app.admin.dashboard.stability.strategy.page.k0ef844ca" },
                ]}
            />

            <div className="grid gap-4 lg:grid-cols-3">
                {MODES.map((item) => (
                    <button
                        key={item.key}
                        type="button"
                        className={cn(
                            "rounded-2xl border px-5 py-5 text-left shadow-sm transition-colors",
                            mode === item.key
                                ? "border-sky-200 bg-sky-50 text-sky-900"
                                : "border-border bg-card text-foreground hover:border-input"
                        )}
                        onClick={() => setMode(item.key)}
                    >
                        <div className="text-base font-semibold">{item.title}</div>
                    </button>
                ))}
            </div>

            <div className="rounded-2xl border border-border bg-card p-5 shadow-sm">
                <div className="flex items-center justify-between gap-3">
                    <div>
                        <div className="text-sm font-medium text-foreground">{t("app.admin.dashboard.stability.strategy.page.kb33db226")}</div>
                    </div>
                    <Button
                        variant={durability ? "default" : "outline"}
                        className="rounded-2xl"
                        onClick={() => setDurability((current) => !current)}
                    >
                        {durability ? t("app.admin.dashboard.stability.strategy.page.kd945d5d0") : t("app.admin.dashboard.stability.strategy.page.k12b31ba6")}
                    </Button>
                </div>
            </div>

            <SourceMetaRow
                source={envelope.source}
                savePath={envelope.savePath}
                reloadRequired={envelope.reloadRequired}
            />

            <AdvancedSection title="app.admin.dashboard.stability.strategy.page.kcb5109d5" defaultOpen={false}>
                <RuntimeStabilityPanel />
            </AdvancedSection>
        </AdminPageShell>
    );
}
