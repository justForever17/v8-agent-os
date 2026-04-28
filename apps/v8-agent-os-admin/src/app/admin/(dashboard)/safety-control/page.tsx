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
import { ModelSelect } from "@/components/models/ModelSelect";
import { SafetyGuardianPanel } from "@/components/runtime/SafetyGuardianPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";

type MachinePosture = "dedicated_runtime_host" | "developer_mixed_host";

type ModelOption = {
    id?: string;
    modelRef?: string;
    providerId?: string;
    modelId: string;
    name: string;
    type: string;
    provider?: { id?: string; name?: string };
    providerName?: string;
};

type SafetyApproval = {
    id: string;
    session_id?: string;
    run_id?: string;
    approval_kind?: string;
    status?: string;
    created_at?: string;
    question?: string;
    riskCode?: string;
    verdict?: string;
    reason?: string;
    allowlistCandidate?: Record<string, unknown> | null;
};

type SkillSafetyReview = {
    id: string;
    skill_name?: string;
    skill_id?: string;
    skill_path?: string;
    static_verdict?: string;
    effective_verdict?: string;
    user_override?: string | null;
    disabled?: boolean;
    reasons?: string[];
    flaggedFiles?: Array<Record<string, unknown>>;
    updated_at?: string;
};

type SafetyAllowlistEntry = {
    id: string;
    normalized_target_label?: string;
    path_plane?: string;
    runtime_source?: string;
    action?: string;
    risk_code?: string;
    enabled?: boolean;
    updated_at?: string;
};

type SafetyDecisionEvent = {
    id?: string;
    timestamp?: string;
    action?: string;
    status?: string;
    verdict?: string;
    riskCode?: string;
    runtimeSource?: string;
    subject?: string;
    reason?: string;
    decodedPreview?: unknown;
    downloadHosts?: string[];
};

