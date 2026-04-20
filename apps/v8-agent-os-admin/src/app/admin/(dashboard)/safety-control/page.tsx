"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { SafetyGuardianPanel } from "@/components/runtime/SafetyGuardianPanel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";

type MachinePosture = "dedicated_runtime_host" | "developer_mixed_host";

type ModelOption = {
    modelId: string;
    name: string;
    type: string;
    provider?: { name?: string };
};

type SafetyData = {
    enabled: boolean;
    machinePosture: MachinePosture;
    skillRules?: {
        declarationVerdict?: string;
        localSecretReadVerdict?: string;
        browserProfileAccessVerdict?: Record<string, string>;
        binaryPayloadVerdict?: string;
        llmReviewEnabledFor?: string[];
    };
    networkMutationRules?: {
        defaultExternalMutationVerdict?: Record<string, string>;
        sensitivePayloadVerdict?: string;
        credentialExfiltrationVerdict?: string;
    };
    computerUseRules?: {
        defaultMutationVerdict?: Record<string, string>;
        hotkeyLifecycleVerdict?: string;
        destructiveKeywordVerdict?: string;
    };
    systemIntegrityRules?: {
        packageInstallVerdict?: Record<string, string>;
        destructiveCommandVerdict?: string;
    };
    v8IntegrityRules?: {
        protectedConfigWriteVerdict?: string;
        protectedRuntimeProcessVerdict?: string;
    };
    channelGroupGuard?: {
        enabled?: boolean;
        allowlistOnly?: boolean;
        requireMention?: boolean;
        auditOnly?: boolean;
    };
    modelBindings?: {
        safetyReviewModel?: string;
    };
    governancePolicies?: {
        machinePosture?: string;
        governanceTargets?: string[];
        skillStrategy?: string;
    };
    runtimeSummary?: {
        machinePosture?: string;
        safetyReviewModel?: string | null;
        llmBound?: boolean;
        auditCount?: number;
        reviewCount?: number;
        blockCount?: number;
        verdictDistribution?: Record<string, number>;
    };
    skillScanSummary?: {
        enabled?: boolean;
        verdictDistribution?: Record<string, number>;
        recentSkillScans?: Array<{
            skillName?: string;
            verdict?: string;
            confidence?: number;
            auditId?: string;
            timestamp?: string;
            reasons?: string[];
        }>;
    };
};

const PRESET_OPTIONS = [
    {
        key: "dedicated_runtime_host",
        title:"app.admin.dashboard.safety.control.page.k5970446a",
        description:"app.admin.dashboard.safety.control.page.k4b26c5fc",
    },
    {
        key: "developer_mixed_host",
        title: "developer_mixed_host",
        description:"app.admin.dashboard.safety.control.page.k6a2115f2",
    },
    {
        key: "locked_down_sensitive",
        title: "locked_down_sensitive",
        description:"app.admin.dashboard.safety.control.page.k24c3f9e9",
    },
] as const;

