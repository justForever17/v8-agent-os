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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";
import { cn } from "@/lib/utils";

type SafetyData = {
    enabled: boolean;
    commandRules?: Array<{ verdict?: string; patterns?: string[] }>;
    runtimeRules?: Record<string, { reviewTriggerSources?: string[] }>;
    automationRules?: { reviewActionTypes?: string[] };
    channelGroupGuard?: {
        enabled?: boolean;
        allowlistOnly?: boolean;
        requireMention?: boolean;
        auditOnly?: boolean;
        allowlistGroups?: string[];
    };
};

const PRESET_OPTIONS = [
    {
        key: "daily",
        title: "日常使用",
        description: "保留基础阻断和常见复核，尽量少打扰。",
    },
    {
        key: "balanced",
        title: "平衡保护",
        description: "对自动化来源增加更多复核，适合长期运行。",
    },
    {
        key: "strict",
        title: "严格保护",
        description: "对自动化和渠道来源更谨慎，适合高风险场景。",
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
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<SafetyData> | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [preset, setPreset] = useState("daily");
    const [allowlistDraft, setAllowlistDraft] = useState("");

    const loadConfig = async () => {
        setLoading(true);
        try {
            const next = await fetchConfigDomain<SafetyData>("safety");
            setEnvelope(next);
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
        return {
            blockCount,
            reviewCount,
            groupGuardEnabled: Boolean(groupGuard.enabled),
            allowlistOnly: Boolean(groupGuard.allowlistOnly),
            requireMention: Boolean(groupGuard.requireMention),
            auditOnly: Boolean(groupGuard.auditOnly),
        };
    }, [envelope]);

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

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="安全控制"
                description="设置风险防护、人工确认和拦截规则。"
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button onClick={() => void handleApplyPreset()} disabled={saving}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}
                            立即应用
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip
                items={[
                    { label: "当前档位", value: PRESET_OPTIONS.find((item) => item.key === preset)?.title || "日常使用", description: "当前首选的风险保护方式。" },
                    { label: "阻断规则", value: summary.blockCount, description: "命中后会直接阻止执行。" },
                    { label: "复核规则", value: summary.reviewCount, description: "命中后会转入人工确认。" },
                    {
                        label: "群聊风险护栏",
                        value: summary.groupGuardEnabled ? (summary.auditOnly ? "仅审计" : "已开启") : "默认关闭",
                        description: summary.groupGuardEnabled ? "群聊自动响应会先经过 Plugin Host 入口护栏。" : "当前不会在群聊入口自动拦截危险会话。",
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
                        <div className="text-base font-semibold">{option.title}</div>
                        <div className="mt-2 text-sm leading-6 text-slate-500">{option.description}</div>
                    </button>
                ))}
            </div>

            <StatusNotice
                title="待确认事项请到运行与问题处理。"
                description="群聊插件若还有原生限制，请到对应插件页查看。"
                tone="info"
            />

            <Card className="border-slate-200 shadow-sm">
                <CardHeader>
                    <CardTitle>群聊危险会话拦截</CardTitle>
                    <CardDescription>这层护栏前置在渠道插件入站口，只管群聊自动响应风险，不把它扩成审批系统。默认关闭。</CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="grid gap-4 lg:grid-cols-2">
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/60 p-4">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <div className="font-medium text-slate-900">启用群聊危险会话拦截</div>
                                    <div className="mt-1 text-sm leading-6 text-slate-500">开启后，群聊消息会先经过 PluginHostRuntime 的群聊风险规则。</div>
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
                                        <div className="font-medium text-slate-900">仅 allowlist 群自动响应</div>
                                        <div className="mt-1 text-sm leading-6 text-slate-500">不在 allowlist 的群会被拦截或记录审计。</div>
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
                                        <div className="font-medium text-slate-900">必须 @ 机器人后继续</div>
                                        <div className="mt-1 text-sm leading-6 text-slate-500">适合只在明确点名时才允许自动响应的群聊场景。</div>
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
                                        <div className="font-medium text-slate-900">仅审计不拦截</div>
                                        <div className="mt-1 text-sm leading-6 text-slate-500">用于先观察真实群聊风险，再决定是否打开硬拦截。</div>
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
                        <Label htmlFor="group-allowlist">allowlist 群 ID（每行一个）</Label>
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
                title="详细规则"
                description="需要调整更细的拦截和复核条件时，再展开这里。"
                defaultOpen={false}
            >
                <SafetyGuardianPanel />
            </AdvancedSection>
        </AdminPageShell>
    );
}
