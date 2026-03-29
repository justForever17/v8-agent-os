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
    { key: "fast", title: "更快响应", description: "忙时直接拒绝同会话新任务，减少等待。", lane: "reject" as const },
    { key: "balanced", title: "平衡稳定", description: "同一会话排队等待，适合大多数日常使用。", lane: "queue" as const },
    { key: "durable", title: "长任务优先", description: "优先保证长任务不互踩，建议长期保持。", lane: "queue" as const },
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
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="稳定性策略"
                description="设置系统在速度、排队和长任务之间的取舍。"
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void handleSave()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Gauge className="mr-2 h-4 w-4" />}
                            {t("保存")}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip
                items={[
                    { label: "当前策略", value: MODES.find((item) => item.key === mode)?.title || "平衡稳定", description: "当前首页选择的稳定性方案。" },
                    { label: "持久化护栏", value: durability ? "已开启" : "已关闭", description: "建议长期保持开启。" },
                    { label: "同会话任务", value: envelope.data.sessionLanePolicy === "reject" ? "忙时拒绝" : "稳妥排队", description: "控制同一会话内的任务冲突。" },
                    { label: "保存位置", value: "config.json", description: "系统会把稳定性策略写入统一配置。" },
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
                                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
                        )}
                        onClick={() => setMode(item.key)}
                    >
                        <div className="text-base font-semibold">{item.title}</div>
                        <div className="mt-2 text-sm leading-6 text-slate-500">{item.description}</div>
                    </button>
                ))}
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="flex items-center justify-between gap-3">
                    <div>
                        <div className="text-sm font-medium text-slate-900">{t("禁止持久化回退")}</div>
                        <div className="mt-1 text-sm leading-6 text-slate-500">{t("关闭后长期任务更容易丢失运行状态。")}</div>
                    </div>
                    <Button
                        variant={durability ? "default" : "outline"}
                        className="rounded-2xl"
                        onClick={() => setDurability((current) => !current)}
                    >
                        {durability ? t("已开启") : t("已关闭")}
                    </Button>
                </div>
            </div>

            <SourceMetaRow
                source={envelope.source}
                savePath={envelope.savePath}
                reloadRequired={envelope.reloadRequired}
            />

            <AdvancedSection title="详细规则" defaultOpen={false}>
                <RuntimeStabilityPanel />
            </AdvancedSection>
        </AdminPageShell>
    );
}
