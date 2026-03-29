"use client";

import * as React from "react";

import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { lt } from "@/lib/locale";

type HeartbeatConfig = {
    enabled: boolean;
    intervalMinutes: number;
    messageTemplate: string;
    onlyWhenIdle: boolean;
    suppressWhenActiveRun: boolean;
};

const DEFAULT_CONFIG: HeartbeatConfig = {
    enabled: false,
    intervalMinutes: 30,
    messageTemplate: "What did you do today? How is the task going? Why are you not continuing right now?",
    onlyWhenIdle: true,
    suppressWhenActiveRun: true,
};

export function SupervisorHeartbeatCard() {
    const t = useT();
    const { toast } = useToast();
    const [config, setConfig] = React.useState<HeartbeatConfig>(DEFAULT_CONFIG);
    const [loading, setLoading] = React.useState(true);
    const [saving, setSaving] = React.useState(false);

    const loadConfig = React.useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetch("/api/config-registry/automation-runtime", { cache: "no-store" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(String(data?.error || "Failed to load automation runtime config"));
            }
            const heartbeat = data?.data?.supervisorHeartbeat ?? data?.supervisorHeartbeat ?? {};
            setConfig({
                enabled: Boolean(heartbeat.enabled),
                intervalMinutes: Number(heartbeat.intervalMinutes || DEFAULT_CONFIG.intervalMinutes),
                messageTemplate: String(heartbeat.messageTemplate || DEFAULT_CONFIG.messageTemplate),
                onlyWhenIdle: heartbeat.onlyWhenIdle !== false,
                suppressWhenActiveRun: heartbeat.suppressWhenActiveRun !== false,
            });
        } catch (error) {
            toast({
                variant: "destructive",
                title: t(lt("加载失败", "Load failed")),
                description:
                    error instanceof Error
                        ? error.message
                        : t(lt("无法读取 Supervisor 心跳配置。", "Unable to load supervisor heartbeat config.")),
            });
        } finally {
            setLoading(false);
        }
    }, [t, toast]);

    React.useEffect(() => {
        void loadConfig();
    }, [loadConfig]);

    const handleSave = React.useCallback(async () => {
        setSaving(true);
        try {
            const res = await fetch("/api/config-registry/automation-runtime", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ supervisorHeartbeat: config }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(String(data?.error || "Failed to save automation runtime config"));
            }
            toast({
                title: t(lt("已保存", "Saved")),
                description: t(lt("Supervisor 心跳设置已写入 AUTOMATION RUNTIME。", "Supervisor heartbeat settings were saved to AUTOMATION RUNTIME.")),
            });
        } catch (error) {
            toast({
                variant: "destructive",
                title: t(lt("保存失败", "Save failed")),
                description:
                    error instanceof Error
                        ? error.message
                        : t(lt("Supervisor 心跳设置保存失败。", "Failed to save supervisor heartbeat settings.")),
            });
        } finally {
            setSaving(false);
        }
    }, [config, t, toast]);

    return (
        <ConfigCard
            title={lt("Supervisor 心跳唤醒", "Supervisor heartbeat")}
            description={lt(
                "按固定节奏唤醒 Supervisor，询问今天做了什么、进展如何以及为什么没有继续。",
                "Wake the Supervisor on a fixed cadence and ask what was done, how the work is going, and why it stopped.",
            )}
            className="h-full"
        >
            <div className="space-y-4">
                <div className="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                    <div className="space-y-1">
                        <div className="text-sm font-medium text-slate-900">{t(lt("启用心跳任务", "Enable heartbeat job"))}</div>
                        <div className="text-xs text-slate-500">{t(lt("配置来源 config.json#automationRuntime", "Source: config.json#automationRuntime"))}</div>
                    </div>
                    <Switch
                        checked={config.enabled}
                        disabled={loading || saving}
                        onCheckedChange={(checked) => setConfig((prev) => ({ ...prev, enabled: checked }))}
                    />
                </div>

                <label className="block space-y-2">
                    <span className="text-sm font-medium text-slate-700">{t(lt("间隔时间（分钟）", "Interval (minutes)"))}</span>
                    <Input
                        type="number"
                        min={5}
                        step={5}
                        value={String(config.intervalMinutes)}
                        disabled={loading || saving}
                        onChange={(event) =>
                            setConfig((prev) => ({
                                ...prev,
                                intervalMinutes: Math.max(5, Number(event.target.value || DEFAULT_CONFIG.intervalMinutes)),
                            }))
                        }
                    />
                </label>

                <label className="block space-y-2">
                    <span className="text-sm font-medium text-slate-700">{t(lt("发送内容", "Message template"))}</span>
                    <Textarea
                        rows={4}
                        value={config.messageTemplate}
                        disabled={loading || saving}
                        onChange={(event) => setConfig((prev) => ({ ...prev, messageTemplate: event.target.value }))}
                    />
                </label>

                <div className="grid gap-3 md:grid-cols-2">
                    <label className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                        <div className="pr-4">
                            <div className="text-sm font-medium text-slate-900">{t(lt("仅在空闲时触发", "Only when idle"))}</div>
                            <div className="text-xs text-slate-500">{t(lt("活跃任务较少时才触发。", "Only wake when the system looks idle."))}</div>
                        </div>
                        <Switch
                            checked={config.onlyWhenIdle}
                            disabled={loading || saving}
                            onCheckedChange={(checked) => setConfig((prev) => ({ ...prev, onlyWhenIdle: checked }))}
                        />
                    </label>

                    <label className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                        <div className="pr-4">
                            <div className="text-sm font-medium text-slate-900">{t(lt("活跃 run 时抑制", "Suppress during active runs"))}</div>
                            <div className="text-xs text-slate-500">{t(lt("避免打断正在运行的工作。", "Avoid nudging while a run is active."))}</div>
                        </div>
                        <Switch
                            checked={config.suppressWhenActiveRun}
                            disabled={loading || saving}
                            onCheckedChange={(checked) => setConfig((prev) => ({ ...prev, suppressWhenActiveRun: checked }))}
                        />
                    </label>
                </div>

                <div className="flex items-center justify-end gap-3">
                    <Button variant="outline" onClick={() => void loadConfig()} disabled={loading || saving}>
                        {t(lt("刷新", "Refresh"))}
                    </Button>
                    <Button onClick={() => void handleSave()} disabled={loading || saving}>
                        {saving ? t(lt("保存中", "Saving")) : t(lt("保存配置", "Save config"))}
                    </Button>
                </div>
            </div>
        </ConfigCard>
    );
}