function normalizeSafetyData(input: SafetyData): SafetyData {
    return {
        ...input,
        machinePosture: input.machinePosture === "developer_mixed_host" ? "developer_mixed_host" : "dedicated_runtime_host",
        skillRules: {
            declarationVerdict: input.skillRules?.declarationVerdict || "audit",
            localSecretReadVerdict: input.skillRules?.localSecretReadVerdict || "review",
            browserProfileAccessVerdict: {
                dedicated_runtime_host: input.skillRules?.browserProfileAccessVerdict?.dedicated_runtime_host || "review",
                developer_mixed_host: input.skillRules?.browserProfileAccessVerdict?.developer_mixed_host || "block",
            },
            binaryPayloadVerdict: input.skillRules?.binaryPayloadVerdict || "review",
            llmReviewEnabledFor: Array.isArray(input.skillRules?.llmReviewEnabledFor) ? input.skillRules?.llmReviewEnabledFor : ["review"],
        },
        networkMutationRules: {
            defaultExternalMutationVerdict: {
                dedicated_runtime_host: input.networkMutationRules?.defaultExternalMutationVerdict?.dedicated_runtime_host || "audit",
                developer_mixed_host: input.networkMutationRules?.defaultExternalMutationVerdict?.developer_mixed_host || "review",
            },
            sensitivePayloadVerdict: input.networkMutationRules?.sensitivePayloadVerdict || "review",
            credentialExfiltrationVerdict: input.networkMutationRules?.credentialExfiltrationVerdict || "block",
        },
        computerUseRules: {
            defaultMutationVerdict: {
                dedicated_runtime_host: input.computerUseRules?.defaultMutationVerdict?.dedicated_runtime_host || "audit",
                developer_mixed_host: input.computerUseRules?.defaultMutationVerdict?.developer_mixed_host || "review",
            },
            hotkeyLifecycleVerdict: input.computerUseRules?.hotkeyLifecycleVerdict || "review",
            destructiveKeywordVerdict: input.computerUseRules?.destructiveKeywordVerdict || "block",
        },
        systemIntegrityRules: {
            packageInstallVerdict: {
                dedicated_runtime_host: input.systemIntegrityRules?.packageInstallVerdict?.dedicated_runtime_host || "audit",
                developer_mixed_host: input.systemIntegrityRules?.packageInstallVerdict?.developer_mixed_host || "review",
            },
            destructiveCommandVerdict: input.systemIntegrityRules?.destructiveCommandVerdict || "block",
        },
        v8IntegrityRules: {
            protectedConfigWriteVerdict: input.v8IntegrityRules?.protectedConfigWriteVerdict || "review",
            protectedRuntimeProcessVerdict: input.v8IntegrityRules?.protectedRuntimeProcessVerdict || "block",
        },
        channelGroupGuard: {
            enabled: Boolean(input.channelGroupGuard?.enabled),
            allowlistOnly: Boolean(input.channelGroupGuard?.allowlistOnly),
            requireMention: Boolean(input.channelGroupGuard?.requireMention),
            auditOnly: Boolean(input.channelGroupGuard?.auditOnly),
        },
        runtimeSummary: {
            machinePosture: input.runtimeSummary?.machinePosture || input.machinePosture,
            safetyReviewModel: input.runtimeSummary?.safetyReviewModel || null,
            llmBound: Boolean(input.runtimeSummary?.llmBound),
            auditCount: Number(input.runtimeSummary?.auditCount || 0),
            reviewCount: Number(input.runtimeSummary?.reviewCount || 0),
            blockCount: Number(input.runtimeSummary?.blockCount || 0),
            verdictDistribution: input.runtimeSummary?.verdictDistribution || {},
        },
        skillScanSummary: {
            enabled: Boolean(input.skillScanSummary?.enabled),
            verdictDistribution: input.skillScanSummary?.verdictDistribution || {},
            recentSkillScans: Array.isArray(input.skillScanSummary?.recentSkillScans) ? input.skillScanSummary?.recentSkillScans : [],
        },
    };
}

function applyPreset(config: SafetyData, preset: (typeof PRESET_OPTIONS)[number]["key"]): SafetyData {
    const next = normalizeSafetyData(structuredClone(config));
    next.enabled = true;

    if (preset === "dedicated_runtime_host") {
        next.machinePosture = "dedicated_runtime_host";
        next.skillRules!.declarationVerdict = "audit";
        next.skillRules!.localSecretReadVerdict = "review";
        next.networkMutationRules!.defaultExternalMutationVerdict!.dedicated_runtime_host = "audit";
        next.computerUseRules!.defaultMutationVerdict!.dedicated_runtime_host = "audit";
        next.networkMutationRules!.sensitivePayloadVerdict = "review";
        next.v8IntegrityRules!.protectedConfigWriteVerdict = "review";
        return next;
    }

    next.machinePosture = "developer_mixed_host";
    next.networkMutationRules!.defaultExternalMutationVerdict!.developer_mixed_host = "review";
    next.computerUseRules!.defaultMutationVerdict!.developer_mixed_host = "review";
    next.skillRules!.browserProfileAccessVerdict!.developer_mixed_host = "block";
    next.systemIntegrityRules!.packageInstallVerdict!.developer_mixed_host = "review";

    if (preset === "locked_down_sensitive") {
        next.networkMutationRules!.sensitivePayloadVerdict = "block";
        next.computerUseRules!.hotkeyLifecycleVerdict = "block";
        next.v8IntegrityRules!.protectedConfigWriteVerdict = "block";
    }

    return next;
}

