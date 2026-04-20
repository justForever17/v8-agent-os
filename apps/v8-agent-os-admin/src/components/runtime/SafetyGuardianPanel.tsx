"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Save, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

type MachinePosture = "dedicated_runtime_host" | "developer_mixed_host";

type SafetyGuardianConfig = {
    enabled: boolean;
    machinePosture: MachinePosture;
    skillRules?: {
        declarationVerdict?: "allow" | "audit" | "review";
        localSecretReadVerdict?: "audit" | "review" | "block";
        binaryPayloadVerdict?: "audit" | "review" | "block";
        browserProfileAccessVerdict?: Record<MachinePosture, "review" | "block">;
        llmReviewEnabledFor?: Array<"review" | "block">;
    };
    networkMutationRules?: {
        defaultExternalMutationVerdict?: Record<MachinePosture, "audit" | "review">;
        sensitivePayloadVerdict?: "audit" | "review" | "block";
        credentialExfiltrationVerdict?: "review" | "block";
    };
    computerUseRules?: {
        defaultMutationVerdict?: Record<MachinePosture, "audit" | "review">;
        hotkeyLifecycleVerdict?: "audit" | "review" | "block";
        destructiveKeywordVerdict?: "review" | "block";
    };
    systemIntegrityRules?: {
        packageInstallVerdict?: Record<MachinePosture, "audit" | "review">;
        destructiveCommandVerdict?: "review" | "block";
    };
    v8IntegrityRules?: {
        protectedConfigWriteVerdict?: "audit" | "review" | "block";
        protectedRuntimeProcessVerdict?: "review" | "block";
    };
    channelGroupGuard?: {
        enabled?: boolean;
        allowlistOnly?: boolean;
        requireMention?: boolean;
        auditOnly?: boolean;
    };
    [key: string]: unknown;
};

const DEFAULT_CONFIG: SafetyGuardianConfig = {
    enabled: true,
    machinePosture: "dedicated_runtime_host",
    skillRules: {
        declarationVerdict: "audit",
        localSecretReadVerdict: "review",
        binaryPayloadVerdict: "review",
        browserProfileAccessVerdict: {
            dedicated_runtime_host: "review",
            developer_mixed_host: "block",
        },
        llmReviewEnabledFor: ["review"],
    },
    networkMutationRules: {
        defaultExternalMutationVerdict: {
            dedicated_runtime_host: "audit",
            developer_mixed_host: "review",
        },
        sensitivePayloadVerdict: "review",
        credentialExfiltrationVerdict: "block",
    },
    computerUseRules: {
        defaultMutationVerdict: {
            dedicated_runtime_host: "audit",
            developer_mixed_host: "review",
        },
        hotkeyLifecycleVerdict: "review",
        destructiveKeywordVerdict: "block",
    },
    systemIntegrityRules: {
        packageInstallVerdict: {
            dedicated_runtime_host: "audit",
            developer_mixed_host: "review",
        },
        destructiveCommandVerdict: "block",
    },
    v8IntegrityRules: {
        protectedConfigWriteVerdict: "review",
        protectedRuntimeProcessVerdict: "block",
    },
    channelGroupGuard: {
        enabled: false,
        allowlistOnly: false,
        requireMention: false,
        auditOnly: false,
    },
};