type SafetyDashboard = {
    pendingSafetyApprovals?: SafetyApproval[];
    skillSafetyReviews?: SkillSafetyReview[];
    allowlistEntries?: SafetyAllowlistEntry[];
    recentDecisions?: SafetyDecisionEvent[];
    summary?: {
        pendingSafetyApprovals?: number;
        skillReviews?: number;
        activeAllowlist?: number;
        recentDecisions?: number;
        verdictCounts?: Record<string, number>;
        riskCounts?: Record<string, number>;
    };
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
    const [dashboard, setDashboard] = useState<SafetyDashboard | null>(null);
    const [governanceBusy, setGovernanceBusy] = useState<string | null>(null);
    const [rememberAllowlist, setRememberAllowlist] = useState<Record<string, boolean>>({});

    const loadConfig = async () => {
        setLoading(true);
        try {
            const [next, modelList, safetyDashboard] = await Promise.all([
                fetchConfigDomain<SafetyData>("safety"),
                fetch("/api/models", { cache: "no-store" }).then((response) => response.json().catch(() => [])),
                fetch("/api/safety/dashboard?limit=80", { cache: "no-store" }).then((response) => response.json().catch(() => ({}))),
            ]);
            const normalized = normalizeSafetyData(next.data);
            setEnvelope({ ...next, data: normalized });
            setModels(Array.isArray(modelList) ? modelList : []);
            setDashboard(safetyDashboard && typeof safetyDashboard === "object" ? safetyDashboard : {});
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

    const handleApprovalAction = async (approvalId: string, approve: boolean) => {
        const busyKey = `approval:${approve ? "approve" : "reject"}:${approvalId}`;
        setGovernanceBusy(busyKey);
        try {
            const response = await fetch(`/api/approvals/${encodeURIComponent(approvalId)}/${approve ? "approve" : "reject"}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    response: {
                        approved: approve,
                        answer: approve ? "Approved from SafetyRuntime governance." : "Rejected from SafetyRuntime governance.",
                        persistSafetyAllowlist: approve ? Boolean(rememberAllowlist[approvalId]) : false,
                    },
                }),
            });
            if (!response.ok) {
                throw new Error(`Approval action failed: ${response.status}`);
            }
            await loadConfig();
        } finally {
            setGovernanceBusy(null);
        }
    };

    const handleSkillSafetyAction = async (reviewId: string, action: "approve" | "disable" | "revoke" | "rescan") => {
        const busyKey = `skill:${action}:${reviewId}`;
        setGovernanceBusy(busyKey);
        try {
            const response = await fetch(`/api/skills/safety/reviews/${encodeURIComponent(reviewId)}/${action}`, { method: "POST" });
            if (!response.ok) {
                throw new Error(`Skill safety action failed: ${response.status}`);
            }
            await loadConfig();
        } finally {
            setGovernanceBusy(null);
        }
    };

    const handleAllowlistRevoke = async (entryId: string) => {
        const busyKey = `allowlist:revoke:${entryId}`;
        setGovernanceBusy(busyKey);
        try {
            const response = await fetch(`/api/safety/allowlist/${encodeURIComponent(entryId)}/revoke`, { method: "POST" });
            if (!response.ok) {
                throw new Error(`Allowlist revoke failed: ${response.status}`);
            }
            await loadConfig();
        } finally {
            setGovernanceBusy(null);
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
                            <ModelSelect
                                models={llmModels}
                                value={String(data.modelBindings?.safetyReviewModel || "__none__")}
                                emptyValue="__none__"
                                emptyLabel="未绑定"
                                placeholder="未绑定"
                                onValueChange={(next) =>
                                    setEnvelope((previous) =>
                                        previous
                                            ? {
                                                  ...previous,
                                                  data: {
                                                      ...previous.data,
                                                      modelBindings: {
                                                          ...(previous.data.modelBindings || {}),
                                                          safetyReviewModel: next,
                                                      },
                                                  },
                                              }
                                            : previous,
                                    )
                                }
                            />
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

                <AdvancedSection title="SafetyRuntime 观测与审批" description="真实运行观测、待审批项、Skill ledger 与可撤销长期授权集中在这里；空运行矩阵只保留在本地测试脚本。" defaultOpen={false}>
                    <div className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-4">
                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.18em] text-slate-400">pending</div>
                                <div className="mt-2 text-2xl font-semibold text-slate-950">{dashboard?.summary?.pendingSafetyApprovals ?? 0}</div>
                                <div className="text-xs text-slate-500">Safety approvals</div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.18em] text-slate-400">ledger</div>
                                <div className="mt-2 text-2xl font-semibold text-slate-950">{dashboard?.summary?.skillReviews ?? 0}</div>
                                <div className="text-xs text-slate-500">Skill reviews</div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.18em] text-slate-400">allowlist</div>
                                <div className="mt-2 text-2xl font-semibold text-slate-950">{dashboard?.summary?.activeAllowlist ?? 0}</div>
                                <div className="text-xs text-slate-500">Active entries</div>
                            </div>
                            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                <div className="text-xs uppercase tracking-[0.18em] text-slate-400">events</div>
                                <div className="mt-2 text-2xl font-semibold text-slate-950">{dashboard?.summary?.recentDecisions ?? 0}</div>
                                <div className="text-xs text-slate-500">Recent decisions</div>
                            </div>
                        </div>

                        <Card className="rounded-2xl border-slate-200 shadow-sm">
                            <CardHeader>
                                <CardTitle className="text-base">待处理 Safety 审批</CardTitle>
                                <CardDescription>“记住此授权”会写入强绑定 allowlist；目标、runtime、path plane 或 risk code 变化后不会复用。</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                {(dashboard?.pendingSafetyApprovals || []).length === 0 ? (
                                    <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">暂无待处理 Safety 审批。</div>
                                ) : (
                                    (dashboard?.pendingSafetyApprovals || []).slice(0, 8).map((approval) => (
                                        <div key={approval.id} className="rounded-2xl border border-slate-200 p-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge variant="outline">{approval.approval_kind || "safety_review"}</Badge>
                                                <Badge variant={approval.verdict === "block" ? "destructive" : "secondary"}>{approval.riskCode || "unknown"}</Badge>
                                                <span className="text-xs text-slate-500">Run {approval.run_id || "-"}</span>
                                            </div>
                                            <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{approval.question || approval.reason || "SafetyRuntime 请求人工确认。"}</p>
                                            {approval.allowlistCandidate ? (
                                                <label className="mt-3 flex items-center gap-2 text-sm text-slate-600">
                                                    <input
                                                        type="checkbox"
                                                        checked={Boolean(rememberAllowlist[approval.id])}
                                                        onChange={(event) => setRememberAllowlist((previous) => ({ ...previous, [approval.id]: event.target.checked }))}
                                                    />
                                                    记住此授权为长期 allowlist（可撤销，强绑定目标与风险类型）
                                                </label>
                                            ) : null}
                                            <div className="mt-3 flex justify-end gap-2">
                                                <Button
                                                    type="button"
                                                    variant="outline"
                                                    disabled={governanceBusy === `approval:reject:${approval.id}` || governanceBusy === `approval:approve:${approval.id}`}
                                                    onClick={() => void handleApprovalAction(approval.id, false)}
                                                >
                                                    拒绝
                                                </Button>
                                                <Button
                                                    type="button"
                                                    disabled={governanceBusy === `approval:approve:${approval.id}` || governanceBusy === `approval:reject:${approval.id}`}
                                                    onClick={() => void handleApprovalAction(approval.id, true)}
                                                >
                                                    通过
                                                </Button>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </CardContent>
                        </Card>

                        <div className="grid gap-4 xl:grid-cols-2">
                            <Card className="rounded-2xl border-slate-200 shadow-sm">
                                <CardHeader>
                                    <CardTitle className="text-base">Skill Safety Ledger</CardTitle>
                                    <CardDescription>按内容 hash 复用审查结果；disabled skill 不进入模型候选。</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-3">
                                    {(dashboard?.skillSafetyReviews || []).length === 0 ? (
                                        <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">暂无 Skill 安全审查记录。</div>
                                    ) : (
                                        (dashboard?.skillSafetyReviews || []).slice(0, 8).map((review) => (
                                            <div key={review.id} className="rounded-2xl border border-slate-200 p-4">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className="font-medium text-slate-950">{review.skill_name || review.skill_id || "Unknown skill"}</span>
                                                    <Badge variant={review.disabled ? "destructive" : "outline"}>{review.disabled ? "disabled" : review.effective_verdict || "unknown"}</Badge>
                                                    {review.user_override ? <Badge variant="secondary">{review.user_override}</Badge> : null}
                                                </div>
                                                <div className="mt-2 line-clamp-1 text-xs text-slate-500">{review.skill_path || "-"}</div>
                                                {Array.isArray(review.reasons) && review.reasons.length ? (
                                                    <div className="mt-2 text-sm text-slate-600">{review.reasons.slice(0, 2).join(" / ")}</div>
                                                ) : null}
                                                <div className="mt-3 flex flex-wrap justify-end gap-2">
                                                    <Button size="sm" variant="outline" disabled={Boolean(governanceBusy)} onClick={() => void handleSkillSafetyAction(review.id, "approve")}>审批放行</Button>
                                                    <Button size="sm" variant="outline" disabled={Boolean(governanceBusy)} onClick={() => void handleSkillSafetyAction(review.id, "disable")}>禁用</Button>
                                                    <Button size="sm" variant="ghost" disabled={Boolean(governanceBusy)} onClick={() => void handleSkillSafetyAction(review.id, "revoke")}>撤销</Button>
                                                    <Button size="sm" variant="ghost" disabled={Boolean(governanceBusy)} onClick={() => void handleSkillSafetyAction(review.id, "rescan")}>重扫</Button>
                                                </div>
                                            </div>
                                        ))
                                    )}
                                </CardContent>
                            </Card>

                            <Card className="rounded-2xl border-slate-200 shadow-sm">
                                <CardHeader>
                                    <CardTitle className="text-base">Safety Allowlist</CardTitle>
                                    <CardDescription>长期有效但可撤销；只按 normalized target hash、path plane、runtime source、action、risk code 命中。</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-3">
                                    {(dashboard?.allowlistEntries || []).length === 0 ? (
                                        <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">暂无长期授权记录。</div>
                                    ) : (
                                        (dashboard?.allowlistEntries || []).slice(0, 8).map((entry) => (
                                            <div key={entry.id} className="rounded-2xl border border-slate-200 p-4">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <Badge variant={entry.enabled ? "secondary" : "outline"}>{entry.enabled ? "active" : "revoked"}</Badge>
                                                    <Badge variant="outline">{entry.risk_code || "unknown"}</Badge>
                                                    <span className="text-xs text-slate-500">{entry.runtime_source || "unknown"} / {entry.path_plane || "unknown"} / {entry.action || "unknown"}</span>
                                                </div>
                                                <div className="mt-2 break-all text-sm text-slate-700">{entry.normalized_target_label || entry.id}</div>
                                                {entry.enabled ? (
                                                    <div className="mt-3 flex justify-end">
                                                        <Button size="sm" variant="outline" disabled={governanceBusy === `allowlist:revoke:${entry.id}`} onClick={() => void handleAllowlistRevoke(entry.id)}>
                                                            撤销授权
                                                        </Button>
                                                    </div>
                                                ) : null}
                                            </div>
                                        ))
                                    )}
                                </CardContent>
                            </Card>
                        </div>

                        <Card className="rounded-2xl border-slate-200 shadow-sm">
                            <CardHeader>
                                <CardTitle className="text-base">最近 Safety 决策</CardTitle>
                                <CardDescription>只显示脱敏后的 normalized / decoded 摘要，不显示完整 secret。</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                {(dashboard?.recentDecisions || []).length === 0 ? (
                                    <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">暂无 Safety decision event。</div>
                                ) : (
                                    (dashboard?.recentDecisions || []).slice(0, 10).map((event, index) => (
                                        <div key={event.id || `${event.timestamp}-${index}`} className="rounded-2xl border border-slate-200 p-4">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge variant={event.verdict === "block" ? "destructive" : "outline"}>{event.verdict || event.status || "unknown"}</Badge>
                                                <Badge variant="secondary">{event.riskCode || "unknown"}</Badge>
                                                <span className="text-xs text-slate-500">{event.action || "safety"} · {event.runtimeSource || "unknown"} · {event.timestamp || "-"}</span>
                                            </div>
                                            {event.subject ? <div className="mt-2 break-all text-sm text-slate-700">{event.subject}</div> : null}
                                            {event.reason ? <div className="mt-2 text-sm text-slate-600">{event.reason}</div> : null}
                                            {Array.isArray(event.downloadHosts) && event.downloadHosts.length ? (
                                                <div className="mt-2 text-xs text-slate-500">download hosts: {event.downloadHosts.join(", ")}</div>
                                            ) : null}
                                        </div>
                                    ))
                                )}
                            </CardContent>
                        </Card>
                    </div>
                </AdvancedSection>

                <AdvancedSection title="app.admin.dashboard.safety.control.page.k4f8c7149" defaultOpen={false}>
                    <SafetyGuardianPanel />
                </AdvancedSection>

                <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} warnings={envelope.warnings} />
            </div>
        </AdminPageShell>
    );
}
