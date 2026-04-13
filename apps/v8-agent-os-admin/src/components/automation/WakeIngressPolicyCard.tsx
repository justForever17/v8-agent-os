"use client";

import * as React from "react";

import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
import { lt } from "@/lib/locale";
import { fetchConfigDomain, saveConfigDomain } from "@/lib/config-registry";

type WakeIngressPolicies = {
    allowNudgeWithoutTarget: boolean;
    defaultAttachPolicy: "new_session" | "attach_session" | "attach_run" | "resume_run";
    enabledSourceRuntimes: string[];
};

const DEFAULT_CONFIG: WakeIngressPolicies = {
    allowNudgeWithoutTarget: true,
    defaultAttachPolicy: "new_session",
    enabledSourceRuntimes: ["cron", "hook", "plugin_host", "network_supervisor", "chat", "computer_use"],
};

export function WakeIngressPolicyCard() {
    const t = useT();
    const { toast } = useToast();
    const [config, setConfig] = React.useState<WakeIngressPolicies>(DEFAULT_CONFIG);
    const [loading, setLoading] = React.useState(true);
    const [saving, setSaving] = React.useState(false);
    const [sourceRuntimeInput, setSourceRuntimeInput] = React.useState(DEFAULT_CONFIG.enabledSourceRuntimes.join(", "));

    const loadConfig = React.useCallback(async () => {
        setLoading(true);
        try {
            const data = await fetchConfigDomain<{ wakeIngressPolicies?: Partial<WakeIngressPolicies> }>("automation-runtime");
            const policies = data?.data?.wakeIngressPolicies || {};
            const normalized: WakeIngressPolicies = {
                allowNudgeWithoutTarget: policies.allowNudgeWithoutTarget !== false,
                defaultAttachPolicy: (policies.defaultAttachPolicy || DEFAULT_CONFIG.defaultAttachPolicy) as WakeIngressPolicies["defaultAttachPolicy"],
                enabledSourceRuntimes: Array.isArray(policies.enabledSourceRuntimes)
                    ? policies.enabledSourceRuntimes.map((item) => String(item).trim()).filter(Boolean)
                    : DEFAULT_CONFIG.enabledSourceRuntimes,
            };
            setConfig(normalized);
            setSourceRuntimeInput(normalized.enabledSourceRuntimes.join(", "));
        } catch (error) {
            toast({
                variant: "destructive",
                title: t(lt("加载失败", "Load failed")),
                description:
                    error instanceof Error
                        ? error.message
                        : t(lt("无法读取 Wake Ingress 配置。", "Unable to load wake ingress policies.")),
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
            const nextConfig: WakeIngressPolicies = {
                ...config,
                enabledSourceRuntimes: sourceRuntimeInput
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean),
            };
            await saveConfigDomain("automation-runtime", {
                data: {
                    wakeIngressPolicies: nextConfig,
                },
            });
            setConfig(nextConfig);
            setSourceRuntimeInput(nextConfig.enabledSourceRuntimes.join(", "));
            toast({
                title: t(lt("已保存", "Saved")),
                description: t(lt("Wake ingress policy 已写入 AUTOMATION RUNTIME。", "Wake ingress policy has been saved to AUTOMATION RUNTIME.")),
            });
        } catch (error) {
            toast({
                variant: "destructive",
                title: t(lt("保存失败", "Save failed")),
                description:
                    error instanceof Error
                        ? error.message
                        : t(lt("Wake ingress policy 保存失败。", "Failed to save wake ingress policy.")),
            });
        } finally {
            setSaving(false);
        }
    }, [config, sourceRuntimeInput, t, toast]);

    return (
        <ConfigCard
            title={lt("Wake Ingress Policies", "Wake ingress policies")}
            description={lt(
                "统一管理所有非人类触发入口的 envelope 规则。nudge 只是 WakeIngressEnvelope.triggerKind 的一种轻量类型，不再作为独立功能存在。",
                "Configure the unified envelope policy for all non-human ingress sources. Nudge now exists only as a WakeIngressEnvelope trigger kind, not a standalone feature.",
            )}
            className="h-full"
        >
            <div className="space-y-4">
                <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-6 text-slate-600">
                    {t(
                        lt(
                            "没有 targetBinding 或 recoveryAnchor 的 hooks / cron，只会被当作 nudge。真正的 wake / recovery_wake 必须显式带上绑定或恢复锚点。",
                            "Hooks or cron triggers without targetBinding or recoveryAnchor are treated only as nudge. Real wake / recovery_wake events must carry explicit binding or recovery anchors.",
                        ),
                    )}
                </div>

                <label className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                    <div className="pr-4">
                        <div className="text-sm font-medium text-slate-900">{t(lt("允许无目标 nudge", "Allow nudge without target"))}</div>
                        <div className="text-xs text-slate-500">{t(lt("只影响 triggerKind=nudge；不影响 wake / recovery_wake 的显式锚点要求。", "Only affects triggerKind=nudge; wake / recovery_wake still require explicit anchors."))}</div>
                    </div>
                    <Switch
                        checked={config.allowNudgeWithoutTarget}
                        disabled={loading || saving}
                        onCheckedChange={(checked) => setConfig((prev) => ({ ...prev, allowNudgeWithoutTarget: checked }))}
                    />
                </label>

                <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                        <Label>{t(lt("默认附着策略", "Default attach policy"))}</Label>
                        <Select
                            value={config.defaultAttachPolicy}
                            onValueChange={(value: WakeIngressPolicies["defaultAttachPolicy"]) => setConfig((prev) => ({ ...prev, defaultAttachPolicy: value }))}
                            disabled={loading || saving}
                        >
                            <SelectTrigger>
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="new_session">new_session</SelectItem>
                                <SelectItem value="attach_session">attach_session</SelectItem>
                                <SelectItem value="attach_run">attach_run</SelectItem>
                                <SelectItem value="resume_run">resume_run</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="space-y-2">
                        <Label>{t(lt("允许的来源 runtime（逗号分隔）", "Enabled source runtimes (comma separated)"))}</Label>
                        <Input
                            value={sourceRuntimeInput}
                            disabled={loading || saving}
                            onChange={(event) => setSourceRuntimeInput(event.target.value)}
                            placeholder="cron, hook, plugin_host, network_supervisor"
                        />
                    </div>
                </div>

                <div className="flex items-center justify-end gap-3">
                    <Button variant="outline" onClick={() => void loadConfig()} disabled={loading || saving}>
                        {t(lt("刷新", "Refresh"))}
                    </Button>
                    <Button onClick={() => void handleSave()} disabled={loading || saving}>
                        {saving ? t(lt("保存中", "Saving")) : t(lt("保存策略", "Save policy"))}
                    </Button>
                </div>
            </div>
        </ConfigCard>
    );
}