function normalizeConfig(data: unknown): SafetyGuardianConfig {
    const raw = data && typeof data === "object" ? (data as SafetyGuardianConfig) : DEFAULT_CONFIG;
    const browserProfileAccessVerdict: Record<MachinePosture, "review" | "block"> = {
        dedicated_runtime_host:
            raw.skillRules?.browserProfileAccessVerdict?.dedicated_runtime_host ??
            DEFAULT_CONFIG.skillRules!.browserProfileAccessVerdict!.dedicated_runtime_host,
        developer_mixed_host:
            raw.skillRules?.browserProfileAccessVerdict?.developer_mixed_host ??
            DEFAULT_CONFIG.skillRules!.browserProfileAccessVerdict!.developer_mixed_host,
    };
    const defaultExternalMutationVerdict: Record<MachinePosture, "audit" | "review"> = {
        dedicated_runtime_host:
            raw.networkMutationRules?.defaultExternalMutationVerdict?.dedicated_runtime_host ??
            DEFAULT_CONFIG.networkMutationRules!.defaultExternalMutationVerdict!.dedicated_runtime_host,
        developer_mixed_host:
            raw.networkMutationRules?.defaultExternalMutationVerdict?.developer_mixed_host ??
            DEFAULT_CONFIG.networkMutationRules!.defaultExternalMutationVerdict!.developer_mixed_host,
    };
    const defaultMutationVerdict: Record<MachinePosture, "audit" | "review"> = {
        dedicated_runtime_host:
            raw.computerUseRules?.defaultMutationVerdict?.dedicated_runtime_host ??
            DEFAULT_CONFIG.computerUseRules!.defaultMutationVerdict!.dedicated_runtime_host,
        developer_mixed_host:
            raw.computerUseRules?.defaultMutationVerdict?.developer_mixed_host ??
            DEFAULT_CONFIG.computerUseRules!.defaultMutationVerdict!.developer_mixed_host,
    };
    const packageInstallVerdict: Record<MachinePosture, "audit" | "review"> = {
        dedicated_runtime_host:
            raw.systemIntegrityRules?.packageInstallVerdict?.dedicated_runtime_host ??
            DEFAULT_CONFIG.systemIntegrityRules!.packageInstallVerdict!.dedicated_runtime_host,
        developer_mixed_host:
            raw.systemIntegrityRules?.packageInstallVerdict?.developer_mixed_host ??
            DEFAULT_CONFIG.systemIntegrityRules!.packageInstallVerdict!.developer_mixed_host,
    };
    return {
        ...DEFAULT_CONFIG,
        ...raw,
        machinePosture: raw.machinePosture === "developer_mixed_host" ? "developer_mixed_host" : "dedicated_runtime_host",
        skillRules: {
            ...DEFAULT_CONFIG.skillRules,
            ...(raw.skillRules || {}),
            browserProfileAccessVerdict,
        },
        networkMutationRules: {
            ...DEFAULT_CONFIG.networkMutationRules,
            ...(raw.networkMutationRules || {}),
            defaultExternalMutationVerdict,
        },
        computerUseRules: {
            ...DEFAULT_CONFIG.computerUseRules,
            ...(raw.computerUseRules || {}),
            defaultMutationVerdict,
        },
        systemIntegrityRules: {
            ...DEFAULT_CONFIG.systemIntegrityRules,
            ...(raw.systemIntegrityRules || {}),
            packageInstallVerdict,
        },
        v8IntegrityRules: {
            ...DEFAULT_CONFIG.v8IntegrityRules,
            ...(raw.v8IntegrityRules || {}),
        },
        channelGroupGuard: {
            ...DEFAULT_CONFIG.channelGroupGuard,
            ...(raw.channelGroupGuard || {}),
        },
    };
}