function detectPreset(config: SafetyData) {
    if (
        config.machinePosture === "developer_mixed_host" &&
        config.networkMutationRules?.sensitivePayloadVerdict === "block" &&
        config.computerUseRules?.hotkeyLifecycleVerdict === "block" &&
        config.v8IntegrityRules?.protectedConfigWriteVerdict === "block"
    ) {
        return "locked_down_sensitive";
    }
    return config.machinePosture;
}

export default function SafetyControlPage() {
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<SafetyData> | null>(null);
    const [models, setModels] = useState<ModelOption[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [preset, setPreset] = useState<(typeof PRESET_OPTIONS)[number]["key"]>("dedicated_runtime_host");

    const loadConfig = async () => {
        setLoading(true);
        try {
            const [next, modelList] = await Promise.all([
                fetchConfigDomain<SafetyData>("safety"),
                fetch("/api/models", { cache: "no-store" }).then((response) => response.json().catch(() => [])),
            ]);
            const normalized = normalizeSafetyData(next.data);
            setEnvelope({ ...next, data: normalized });
            setModels(Array.isArray(modelList) ? modelList : []);
            setPreset(detectPreset(normalized));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadConfig();
    }, []);

    const llmModels = useMemo(
        () => models.filter((model) => ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes((model.type || "").toUpperCase())),
        [models],
    );

    const summary = useMemo(() => {
        const data = envelope?.data;
        const runtimeSummary = data?.runtimeSummary || {};
        const skillScanSummary = data?.skillScanSummary || {};
        return {
            posture: data?.machinePosture || "dedicated_runtime_host",
            reviewModel: data?.modelBindings?.safetyReviewModel || "未绑定",
            auditCount: Number(runtimeSummary.auditCount || 0),
            reviewCount: Number(runtimeSummary.reviewCount || 0),
            blockCount: Number(runtimeSummary.blockCount || 0),
            skillDistribution: skillScanSummary.verdictDistribution || {},
            recentSkillScans: skillScanSummary.recentSkillScans || [],
        };
    }, [envelope]);

    const saveData = async (nextData: SafetyData) => {
        if (!envelope) return;
        setSaving(true);
        try {
            const next = await saveConfigDomain<SafetyData>("safety", { data: nextData });
            const normalized = normalizeSafetyData(next.data);
            setEnvelope({ ...next, data: normalized });
            setPreset(detectPreset(normalized));
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

    const data = envelope.data;
    const activePreset = PRESET_OPTIONS.find((item) => item.key === preset) || PRESET_OPTIONS[0];

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="app.admin.dashboard.safety.control.page.k8f467cf5"
                description="app.admin.dashboard.safety.control.page.k65868ff2"
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void saveData(data)} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}
                            立即保存
                        </Button>
                    </div>
                }
            />

            <div className="space-y-6">
                <DomainSummaryStrip
                    items={[
                        { label: "Machine Posture", value: summary.posture },
                        { label: "Audit / Review / Block", value: `${summary.auditCount} / ${summary.reviewCount} / ${summary.blockCount}` },
                        {
                            label:"app.admin.dashboard.safety.control.page.k87b50116",
                            value: `audit ${Number(summary.skillDistribution.audit || 0)} · review ${Number(summary.skillDistribution.review || 0)} · block ${Number(summary.skillDistribution.block || 0)}`,
                        },
                        { label: "Safety Review Model", value: summary.reviewModel },
                    ]}
                />

                <StatusNotice
                    tone={data.machinePosture === "developer_mixed_host" ? "warning" : "success"}
                    title={data.machinePosture === "developer_mixed_host" ? "当前是开发机混用姿态" : "当前是专用运行机姿态"}
                    description={
                        data.machinePosture === "developer_mixed_host"
                            ? "浏览器 profile、本地 secret 和某些外部 mutating HTTP 会更严格，适合当前这种混有私人日常使用的开发机。"
                            : "正常依赖安装、正常 API 写操作和声明式 skill 配置默认记 audit，不会被旧的一刀切规则过度阻断。"
                    }
                />

                <Card className="rounded-2xl border-slate-200 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-base">Canonical 控制面</CardTitle>
                        <CardDescription>主页面负责 posture / preset / review model 心智；高级面板只负责细粒度规则编辑。</CardDescription>
                    </CardHeader>
                    <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                        <div className="space-y-2">
                            <Label>Machine Posture</Label>
                            <Select
                                value={data.machinePosture}
                                onValueChange={(next) =>
                                    setEnvelope((previous) =>
                                        previous
                                            ? {
                                                  ...previous,
                                                  data: normalizeSafetyData({
                                                      ...previous.data,
                                                      machinePosture: next as MachinePosture,
                                                  }),
                                              }
                                            : previous,
                                    )
                                }
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="dedicated_runtime_host">dedicated_runtime_host（推荐）</SelectItem>
                                    <SelectItem value="developer_mixed_host">developer_mixed_host</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>Preset</Label>
                            <Select
                                value={preset}
                                onValueChange={(next) => {
                                    const presetKey = next as (typeof PRESET_OPTIONS)[number]["key"];
                                    setPreset(presetKey);
                                    setEnvelope((previous) =>
                                        previous
                                            ? {
                                                  ...previous,
                                                  data: normalizeSafetyData(applyPreset(previous.data, presetKey)),
                                              }
                                            : previous,
                                    );
                                }}
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {PRESET_OPTIONS.map((item) => (
                                        <SelectItem key={item.key} value={item.key}>
                                            {item.title}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>Safety Review Model</Label>
                            <Select
                                value={String(data.modelBindings?.safetyReviewModel || "__none__")}
                                onValueChange={(next) =>
                                    setEnvelope((previous) =>
                                        previous
                                            ? {
                                                  ...previous,
                                                  data: {
                                                      ...previous.data,
                                                      modelBindings: {
                                                          ...(previous.data.modelBindings || {}),
                                                          safetyReviewModel: next === "__none__" ? "" : next,
                                                      },
                                                  },
                                              }
                                            : previous,
                                    )
                                }
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="__none__">未绑定</SelectItem>
                                    {llmModels.map((model) => (
                                        <SelectItem key={model.modelId} value={model.modelId}>
                                            {model.name || model.modelId}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex items-center justify-between rounded-2xl border border-slate-200 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">启用 Safety Guardian</div>
                                <div className="text-xs leading-5 text-slate-500">关闭后只暂停阻断与审批，不会抹掉当前 posture / rules。</div>
                            </div>
                            <Switch
                                checked={Boolean(data.enabled)}
                                onCheckedChange={(checked) =>
                                    setEnvelope((previous) =>
                                        previous
                                            ? {
                                                  ...previous,
                                                  data: { ...previous.data, enabled: checked },
                                              }
                                            : previous,
                                    )
                                }
                            />
                        </div>
                    </CardContent>
                </Card>

                <Card className="rounded-2xl border-slate-200 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-base">当前边界摘要</CardTitle>
                        <CardDescription>{activePreset.description}</CardDescription>
                    </CardHeader>
                    <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                        <div className="rounded-2xl border border-slate-200 p-4">
                            <div className="text-sm font-medium text-slate-900">Skill 治理</div>
                            <div className="mt-2 text-sm leading-6 text-slate-600">
                                声明式依赖：<span className="font-medium text-slate-900">{data.skillRules?.declarationVerdict}</span>
                                <br />
                                本地 secret：<span className="font-medium text-slate-900">{data.skillRules?.localSecretReadVerdict}</span>
                                <br />
                                浏览器资料（开发机）：<span className="font-medium text-slate-900">{data.skillRules?.browserProfileAccessVerdict?.developer_mixed_host}</span>
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 p-4">
                            <div className="text-sm font-medium text-slate-900">网络与外部变更</div>
                            <div className="mt-2 text-sm leading-6 text-slate-600">
                                专用机默认：<span className="font-medium text-slate-900">{data.networkMutationRules?.defaultExternalMutationVerdict?.dedicated_runtime_host}</span>
                                <br />
                                开发机默认：<span className="font-medium text-slate-900">{data.networkMutationRules?.defaultExternalMutationVerdict?.developer_mixed_host}</span>
                                <br />
                                敏感 payload：<span className="font-medium text-slate-900">{data.networkMutationRules?.sensitivePayloadVerdict}</span>
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 p-4">
                            <div className="text-sm font-medium text-slate-900">Computer Use</div>
                            <div className="mt-2 text-sm leading-6 text-slate-600">
                                专用机动作：<span className="font-medium text-slate-900">{data.computerUseRules?.defaultMutationVerdict?.dedicated_runtime_host}</span>
                                <br />
                                开发机动作：<span className="font-medium text-slate-900">{data.computerUseRules?.defaultMutationVerdict?.developer_mixed_host}</span>
                                <br />
                                生命周期热键：<span className="font-medium text-slate-900">{data.computerUseRules?.hotkeyLifecycleVerdict}</span>
                            </div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 p-4">
                            <div className="text-sm font-medium text-slate-900">系统 / V8 完整性</div>
                            <div className="mt-2 text-sm leading-6 text-slate-600">
                                依赖安装（专用机）：<span className="font-medium text-slate-900">{data.systemIntegrityRules?.packageInstallVerdict?.dedicated_runtime_host}</span>
                                <br />
                                配置写入：<span className="font-medium text-slate-900">{data.v8IntegrityRules?.protectedConfigWriteVerdict}</span>
                                <br />
                                核心进程：<span className="font-medium text-slate-900">{data.v8IntegrityRules?.protectedRuntimeProcessVerdict}</span>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="rounded-2xl border-slate-200 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-base">Recent Skill Scan Summary</CardTitle>
                        <CardDescription>这里展示的是 skill 供应链治理结果，不再把旧 severity 级别或预读阻断当成主故事线。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {summary.recentSkillScans.length === 0 ? (
                            <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">最近没有新的 skill scan 记录。</div>
                        ) : (
                            summary.recentSkillScans.map((item, index) => (
                                <div key={`${item.auditId || item.skillName || "skill"}-${index}`} className="rounded-2xl border border-slate-200 p-4">
                                    <div className="flex flex-wrap items-center gap-3">
                                        <div className="text-sm font-medium text-slate-900">{item.skillName || "未知 Skill"}</div>
                                        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{item.verdict || "unknown"}</div>
                                        {item.confidence != null ? <div className="text-xs text-slate-500">confidence {item.confidence}</div> : null}
                                    </div>
                                    {Array.isArray(item.reasons) && item.reasons.length > 0 ? (
                                        <ul className="mt-3 space-y-1 text-sm leading-6 text-slate-600">
                                            {item.reasons.map((reason) => (
                                                <li key={reason}>- {reason}</li>
                                            ))}
                                        </ul>
                                    ) : null}
                                </div>
                            ))
                        )}
                    </CardContent>
                </Card>

                <AdvancedSection title="app.admin.dashboard.safety.control.page.k4f8c7149" defaultOpen={false}>
                    <SafetyGuardianPanel />
                </AdvancedSection>

                <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} warnings={envelope.warnings} />
            </div>
        </AdminPageShell>
    );
}
