"use client";
import * as React from "react";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from "@/components/ui/select";
import { useT } from "@/components/providers/LocaleProvider";
import { useToast } from "@/components/ui/use-toast";
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
            const data = await fetchConfigDomain<{
                wakeIngressPolicies?: Partial<WakeIngressPolicies>;
            }>("automation-runtime");
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
        }
        catch (error) {
            toast({
                variant: "destructive",
                title: t("components.automation.WakeIngressPolicyCard.k65ed1d75"),
                description: error instanceof Error
                    ? error.message
                    : t("components.automation.WakeIngressPolicyCard.k32cdcf01"),
            });
        }
        finally {
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
                title: t("components.automation.WakeIngressPolicyCard.k9bfe7d6d"),
                description: t("components.automation.WakeIngressPolicyCard.k4ae10f20"),
            });
        }
        catch (error) {
            toast({
                variant: "destructive",
                title: t("components.automation.WakeIngressPolicyCard.k12769ce1"),
                description: error instanceof Error
                    ? error.message
                    : t("components.automation.WakeIngressPolicyCard.k9be1db05"),
            });
        }
        finally {
            setSaving(false);
        }
    }, [config, sourceRuntimeInput, t, toast]);
    return (<ConfigCard title={"components.automation.WakeIngressPolicyCard.kaf4bc45c"} description={"components.automation.WakeIngressPolicyCard.description"} className="h-full">
            <div className="space-y-4">
                <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-6 text-slate-600">
                    {t("components.automation.WakeIngressPolicyCard.k175f5ac7")}
                </div>

                <label className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                    <div className="pr-4">
                        <div className="text-sm font-medium text-slate-900">{t("components.automation.WakeIngressPolicyCard.k0d5eba4e")}</div>
                        <div className="text-xs text-slate-500">{t("components.automation.WakeIngressPolicyCard.kdc65f966")}</div>
                    </div>
                    <Switch checked={config.allowNudgeWithoutTarget} disabled={loading || saving} onCheckedChange={(checked) => setConfig((prev) => ({ ...prev, allowNudgeWithoutTarget: checked }))}/>
                </label>

                <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                        <Label>{t("components.automation.WakeIngressPolicyCard.k3301f7ea")}</Label>
                        <Select value={config.defaultAttachPolicy} onValueChange={(value: WakeIngressPolicies["defaultAttachPolicy"]) => setConfig((prev) => ({ ...prev, defaultAttachPolicy: value }))} disabled={loading || saving}>
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
                        <Label>{t("components.automation.WakeIngressPolicyCard.k279e4034")}</Label>
                        <Input value={sourceRuntimeInput} disabled={loading || saving} onChange={(event) => setSourceRuntimeInput(event.target.value)} placeholder="cron, hook, plugin_host, network_supervisor"/>
                    </div>
                </div>

                <div className="flex items-center justify-end gap-3">
                    <Button variant="outline" onClick={() => void loadConfig()} disabled={loading || saving}>
                        {t("components.automation.WakeIngressPolicyCard.k876e8c06")}
                    </Button>
                    <Button onClick={() => void handleSave()} disabled={loading || saving}>
                        {saving ? t("components.automation.WakeIngressPolicyCard.kb5d22555") : t("components.automation.WakeIngressPolicyCard.kcf3d8163")}
                    </Button>
                </div>
            </div>
        </ConfigCard>);
}