function VerdictSelect<T extends string>({
    value,
    onChange,
    options,
}: {
    value: T;
    onChange: (next: T) => void;
    options: Array<{ value: T; label: string }>;
}) {
    return (
        <Select value={value} onValueChange={(next) => onChange(next as T)}>
            <SelectTrigger>
                <SelectValue />
            </SelectTrigger>
            <SelectContent>
                {options.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                        {option.label}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}

export function SafetyGuardianPanel() {
    const [config, setConfig] = useState<SafetyGuardianConfig>(DEFAULT_CONFIG);
    const [rawJson, setRawJson] = useState(JSON.stringify(DEFAULT_CONFIG, null, 2));
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [parseError, setParseError] = useState("");

    const syncConfig = useCallback((next: unknown) => {
        const normalized = normalizeConfig(next);
        setConfig(normalized);
        setRawJson(JSON.stringify(normalized, null, 2));
        setParseError("");
    }, []);

    const loadConfig = useCallback(async () => {
        setLoading(true);
        try {
            const response = await fetch("/api/settings/safety-guardian", { cache: "no-store" });
            const payload = response.ok ? await response.json().catch(() => ({})) : {};
            syncConfig(payload);
        } finally {
            setLoading(false);
        }
    }, [syncConfig]);

    useEffect(() => {
        void loadConfig();
    }, [loadConfig]);

    const summaryBadges = useMemo(
        () => [
            config.enabled ? "Guardian 已启用" : "Guardian 已禁用",
            `posture=${config.machinePosture}`,
            `skill=${config.skillRules?.declarationVerdict ?? "audit"} / ${config.skillRules?.localSecretReadVerdict ?? "review"}`,
        ],
        [config],
    );

    const updateAndSync = (updater: (previous: SafetyGuardianConfig) => SafetyGuardianConfig) => {
        setConfig((previous) => {
            const next = normalizeConfig(updater(previous));
            setRawJson(JSON.stringify(next, null, 2));
            setParseError("");
            return next;
        });
    };

    const saveConfig = async () => {
        setSaving(true);
        try {
            const parsed = JSON.parse(rawJson);
            setParseError("");
            const response = await fetch("/api/settings/safety-guardian", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(parsed),
            });
            const payload = response.ok ? await response.json().catch(() => ({})) : {};
            syncConfig(payload?.config || parsed);
        } catch (error) {
            setParseError(error instanceof Error ? error.message : "高级配置 JSON 解析失败");
        } finally {
            setSaving(false);
        }
    };

    if (loading) {
        return (
            <Card className="rounded-2xl border-slate-200 shadow-sm">
                <CardContent className="py-8 text-sm text-slate-500">正在读取高级 Safety 配置…</CardContent>
            </Card>
        );
    }

    return (
        <div className="space-y-6">
            <Card className="rounded-2xl border-slate-200 shadow-sm">
                <CardHeader>
                    <div className="flex items-start justify-between gap-4">
                        <div className="space-y-2">
                            <CardTitle className="flex items-center gap-2 text-base">
                                <ShieldCheck className="h-4 w-4 text-sky-600" />
                                Safety Guardian 高级规则编辑器
                            </CardTitle>
                            <CardDescription>主页面负责 posture 和控制面心智；这里保留关键 verdict 快捷编辑，并允许直接调整完整 JSON 规则。</CardDescription>
                        </div>
                        <div className="flex items-center gap-2">
                            <Button variant="outline" size="sm" onClick={() => void loadConfig()} disabled={saving}>
                                <RefreshCw className="mr-2 h-4 w-4" />
                                刷新
                            </Button>
                            <Button size="sm" onClick={() => void saveConfig()} disabled={saving}>
                                <Save className="mr-2 h-4 w-4" />
                                {saving ? "保存中…" : "保存规则"}
                            </Button>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                    {summaryBadges.map((badge) => (
                        <Badge key={badge} variant="outline">
                            {badge}
                        </Badge>
                    ))}
                </CardContent>
            </Card>

            <Card className="rounded-2xl border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle className="text-base">关键 posture / verdict</CardTitle>
                    <CardDescription>这些是最常调的边界，先在这里收正，再决定是否需要改完整 JSON。</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                    <div className="space-y-2">
                        <Label>Machine Posture</Label>
                        <VerdictSelect<MachinePosture>
                            value={config.machinePosture}
                            onChange={(next) => updateAndSync((previous) => ({ ...previous, machinePosture: next }))}
                            options={[
                                { value: "dedicated_runtime_host", label:"components.runtime.SafetyGuardianPanel.k5970446a" },
                                { value: "developer_mixed_host", label: "developer_mixed_host" },
                            ]}
                        />
                    </div>
                    <div className="flex items-center justify-between rounded-xl border border-slate-200 px-4 py-3">
                        <div className="space-y-1">
                            <div className="text-sm font-medium text-slate-900">启用 Guardian</div>
                            <div className="text-xs text-slate-500">关闭后只暂停拦截，不会删除策略本身。</div>
                        </div>
                        <Switch checked={config.enabled} onCheckedChange={(checked) => updateAndSync((previous) => ({ ...previous, enabled: checked }))} />
                    </div>
                    <div className="space-y-2">
                        <Label>声明式 Key / Env</Label>
                        <VerdictSelect<"allow" | "audit" | "review">
                            value={config.skillRules?.declarationVerdict ?? "audit"}
                            onChange={(next) =>
                                updateAndSync((previous) => ({
                                    ...previous,
                                    skillRules: { ...previous.skillRules, declarationVerdict: next },
                                }))
                            }
                            options={[
                                { value: "allow", label: "allow" },
                                { value: "audit", label: "audit" },
                                { value: "review", label: "review" },
                            ]}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label>本地 Secret 读取</Label>
                        <VerdictSelect<"audit" | "review" | "block">
                            value={config.skillRules?.localSecretReadVerdict ?? "review"}
                            onChange={(next) =>
                                updateAndSync((previous) => ({
                                    ...previous,
                                    skillRules: { ...previous.skillRules, localSecretReadVerdict: next },
                                }))
                            }
                            options={[
                                { value: "audit", label: "audit" },
                                { value: "review", label: "review" },
                                { value: "block", label: "block" },
                            ]}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label>专用机外部 API 变更</Label>
                        <VerdictSelect<"audit" | "review">
                            value={config.networkMutationRules?.defaultExternalMutationVerdict?.dedicated_runtime_host ?? "audit"}
                            onChange={(next) =>
                                updateAndSync((previous) => ({
                                    ...previous,
                                    networkMutationRules: {
                                        ...previous.networkMutationRules,
                                        defaultExternalMutationVerdict: {
                                            dedicated_runtime_host: next,
                                            developer_mixed_host:
                                                previous.networkMutationRules?.defaultExternalMutationVerdict?.developer_mixed_host ?? "review",
                                        },
                                    },
                                }))
                            }
                            options={[
                                { value: "audit", label: "audit" },
                                { value: "review", label: "review" },
                            ]}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label>专用机 computer_use 动作</Label>
                        <VerdictSelect<"audit" | "review">
                            value={config.computerUseRules?.defaultMutationVerdict?.dedicated_runtime_host ?? "audit"}
                            onChange={(next) =>
                                updateAndSync((previous) => ({
                                    ...previous,
                                    computerUseRules: {
                                        ...previous.computerUseRules,
                                        defaultMutationVerdict: {
                                            dedicated_runtime_host: next,
                                            developer_mixed_host:
                                                previous.computerUseRules?.defaultMutationVerdict?.developer_mixed_host ?? "review",
                                        },
                                    },
                                }))
                            }
                            options={[
                                { value: "audit", label: "audit" },
                                { value: "review", label: "review" },
                            ]}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label>开发机浏览器资料访问</Label>
                        <VerdictSelect<"review" | "block">
                            value={config.skillRules?.browserProfileAccessVerdict?.developer_mixed_host ?? "block"}
                            onChange={(next) =>
                                updateAndSync((previous) => ({
                                    ...previous,
                                    skillRules: {
                                        ...previous.skillRules,
                                        browserProfileAccessVerdict: {
                                            dedicated_runtime_host:
                                                previous.skillRules?.browserProfileAccessVerdict?.dedicated_runtime_host ?? "review",
                                            developer_mixed_host: next,
                                        },
                                    },
                                }))
                            }
                            options={[
                                { value: "review", label: "review" },
                                { value: "block", label: "block" },
                            ]}
                        />
                    </div>
                    <div className="flex items-center justify-between rounded-xl border border-slate-200 px-4 py-3">
                        <div className="space-y-1">
                            <div className="text-sm font-medium text-slate-900">群聊仅审计</div>
                            <div className="text-xs text-slate-500">保留旧开关，但后端已映射到正式 audit verdict。</div>
                        </div>
                        <Switch
                            checked={Boolean(config.channelGroupGuard?.auditOnly)}
                            onCheckedChange={(checked) =>
                                updateAndSync((previous) => ({
                                    ...previous,
                                    channelGroupGuard: { ...previous.channelGroupGuard, auditOnly: checked },
                                }))
                            }
                        />
                    </div>
                    <div className="flex items-center justify-between rounded-xl border border-slate-200 px-4 py-3">
                        <div className="space-y-1">
                            <div className="text-sm font-medium text-slate-900">启用 review 类 Skill 二阶段复审</div>
                            <div className="text-xs text-slate-500">只对恶意性仍有歧义的 finding 启用，不再把旧 severity 级别当主叙事。</div>
                        </div>
                        <Switch
                            checked={Boolean(config.skillRules?.llmReviewEnabledFor?.includes("review"))}
                            onCheckedChange={(checked) =>
                                updateAndSync((previous) => ({
                                    ...previous,
                                    skillRules: {
                                        ...previous.skillRules,
                                        llmReviewEnabledFor: checked ? ["review"] : [],
                                    },
                                }))
                            }
                        />
                    </div>
                </CardContent>
            </Card>

            <Card className="rounded-2xl border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle className="text-base">完整高级 JSON</CardTitle>
                    <CardDescription>所有旧 command/file/process/network/runtimeRules 以及新增 machinePosture、skillRules、networkMutationRules 都可以直接在这里调整。</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                    <Textarea value={rawJson} onChange={(event) => setRawJson(event.target.value)} className="min-h-[420px] font-mono text-xs" />
                    {parseError ? <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">{parseError}</div> : null}
                </CardContent>
            </Card>
        </div>
    );
}
