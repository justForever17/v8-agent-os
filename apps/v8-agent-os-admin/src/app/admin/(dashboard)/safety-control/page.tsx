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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/components/providers/LocaleProvider";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { lt } from "@/lib/locale";
import { cn } from "@/lib/utils";

type ModelOption = {
    modelId: string;
    name: string;
    type: string;
    provider?: { name?: string };
};

type SafetyData = {
    enabled: boolean;
    commandRules?: Array<{ verdict?: string; patterns?: string[] }>;
    runtimeRules?: Record<string, { reviewTriggerSources?: string[] }>;
    automationRules?: { reviewActionTypes?: string[] };
    modelBindings?: {
        safetyReviewModel?: string;
    };
    channelGroupGuard?: {
        enabled?: boolean;
        allowlistOnly?: boolean;
        requireMention?: boolean;
        auditOnly?: boolean;
        allowlistGroups?: string[];
    };
    runtimeSummary?: {
        mode?: string;
        llmBound?: boolean;
        skillStaticScanEnabled?: boolean;
        blockedSkillScans?: number;
        verdictDistribution?: Record<string, number>;
        recentSkillScans?: Array<{
            skillName?: string;
            verdict?: string;
            confidence?: number;
            skillTrustScore?: number;
            auditId?: string;
            timestamp?: string;
            reasons?: string[];
        }>;
    };
};

const PRESET_OPTIONS = [
    {
        key: "daily",
        title: lt("日常使用", "Daily use"),
        description: lt("保留基础阻断和常见复核，尽量少打扰。", "Keep the default guards and common reviews while minimizing interruptions."),
    },
    {
        key: "balanced",
        title: lt("平衡保护", "Balanced protection"),
        description: lt("对自动化来源增加更多复核，适合长期运行。", "Add more reviews for automation sources, suitable for long-running sessions."),
    },
    {
        key: "strict",
        title: lt("严格保护", "Strict protection"),
        description: lt("对自动化和渠道来源更谨慎，适合高风险场景。", "Treat automation and channel sources more cautiously for higher-risk scenarios."),
    },
];

function applyPreset(config: SafetyData, preset: string): SafetyData {
    const next = structuredClone(config);
    next.enabled = true;
    const runtimeRules = next.runtimeRules || {};

    if (preset === "daily") {
        Object.keys(runtimeRules).forEach((key) => {
            runtimeRules[key] = {
                ...(runtimeRules[key] || {}),
                reviewTriggerSources: [],
            };
        });
        next.automationRules = {
            ...(next.automationRules || {}),
            reviewActionTypes: ["command"],
        };
        next.runtimeRules = runtimeRules;
        return next;
    }

    if (preset === "balanced") {
        runtimeRules.automation = {
            ...(runtimeRules.automation || {}),
            reviewTriggerSources: ["cron"],
        };
        next.automationRules = {
            ...(next.automationRules || {}),
            reviewActionTypes: ["command"],
        };
        next.runtimeRules = runtimeRules;
        return next;
    }

    runtimeRules.automation = {
        ...(runtimeRules.automation || {}),
        reviewTriggerSources: ["cron", "hook:on_chat_end"],
    };
    runtimeRules.plugin_host = {
        ...(runtimeRules.plugin_host || {}),
        reviewTriggerSources: ["channel"],
    };
    next.automationRules = {
        ...(next.automationRules || {}),
        reviewActionTypes: ["command"],
    };
    next.runtimeRules = runtimeRules;
    return next;
}

function detectPreset(config: SafetyData) {
    const automationTriggers = config.runtimeRules?.automation?.reviewTriggerSources || [];
    const channelTriggers = config.runtimeRules?.plugin_host?.reviewTriggerSources || [];
    if (automationTriggers.includes("hook:on_chat_end") || channelTriggers.includes("channel")) {
        return "strict";
    }
    if (automationTriggers.includes("cron")) {
        return "balanced";
    }
    return "daily";
}

function formatLines(value?: string[]) {
    return Array.isArray(value) ? value.join("\n") : "";
}

function parseLines(value: string) {
    return value
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean);
}

export default function SafetyControlPage() {
    const t = useT();
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<SafetyData> | null>(null);
    const [models, setModels] = useState<ModelOption[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [preset, setPreset] = useState("daily");
    const [allowlistDraft, setAllowlistDraft] = useState("");

    const loadConfig = async () => {
        setLoading(true);
        try {
            const [next, modelList] = await Promise.all([
                fetchConfigDomain<SafetyData>("safety"),
                fetch("/api/models", { cache: "no-store" }).then((response) => response.json().catch(() => [])),
            ]);
            setEnvelope(next);
            setModels(Array.isArray(modelList) ? modelList : []);
            setPreset(detectPreset(next.data));
            setAllowlistDraft(formatLines(next.data.channelGroupGuard?.allowlistGroups));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadConfig();
    }, []);

    const summary = useMemo(() => {
        const blockCount = envelope?.data.commandRules?.find((rule) => rule.verdict === "block")?.patterns?.length || 0;
        const reviewCount = envelope?.data.commandRules?.find((rule) => rule.verdict === "review")?.patterns?.length || 0;
        const groupGuard = envelope?.data.channelGroupGuard || {};
        const runtimeSummary = envelope?.data.runtimeSummary || {};
        return {
            blockCount,
            reviewCount,
            groupGuardEnabled: Boolean(groupGuard.enabled),
            allowlistOnly: Boolean(groupGuard.allowlistOnly),
            requireMention: Boolean(groupGuard.requireMention),
            auditOnly: Boolean(groupGuard.auditOnly),
            blockedSkillScans: Number(runtimeSummary.blockedSkillScans || 0) || 0,
            skillStaticScanEnabled: Boolean(runtimeSummary.skillStaticScanEnabled),
        };
    }, [envelope]);

    const llmModels = useMemo(
        () => models.filter((model) => ["TEXT", "MULTIMODAL", "CHAT", "LLM"].includes((model.type || "").toUpperCase())),
        [models],
    );

    const handleApplyPreset = async () => {
        if (!envelope) return;
        setSaving(true);
        try {
            const next = await saveConfigDomain<SafetyData>("safety", {
                data: {
                    ...applyPreset(envelope.data, preset),
                    channelGroupGuard: {
                        enabled: Boolean(envelope.data.channelGroupGuard?.enabled),
                        allowlistOnly: Boolean(envelope.data.channelGroupGuard?.allowlistOnly),
                        requireMention: Boolean(envelope.data.channelGroupGuard?.requireMention),
                        auditOnly: Boolean(envelope.data.channelGroupGuard?.auditOnly),
                        allowlistGroups: parseLines(allowlistDraft),
                    },
                },
            });
            setEnvelope(next);
            setAllowlistDraft(formatLines(next.data.channelGroupGuard?.allowlistGroups));
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

    const activePreset = PRESET_OPTIONS.find((item) => item.key === preset);
    const safetyReviewModel = String(envelope.data.modelBindings?.safetyReviewModel || "").trim();
    const llmReviewEnabled = Boolean(safetyReviewModel);

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={lt("安全控制", "Safety Control")}
                description={lt(
                    "当前 Safety Guardian 以规则与审计为主；这里管理风险护栏、人工确认、skill 前置阻断，以及专用安全评审模型的二阶段复审绑定。",
                    "Safety Guardian is currently rules-and-audit first. Manage risk guardrails, human reviews, skill preflight blocking, and the dedicated safety-review model used for second-pass review here.",
                )}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void handleApplyPreset()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}
                            {t(lt("立即应用", "Apply now"))}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip
                items={[
                    { label: lt("当前档位", "Current profile"), value: activePreset ? t(activePreset.title) : t(lt("日常使用", "Daily use")), description: lt("当前首选的风险保护方式。", "The currently selected protection posture.") },
                    { label: lt("阻断规则", "Block rules"), value: summary.blockCount, description: lt("命中后会直接阻止执行。", "Matches here are blocked immediately.") },
                    { label: lt("复核规则", "Review rules"), value: summary.reviewCount, description: lt("命中后会转入人工确认。", "Matches here are escalated to review.") },
                    {
                        label: lt("Skill 阻断审计", "Skill block audits"),
                        value: summary.blockedSkillScans,
                        description: summary.skillStaticScanEnabled
                            ? lt("读取 SKILL.md 前会先做同步静态初筛。", "A synchronous static scan now runs before reading SKILL.md.")
                            : lt("当前未启用 Skill 初筛摘要。", "Skill preflight summaries are not enabled right now."),
                    },
                    {
                        label: lt("群聊风险护栏", "Group-chat guardrail"),
                        value: summary.groupGuardEnabled
                            ? (summary.auditOnly ? t(lt("仅审计", "Audit only")) : t(lt("已开启", "Enabled")))
                            : t(lt("默认关闭", "Off by default")),
                        description: summary.groupGuardEnabled
                            ? lt("群聊自动响应会先经过 Plugin Host 入口护栏。", "Automatic group-chat responses now pass through the Plugin Host guardrail first.")
                            : lt("当前不会在群聊入口自动拦截危险会话。", "Dangerous sessions are not intercepted automatically at the group-chat entry point right now."),
                    },
                ]}
            />

            <div className="grid gap-4 lg:grid-cols-3">
                {PRESET_OPTIONS.map((option) => (
                    <button
                        key={option.key}
                        type="button"
                        className={cn(
                            "rounded-2xl border px-5 py-5 text-left shadow-sm transition-colors",
                            preset === option.key
                                ? "border-sky-200 bg-sky-50 text-sky-900"
                                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
                        )}
                        onClick={() => setPreset(option.key)}
                    >
                        <div className="text-base font-semibold">{t(option.title)}</div>
                        <div className="mt-2 text-sm leading-6 text-slate-500">{t(option.description)}</div>
                    </button>
                ))}
            </div>

            <StatusNotice
                title={llmReviewEnabled ? lt("这里是 rules-first + 二阶段复审的安全控制面。", "This is a rules-first safety control surface with second-pass review.") : lt("这里是 rules-first 的安全控制面。", "This is a rules-first safety control surface.")}
                description={lt(
                    llmReviewEnabled
                        ? "当前 Safety Guardian 负责命令/运行时护栏与 skill 静态初筛；medium/high 风险的 skill 现在会进入专用安全模型二阶段复审。"
                        : "当前 Safety Guardian 负责命令/运行时护栏与 skill 静态初筛；如果还没绑定专用安全模型，系统会继续保持 rules/audit-first。",
                    llmReviewEnabled
                        ? "Safety Guardian still owns the command/runtime guardrails and skill preflight scan. medium/high-risk skills now enter a dedicated second-pass safety review."
                        : "Safety Guardian currently handles command/runtime guardrails and skill static preflight scans. If no dedicated safety model is bound, the system remains rules/audit-first.",
                )}
                tone="info"
            />

            <Card className="border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle>{t(lt("当前安全主线", "Current safety path"))}</CardTitle>
                    <CardDescription>{t(llmReviewEnabled
                        ? lt("规则审计仍是第一阶段；skill 读取前会先做静态初筛，medium/high 风险会进入专用安全模型复审，critical 仍会直接阻断。", "Rules and audit remain the first stage. Before a skill is read, a static preflight scan runs first, medium/high-risk skills enter the dedicated safety review model, and critical findings are still blocked immediately.")
                        : lt("规则审计优先，skill 读取前的高风险初筛已接入主链。命中高风险时会阻断当前 skill 使用分支，并把 supervisor 拉回安全路径。", "Rules and audit remain first. High-risk preflight scanning before skill reads is already on the main path. When a high-risk skill is detected, the current skill branch is blocked and the supervisor is pulled back onto a safer route."))}</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-3 text-sm text-slate-600 lg:grid-cols-3">
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                        <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t(lt("执行模式", "Execution mode"))}</div>
                        <div className="mt-2 font-semibold text-slate-900">{llmReviewEnabled ? "Rules / Audit + LLM Review" : "Rules / Audit First"}</div>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                        <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t(lt("Skill 读取前门禁", "Skill pre-read gate"))}</div>
                        <div className="mt-2 font-semibold text-slate-900">{t(lt("已接入", "Active"))}</div>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                        <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t(lt("专用安全模型", "Dedicated safety model"))}</div>
                        <div className="mt-2 font-semibold text-slate-900">{safetyReviewModel || t(lt("当前未绑定", "Unbound right now"))}</div>
                    </div>
                </CardContent>
            </Card>

            <Card className="border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle>{t(lt("专用安全评审模型", "Dedicated safety review model"))}</CardTitle>
                    <CardDescription>{t(lt("这里绑定的是专用安全评审模型，会保存到模型角色配置中。绑定后，medium/high 风险的 skill 会进入二阶段复审；未绑定时继续保持 rules-first。", "This binds the dedicated safety-review model and saves it into the role configuration. Once bound, medium/high-risk skills enter a second-pass review. If unbound, the system remains rules-first."))}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                    <div className="space-y-1.5">
                        <Label>{t(lt("专用安全评审模型", "Dedicated safety review model"))}</Label>
                        <Select
                            value={safetyReviewModel || "__empty__"}
                            onValueChange={(value) =>
                                setEnvelope((current) =>
                                    current
                                        ? {
                                              ...current,
                                              data: {
                                                  ...current.data,
                                                  modelBindings: {
                                                      ...(current.data.modelBindings || {}),
                                                      safetyReviewModel: value === "__empty__" ? "" : value,
                                                  },
                                              },
                                          }
                                        : current
                                )
                            }
                        >
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder={t(lt("未绑定专用安全模型", "No dedicated safety model bound"))} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="__empty__">{t(lt("未绑定", "Unbound"))}</SelectItem>
                                {llmModels.map((model) => (
                                    <SelectItem key={model.modelId} value={model.modelId}>
                                        {model.name || model.modelId} {model.provider?.name ? `(${model.provider.name})` : ""}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-slate-500">
                            {safetyReviewModel
                                ? t(lt("当前已保存专用安全模型绑定；medium/high 风险的 skill 会进入二阶段复审，critical 仍会直接阻断。", "A dedicated safety model is bound. medium/high-risk skills now enter a second-pass review, while critical findings are still blocked immediately."))
                                : t(lt("当前还没有绑定专用安全模型；此时系统只执行规则/审计主链，不会进入 LLM 二次复审。", "No dedicated safety model is bound yet. In this state, the system stays on the rules/audit path and does not enter LLM second-pass review."))}
                        </p>
                    </div>
                </CardContent>
            </Card>

            <Card className="border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle>{t(lt("Skill 初筛摘要", "Skill preflight summary"))}</CardTitle>
                    <CardDescription>{t(lt("这里只展示最近的静态初筛结果，用来说明当前主线已经会在读取说明前先做风险判断，而不是依赖安全专用模型。", "This section shows recent static preflight results. Its purpose is to show that the current main path already performs risk checks before reading a skill, rather than depending on a dedicated safety model."))}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-3">
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                            <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t(lt("安全模式", "Safety mode"))}</div>
                            <div className="mt-2 font-semibold text-slate-900">{envelope.data.runtimeSummary?.mode || "rules_audit_first"}</div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                            <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t(lt("Skill 静态初筛", "Skill static preflight"))}</div>
                            <div className="mt-2 font-semibold text-slate-900">{envelope.data.runtimeSummary?.skillStaticScanEnabled ? t(lt("已接入", "Active")) : t(lt("未接入", "Inactive"))}</div>
                        </div>
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
                            <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">{t(lt("LLM 绑定", "LLM binding"))}</div>
                            <div className="mt-2 font-semibold text-slate-900">{envelope.data.runtimeSummary?.llmBound ? t(lt("已启用二阶段复审", "Second-pass review enabled")) : t(lt("当前无专用安全模型", "No dedicated safety model bound"))}</div>
                        </div>
                    </div>
                    {Array.isArray(envelope.data.runtimeSummary?.recentSkillScans) && envelope.data.runtimeSummary!.recentSkillScans!.length > 0 ? (
                        <div className="space-y-3">
                            {envelope.data.runtimeSummary!.recentSkillScans!.map((scan, index) => (
                                <div key={`${scan.auditId || scan.skillName || "skill"}-${index}`} className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="font-medium text-slate-900">{scan.skillName || t(lt("未知 Skill", "Unknown skill"))}</span>
                                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{scan.verdict || "unknown"}</span>
                                        {typeof scan.skillTrustScore === "number" ? (
                                            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">Trust {scan.skillTrustScore}</span>
                                        ) : null}
                                    </div>
                                    {Array.isArray(scan.reasons) && scan.reasons.length > 0 ? (
                                        <div className="mt-2 text-slate-600">{scan.reasons.slice(0, 2).join(t(lt("；", "; ")))}</div>
                                    ) : (
                                        <div className="mt-2 text-slate-500">{t(lt("暂无详细理由摘要。", "No detailed reason summary is available yet."))}</div>
                                    )}
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-4 text-sm text-slate-500">
                            {t(lt("暂无最近的 Skill 初筛记录。后续命中 Skill 读取时，这里会展示最近的阻断/放行摘要。", "No recent skill preflight records yet. Once a skill-read path is triggered, the latest allow/block summary will appear here."))}
                        </div>
                    )}
                </CardContent>
            </Card>

            <Card className="border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle>{t(lt("群聊危险会话拦截", "Group-chat risky session guard"))}</CardTitle>
                    <CardDescription>{t(lt("这层护栏前置在渠道插件入站口，只管群聊自动响应风险，不把它扩成审批系统。默认关闭。", "This guardrail sits in front of the channel-plugin ingress. It only covers risky automatic group-chat responses and is not expanded into a full approval system. It stays off by default."))}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="grid gap-4 lg:grid-cols-2">
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/60 p-4">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <div className="font-medium text-slate-900">{t(lt("启用群聊危险会话拦截", "Enable risky group-chat guard"))}</div>
                                    <div className="mt-1 text-sm leading-6 text-slate-500">{t(lt("开启后，群聊消息会先经过 PluginHostRuntime 的群聊风险规则。", "When enabled, group-chat messages pass through PluginHostRuntime risk rules first."))}</div>
                                </div>
                                <Switch
                                    checked={Boolean(envelope.data.channelGroupGuard?.enabled)}
                                    onCheckedChange={(checked) =>
                                        setEnvelope((current) =>
                                            current
                                                ? {
                                                      ...current,
                                                      data: {
                                                          ...current.data,
                                                          channelGroupGuard: {
                                                              ...current.data.channelGroupGuard,
                                                              enabled: checked,
                                                          },
                                                      },
                                                  }
                                                : current
                                        )
                                    }
                                />
                            </div>
                        </div>

                        <div className="rounded-2xl border border-slate-200 bg-slate-50/60 p-4">
                            <div className="space-y-3">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="font-medium text-slate-900">{t(lt("仅 allowlist 群自动响应", "Restrict auto-replies to allowlisted groups"))}</div>
                                        <div className="mt-1 text-sm leading-6 text-slate-500">{t(lt("不在 allowlist 的群会被拦截或记录审计。", "Groups outside the allowlist are blocked or logged for audit."))}</div>
                                    </div>
                                    <Switch
                                        checked={Boolean(envelope.data.channelGroupGuard?.allowlistOnly)}
                                        onCheckedChange={(checked) =>
                                            setEnvelope((current) =>
                                                current
                                                    ? {
                                                          ...current,
                                                          data: {
                                                              ...current.data,
                                                              channelGroupGuard: {
                                                                  ...current.data.channelGroupGuard,
                                                                  allowlistOnly: checked,
                                                              },
                                                          },
                                                      }
                                                    : current
                                            )
                                        }
                                    />
                                </div>
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="font-medium text-slate-900">{t(lt("必须 @ 机器人后继续", "Require explicit mention"))}</div>
                                        <div className="mt-1 text-sm leading-6 text-slate-500">{t(lt("适合只在明确点名时才允许自动响应的群聊场景。", "Useful when automatic replies should only continue after an explicit mention."))}</div>
                                    </div>
                                    <Switch
                                        checked={Boolean(envelope.data.channelGroupGuard?.requireMention)}
                                        onCheckedChange={(checked) =>
                                            setEnvelope((current) =>
                                                current
                                                    ? {
                                                          ...current,
                                                          data: {
                                                              ...current.data,
                                                              channelGroupGuard: {
                                                                  ...current.data.channelGroupGuard,
                                                                  requireMention: checked,
                                                              },
                                                          },
                                                      }
                                                    : current
                                            )
                                        }
                                    />
                                </div>
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="font-medium text-slate-900">{t(lt("仅审计不拦截", "Audit without blocking"))}</div>
                                        <div className="mt-1 text-sm leading-6 text-slate-500">{t(lt("用于先观察真实群聊风险，再决定是否打开硬拦截。", "Use this to observe real group-chat risk first, then decide whether to enable hard blocking."))}</div>
                                    </div>
                                    <Switch
                                        checked={Boolean(envelope.data.channelGroupGuard?.auditOnly)}
                                        onCheckedChange={(checked) =>
                                            setEnvelope((current) =>
                                                current
                                                    ? {
                                                          ...current,
                                                          data: {
                                                              ...current.data,
                                                              channelGroupGuard: {
                                                                  ...current.data.channelGroupGuard,
                                                                  auditOnly: checked,
                                                              },
                                                          },
                                                      }
                                                    : current
                                            )
                                        }
                                    />
                                </div>
                            </div>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="group-allowlist">{t(lt("allowlist 群 ID（每行一个）", "Allowlist group IDs (one per line)"))}</Label>
                        <Textarea
                            id="group-allowlist"
                            rows={5}
                            value={allowlistDraft}
                            onChange={(event) => setAllowlistDraft(event.target.value)}
                            placeholder={"oc_group_alpha\nwx_room_beta"}
                        />
                    </div>
                </CardContent>
            </Card>

            <SourceMetaRow
                source={envelope.source}
                savePath={envelope.savePath}
                reloadRequired={envelope.reloadRequired}
            />

            <AdvancedSection
                title={lt("详细规则", "Detailed rules")}
                description={lt("需要调整更细的拦截和复核条件时，再展开这里。", "Expand this section only when you need finer-grained block and review conditions.")}
                defaultOpen={false}
            >
                <SafetyGuardianPanel />
            </AdvancedSection>
        </AdminPageShell>
    );
}
