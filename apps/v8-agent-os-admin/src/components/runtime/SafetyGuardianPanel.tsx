"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Save, ShieldCheck, ShieldEllipsis } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

type CommandRule = {
    id: string;
    label: string;
    verdict: "block" | "review";
    description?: string;
    patterns: string[];
};

type SafetyGuardianConfig = {
    enabled: boolean;
    commandRules: CommandRule[];
    fileRules: {
        protectedPaths: string[];
        blockedPathPatterns: string[];
        reviewPathPatterns: string[];
        protectedFileExtensions: string[];
    };
    processRules: {
        protectedPatterns: string[];
        reviewPatterns: string[];
    };
    networkRules: {
        localHosts: string[];
        blockedHosts: string[];
        reviewHosts: string[];
        reviewMethods: string[];
    };
    automationRules: {
        blockedActionTypes: string[];
        reviewActionTypes: string[];
        reviewTargetPatterns: string[];
        blockedTargetPatterns: string[];
    };
    runtimeRules: Record<
        "chat" | "automation" | "plugin_host",
        {
            reviewTriggerSources: string[];
            blockedTriggerSources: string[];
            reviewScopePatterns: string[];
            blockedScopePatterns: string[];
        }
    >;
    channelGroupGuard: {
        enabled: boolean;
        allowlistOnly: boolean;
        requireMention: boolean;
        auditOnly: boolean;
        allowlistGroups: string[];
    };
    postActionRules: {
        enabledFamilies: string[];
        highlightFamilies: string[];
        mutatingHttpMethods: string[];
    };
};

function parseLines(value: string) {
    return value
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean);
}

function formatLines(value?: string[]) {
    return Array.isArray(value) ? value.join("\n") : "";
}

const DEFAULT_CONFIG: SafetyGuardianConfig = {
    enabled: true,
    commandRules: [
        { id: "command_block", label: "系统级阻断命令", verdict: "block", description: "", patterns: [] },
        { id: "command_review", label: "高风险复核命令", verdict: "review", description: "", patterns: [] },
    ],
    fileRules: {
        protectedPaths: [],
        blockedPathPatterns: [],
        reviewPathPatterns: [],
        protectedFileExtensions: [".db", ".sqlite", ".sqlite3"],
    },
    processRules: {
        protectedPatterns: [],
        reviewPatterns: [],
    },
    networkRules: {
        localHosts: ["127.0.0.1", "localhost", "::1"],
        blockedHosts: [],
        reviewHosts: [],
        reviewMethods: ["POST", "PUT", "PATCH", "DELETE"],
    },
    automationRules: {
        blockedActionTypes: [],
        reviewActionTypes: ["command"],
        reviewTargetPatterns: [],
        blockedTargetPatterns: [],
    },
    runtimeRules: {
        chat: {
            reviewTriggerSources: [],
            blockedTriggerSources: [],
            reviewScopePatterns: [],
            blockedScopePatterns: [],
        },
        automation: {
            reviewTriggerSources: [],
            blockedTriggerSources: [],
            reviewScopePatterns: [],
            blockedScopePatterns: [],
        },
        plugin_host: {
            reviewTriggerSources: [],
            blockedTriggerSources: [],
            reviewScopePatterns: [],
            blockedScopePatterns: [],
        },
    },
    channelGroupGuard: {
        enabled: false,
        allowlistOnly: false,
        requireMention: false,
        auditOnly: false,
        allowlistGroups: [],
    },
    postActionRules: {
        enabledFamilies: ["command", "file_write", "http_request", "process", "cron_mutation", "hook_mutation", "background_command", "automation_action"],
        highlightFamilies: ["process", "cron_mutation", "hook_mutation", "http_request"],
        mutatingHttpMethods: ["POST", "PUT", "PATCH", "DELETE"],
    },
};

const RUNTIME_RULE_META: Array<{ key: keyof SafetyGuardianConfig["runtimeRules"]; label: string }> = [
    { key: "chat", label: "对话运行" },
    { key: "automation", label: "AutomationRuntime" },
    { key: "plugin_host", label: "插件宿主" },
];

function ensureRule(config: SafetyGuardianConfig, verdict: "block" | "review"): CommandRule {
    const existing = config.commandRules.find((rule) => rule.verdict === verdict);
    if (existing) {
        return existing;
    }
    return {
        id: verdict === "block" ? "command_block" : "command_review",
        label: verdict === "block" ? "系统级阻断命令" : "高风险复核命令",
        verdict,
        description: "",
        patterns: [],
    };
}

export function SafetyGuardianPanel() {
    const [config, setConfig] = useState<SafetyGuardianConfig>(DEFAULT_CONFIG);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const [protectedPathsDraft, setProtectedPathsDraft] = useState("");
    const [blockedCommandDraft, setBlockedCommandDraft] = useState("");
    const [reviewCommandDraft, setReviewCommandDraft] = useState("");
    const [blockedPathDraft, setBlockedPathDraft] = useState("");
    const [reviewPathDraft, setReviewPathDraft] = useState("");
    const [protectedExtensionsDraft, setProtectedExtensionsDraft] = useState("");
    const [protectedProcessDraft, setProtectedProcessDraft] = useState("");
    const [reviewProcessDraft, setReviewProcessDraft] = useState("");
    const [localHostDraft, setLocalHostDraft] = useState("");
    const [blockedHostDraft, setBlockedHostDraft] = useState("");
    const [reviewHostDraft, setReviewHostDraft] = useState("");
    const [reviewMethodDraft, setReviewMethodDraft] = useState("");
    const [blockedActionTypesDraft, setBlockedActionTypesDraft] = useState("");
    const [reviewActionTypesDraft, setReviewActionTypesDraft] = useState("");
    const [reviewTargetDraft, setReviewTargetDraft] = useState("");
    const [blockedTargetDraft, setBlockedTargetDraft] = useState("");
    const [runtimeDrafts, setRuntimeDrafts] = useState<Record<string, {
        reviewTriggerSources: string;
        blockedTriggerSources: string;
        reviewScopePatterns: string;
        blockedScopePatterns: string;
    }>>({
        chat: { reviewTriggerSources: "", blockedTriggerSources: "", reviewScopePatterns: "", blockedScopePatterns: "" },
        automation: { reviewTriggerSources: "", blockedTriggerSources: "", reviewScopePatterns: "", blockedScopePatterns: "" },
        plugin_host: { reviewTriggerSources: "", blockedTriggerSources: "", reviewScopePatterns: "", blockedScopePatterns: "" },
    });
    const [postActionEnabledDraft, setPostActionEnabledDraft] = useState("");
    const [postActionHighlightDraft, setPostActionHighlightDraft] = useState("");
    const [postActionMutatingHttpDraft, setPostActionMutatingHttpDraft] = useState("");
    const [channelGroupAllowlistDraft, setChannelGroupAllowlistDraft] = useState("");

    const syncDrafts = useCallback((nextConfig: SafetyGuardianConfig) => {
        setConfig(nextConfig);
        const blockRule = ensureRule(nextConfig, "block");
        const reviewRule = ensureRule(nextConfig, "review");

        setProtectedPathsDraft(formatLines(nextConfig.fileRules.protectedPaths));
        setBlockedCommandDraft(formatLines(blockRule.patterns));
        setReviewCommandDraft(formatLines(reviewRule.patterns));
        setBlockedPathDraft(formatLines(nextConfig.fileRules.blockedPathPatterns));
        setReviewPathDraft(formatLines(nextConfig.fileRules.reviewPathPatterns));
        setProtectedExtensionsDraft(formatLines(nextConfig.fileRules.protectedFileExtensions));
        setProtectedProcessDraft(formatLines(nextConfig.processRules.protectedPatterns));
        setReviewProcessDraft(formatLines(nextConfig.processRules.reviewPatterns));
        setLocalHostDraft(formatLines(nextConfig.networkRules.localHosts));
        setBlockedHostDraft(formatLines(nextConfig.networkRules.blockedHosts));
        setReviewHostDraft(formatLines(nextConfig.networkRules.reviewHosts));
        setReviewMethodDraft(formatLines(nextConfig.networkRules.reviewMethods));
        setBlockedActionTypesDraft(formatLines(nextConfig.automationRules.blockedActionTypes));
        setReviewActionTypesDraft(formatLines(nextConfig.automationRules.reviewActionTypes));
        setReviewTargetDraft(formatLines(nextConfig.automationRules.reviewTargetPatterns));
        setBlockedTargetDraft(formatLines(nextConfig.automationRules.blockedTargetPatterns));
        setRuntimeDrafts({
            chat: {
                reviewTriggerSources: formatLines(nextConfig.runtimeRules.chat.reviewTriggerSources),
                blockedTriggerSources: formatLines(nextConfig.runtimeRules.chat.blockedTriggerSources),
                reviewScopePatterns: formatLines(nextConfig.runtimeRules.chat.reviewScopePatterns),
                blockedScopePatterns: formatLines(nextConfig.runtimeRules.chat.blockedScopePatterns),
            },
            automation: {
                reviewTriggerSources: formatLines(nextConfig.runtimeRules.automation.reviewTriggerSources),
                blockedTriggerSources: formatLines(nextConfig.runtimeRules.automation.blockedTriggerSources),
                reviewScopePatterns: formatLines(nextConfig.runtimeRules.automation.reviewScopePatterns),
                blockedScopePatterns: formatLines(nextConfig.runtimeRules.automation.blockedScopePatterns),
            },
            plugin_host: {
                reviewTriggerSources: formatLines(nextConfig.runtimeRules.plugin_host.reviewTriggerSources),
                blockedTriggerSources: formatLines(nextConfig.runtimeRules.plugin_host.blockedTriggerSources),
                reviewScopePatterns: formatLines(nextConfig.runtimeRules.plugin_host.reviewScopePatterns),
                blockedScopePatterns: formatLines(nextConfig.runtimeRules.plugin_host.blockedScopePatterns),
            },
        });
        setPostActionEnabledDraft(formatLines(nextConfig.postActionRules.enabledFamilies));
        setPostActionHighlightDraft(formatLines(nextConfig.postActionRules.highlightFamilies));
        setPostActionMutatingHttpDraft(formatLines(nextConfig.postActionRules.mutatingHttpMethods));
        setChannelGroupAllowlistDraft(formatLines(nextConfig.channelGroupGuard.allowlistGroups));
    }, []);

    const loadConfig = useCallback(async () => {
        setLoading(true);
        try {
            const response = await fetch("/api/settings/safety-guardian", { cache: "no-store" });
            const data = response.ok ? await response.json().catch(() => ({})) : {};
            syncDrafts({
                enabled: Boolean(data?.enabled ?? true),
                commandRules: Array.isArray(data?.commandRules) && data.commandRules.length > 0 ? data.commandRules : DEFAULT_CONFIG.commandRules,
                fileRules: {
                    protectedPaths: Array.isArray(data?.fileRules?.protectedPaths) ? data.fileRules.protectedPaths : DEFAULT_CONFIG.fileRules.protectedPaths,
                    blockedPathPatterns: Array.isArray(data?.fileRules?.blockedPathPatterns) ? data.fileRules.blockedPathPatterns : DEFAULT_CONFIG.fileRules.blockedPathPatterns,
                    reviewPathPatterns: Array.isArray(data?.fileRules?.reviewPathPatterns) ? data.fileRules.reviewPathPatterns : DEFAULT_CONFIG.fileRules.reviewPathPatterns,
                    protectedFileExtensions: Array.isArray(data?.fileRules?.protectedFileExtensions) ? data.fileRules.protectedFileExtensions : DEFAULT_CONFIG.fileRules.protectedFileExtensions,
                },
                processRules: {
                    protectedPatterns: Array.isArray(data?.processRules?.protectedPatterns) ? data.processRules.protectedPatterns : DEFAULT_CONFIG.processRules.protectedPatterns,
                    reviewPatterns: Array.isArray(data?.processRules?.reviewPatterns) ? data.processRules.reviewPatterns : DEFAULT_CONFIG.processRules.reviewPatterns,
                },
                networkRules: {
                    localHosts: Array.isArray(data?.networkRules?.localHosts) ? data.networkRules.localHosts : DEFAULT_CONFIG.networkRules.localHosts,
                    blockedHosts: Array.isArray(data?.networkRules?.blockedHosts) ? data.networkRules.blockedHosts : DEFAULT_CONFIG.networkRules.blockedHosts,
                    reviewHosts: Array.isArray(data?.networkRules?.reviewHosts) ? data.networkRules.reviewHosts : DEFAULT_CONFIG.networkRules.reviewHosts,
                    reviewMethods: Array.isArray(data?.networkRules?.reviewMethods) ? data.networkRules.reviewMethods : DEFAULT_CONFIG.networkRules.reviewMethods,
                },
                automationRules: {
                    blockedActionTypes: Array.isArray(data?.automationRules?.blockedActionTypes) ? data.automationRules.blockedActionTypes : DEFAULT_CONFIG.automationRules.blockedActionTypes,
                    reviewActionTypes: Array.isArray(data?.automationRules?.reviewActionTypes) ? data.automationRules.reviewActionTypes : DEFAULT_CONFIG.automationRules.reviewActionTypes,
                    reviewTargetPatterns: Array.isArray(data?.automationRules?.reviewTargetPatterns) ? data.automationRules.reviewTargetPatterns : DEFAULT_CONFIG.automationRules.reviewTargetPatterns,
                    blockedTargetPatterns: Array.isArray(data?.automationRules?.blockedTargetPatterns) ? data.automationRules.blockedTargetPatterns : DEFAULT_CONFIG.automationRules.blockedTargetPatterns,
                },
                runtimeRules: {
                    chat: {
                        reviewTriggerSources: Array.isArray(data?.runtimeRules?.chat?.reviewTriggerSources) ? data.runtimeRules.chat.reviewTriggerSources : DEFAULT_CONFIG.runtimeRules.chat.reviewTriggerSources,
                        blockedTriggerSources: Array.isArray(data?.runtimeRules?.chat?.blockedTriggerSources) ? data.runtimeRules.chat.blockedTriggerSources : DEFAULT_CONFIG.runtimeRules.chat.blockedTriggerSources,
                        reviewScopePatterns: Array.isArray(data?.runtimeRules?.chat?.reviewScopePatterns) ? data.runtimeRules.chat.reviewScopePatterns : DEFAULT_CONFIG.runtimeRules.chat.reviewScopePatterns,
                        blockedScopePatterns: Array.isArray(data?.runtimeRules?.chat?.blockedScopePatterns) ? data.runtimeRules.chat.blockedScopePatterns : DEFAULT_CONFIG.runtimeRules.chat.blockedScopePatterns,
                    },
                    automation: {
                        reviewTriggerSources: Array.isArray(data?.runtimeRules?.automation?.reviewTriggerSources) ? data.runtimeRules.automation.reviewTriggerSources : DEFAULT_CONFIG.runtimeRules.automation.reviewTriggerSources,
                        blockedTriggerSources: Array.isArray(data?.runtimeRules?.automation?.blockedTriggerSources) ? data.runtimeRules.automation.blockedTriggerSources : DEFAULT_CONFIG.runtimeRules.automation.blockedTriggerSources,
                        reviewScopePatterns: Array.isArray(data?.runtimeRules?.automation?.reviewScopePatterns) ? data.runtimeRules.automation.reviewScopePatterns : DEFAULT_CONFIG.runtimeRules.automation.reviewScopePatterns,
                        blockedScopePatterns: Array.isArray(data?.runtimeRules?.automation?.blockedScopePatterns) ? data.runtimeRules.automation.blockedScopePatterns : DEFAULT_CONFIG.runtimeRules.automation.blockedScopePatterns,
                    },
                    plugin_host: {
                        reviewTriggerSources: Array.isArray(data?.runtimeRules?.plugin_host?.reviewTriggerSources) ? data.runtimeRules.plugin_host.reviewTriggerSources : DEFAULT_CONFIG.runtimeRules.plugin_host.reviewTriggerSources,
                        blockedTriggerSources: Array.isArray(data?.runtimeRules?.plugin_host?.blockedTriggerSources) ? data.runtimeRules.plugin_host.blockedTriggerSources : DEFAULT_CONFIG.runtimeRules.plugin_host.blockedTriggerSources,
                        reviewScopePatterns: Array.isArray(data?.runtimeRules?.plugin_host?.reviewScopePatterns) ? data.runtimeRules.plugin_host.reviewScopePatterns : DEFAULT_CONFIG.runtimeRules.plugin_host.reviewScopePatterns,
                        blockedScopePatterns: Array.isArray(data?.runtimeRules?.plugin_host?.blockedScopePatterns) ? data.runtimeRules.plugin_host.blockedScopePatterns : DEFAULT_CONFIG.runtimeRules.plugin_host.blockedScopePatterns,
                    },
                },
                channelGroupGuard: {
                    enabled: Boolean(data?.channelGroupGuard?.enabled ?? DEFAULT_CONFIG.channelGroupGuard.enabled),
                    allowlistOnly: Boolean(data?.channelGroupGuard?.allowlistOnly ?? DEFAULT_CONFIG.channelGroupGuard.allowlistOnly),
                    requireMention: Boolean(data?.channelGroupGuard?.requireMention ?? DEFAULT_CONFIG.channelGroupGuard.requireMention),
                    auditOnly: Boolean(data?.channelGroupGuard?.auditOnly ?? DEFAULT_CONFIG.channelGroupGuard.auditOnly),
                    allowlistGroups: Array.isArray(data?.channelGroupGuard?.allowlistGroups) ? data.channelGroupGuard.allowlistGroups : DEFAULT_CONFIG.channelGroupGuard.allowlistGroups,
                },
                postActionRules: {
                    enabledFamilies: Array.isArray(data?.postActionRules?.enabledFamilies) ? data.postActionRules.enabledFamilies : DEFAULT_CONFIG.postActionRules.enabledFamilies,
                    highlightFamilies: Array.isArray(data?.postActionRules?.highlightFamilies) ? data.postActionRules.highlightFamilies : DEFAULT_CONFIG.postActionRules.highlightFamilies,
                    mutatingHttpMethods: Array.isArray(data?.postActionRules?.mutatingHttpMethods) ? data.postActionRules.mutatingHttpMethods : DEFAULT_CONFIG.postActionRules.mutatingHttpMethods,
                },
            });
        } finally {
            setLoading(false);
        }
    }, [syncDrafts]);

    useEffect(() => {
        void loadConfig();
    }, [loadConfig]);

    const payload = useMemo<SafetyGuardianConfig>(() => ({
        enabled: config.enabled,
        commandRules: [
            {
                ...ensureRule(config, "block"),
                patterns: parseLines(blockedCommandDraft),
            },
            {
                ...ensureRule(config, "review"),
                patterns: parseLines(reviewCommandDraft),
            },
        ],
        fileRules: {
            protectedPaths: parseLines(protectedPathsDraft),
            blockedPathPatterns: parseLines(blockedPathDraft),
            reviewPathPatterns: parseLines(reviewPathDraft),
            protectedFileExtensions: parseLines(protectedExtensionsDraft),
        },
        processRules: {
            protectedPatterns: parseLines(protectedProcessDraft),
            reviewPatterns: parseLines(reviewProcessDraft),
        },
        networkRules: {
            localHosts: parseLines(localHostDraft),
            blockedHosts: parseLines(blockedHostDraft),
            reviewHosts: parseLines(reviewHostDraft),
            reviewMethods: parseLines(reviewMethodDraft).map((item) => item.toUpperCase()),
        },
        automationRules: {
            blockedActionTypes: parseLines(blockedActionTypesDraft).map((item) => item.toLowerCase()),
            reviewActionTypes: parseLines(reviewActionTypesDraft).map((item) => item.toLowerCase()),
            reviewTargetPatterns: parseLines(reviewTargetDraft),
            blockedTargetPatterns: parseLines(blockedTargetDraft),
        },
        runtimeRules: {
            chat: {
                reviewTriggerSources: parseLines(runtimeDrafts.chat.reviewTriggerSources).map((item) => item.toLowerCase()),
                blockedTriggerSources: parseLines(runtimeDrafts.chat.blockedTriggerSources).map((item) => item.toLowerCase()),
                reviewScopePatterns: parseLines(runtimeDrafts.chat.reviewScopePatterns).map((item) => item.toLowerCase()),
                blockedScopePatterns: parseLines(runtimeDrafts.chat.blockedScopePatterns).map((item) => item.toLowerCase()),
            },
            automation: {
                reviewTriggerSources: parseLines(runtimeDrafts.automation.reviewTriggerSources).map((item) => item.toLowerCase()),
                blockedTriggerSources: parseLines(runtimeDrafts.automation.blockedTriggerSources).map((item) => item.toLowerCase()),
                reviewScopePatterns: parseLines(runtimeDrafts.automation.reviewScopePatterns).map((item) => item.toLowerCase()),
                blockedScopePatterns: parseLines(runtimeDrafts.automation.blockedScopePatterns).map((item) => item.toLowerCase()),
            },
            plugin_host: {
                reviewTriggerSources: parseLines(runtimeDrafts.plugin_host.reviewTriggerSources).map((item) => item.toLowerCase()),
                blockedTriggerSources: parseLines(runtimeDrafts.plugin_host.blockedTriggerSources).map((item) => item.toLowerCase()),
                reviewScopePatterns: parseLines(runtimeDrafts.plugin_host.reviewScopePatterns).map((item) => item.toLowerCase()),
                blockedScopePatterns: parseLines(runtimeDrafts.plugin_host.blockedScopePatterns).map((item) => item.toLowerCase()),
            },
        },
        channelGroupGuard: {
            enabled: config.channelGroupGuard.enabled,
            allowlistOnly: config.channelGroupGuard.allowlistOnly,
            requireMention: config.channelGroupGuard.requireMention,
            auditOnly: config.channelGroupGuard.auditOnly,
            allowlistGroups: parseLines(channelGroupAllowlistDraft),
        },
        postActionRules: {
            enabledFamilies: parseLines(postActionEnabledDraft).map((item) => item.toLowerCase()),
            highlightFamilies: parseLines(postActionHighlightDraft).map((item) => item.toLowerCase()),
            mutatingHttpMethods: parseLines(postActionMutatingHttpDraft).map((item) => item.toUpperCase()),
        },
    }), [
        blockedActionTypesDraft,
        blockedCommandDraft,
        blockedHostDraft,
        blockedPathDraft,
        blockedTargetDraft,
        config,
        localHostDraft,
        protectedExtensionsDraft,
        protectedPathsDraft,
        protectedProcessDraft,
        reviewActionTypesDraft,
        reviewCommandDraft,
        reviewHostDraft,
        reviewMethodDraft,
        reviewPathDraft,
        reviewProcessDraft,
        reviewTargetDraft,
        runtimeDrafts,
        channelGroupAllowlistDraft,
        postActionEnabledDraft,
        postActionHighlightDraft,
        postActionMutatingHttpDraft,
    ]);

    const handleSave = useCallback(async () => {
        setSaving(true);
        try {
            const response = await fetch("/api/settings/safety-guardian", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                throw new Error(`Failed to save safety guardian config: ${response.status}`);
            }
            const data = await response.json().catch(() => ({}));
            syncDrafts(data?.config || payload);
        } catch (error) {
            console.error("[SafetyGuardianPanel] Failed to save config:", error);
        } finally {
            setSaving(false);
        }
    }, [payload, syncDrafts]);

    return (
        <Card className="border-border/70">
            <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                <div>
                    <CardTitle className="flex items-center gap-2">
                        <ShieldCheck className="h-5 w-5 text-emerald-500" />
                        安全守护规则
                    </CardTitle>
                    <CardDescription>
                        这里管理命令、文件、进程、网络和自动化的保护规则，保存后会立即生效。
                    </CardDescription>
                </div>
                <div className="flex items-center gap-2">
                    <Badge variant={config.enabled ? "default" : "secondary"}>
                        {config.enabled ? "守护已启用" : "守护已关闭"}
                    </Badge>
                    <Button type="button" variant="outline" size="sm" onClick={() => void loadConfig()} disabled={loading || saving}>
                        <RefreshCw className="mr-2 h-4 w-4" />
                        刷新
                    </Button>
                </div>
            </CardHeader>
            <CardContent className="space-y-5">
                <div className="flex items-center justify-between rounded-2xl border border-border/70 bg-background/70 px-4 py-3">
                    <div>
                        <Label className="text-sm font-medium">开启安全守护</Label>
                        <p className="mt-1 text-xs text-muted-foreground">
                            关闭后将不再触发拦截或人工确认，建议仅在本地排查问题时临时关闭。
                        </p>
                    </div>
                    <Switch
                        checked={config.enabled}
                        onCheckedChange={(checked) => setConfig((current) => ({ ...current, enabled: checked }))}
                    />
                </div>

                <div className="grid gap-5 xl:grid-cols-2">
                    <div className="space-y-3 rounded-2xl border border-border/70 bg-background/60 p-4">
                        <div className="flex items-center gap-2 text-sm font-medium">
                            <ShieldEllipsis className="h-4 w-4 text-red-500" />
                            命令守护
                        </div>
                        <div className="space-y-2">
                            <Label>阻断型命令模式</Label>
                            <Textarea value={blockedCommandDraft} onChange={(event) => setBlockedCommandDraft(event.target.value)} className="min-h-[140px]" />
                        </div>
                        <div className="space-y-2">
                            <Label>复核型命令模式</Label>
                            <Textarea value={reviewCommandDraft} onChange={(event) => setReviewCommandDraft(event.target.value)} className="min-h-[140px]" />
                        </div>
                    </div>

                    <div className="space-y-3 rounded-2xl border border-border/70 bg-background/60 p-4">
                        <div className="text-sm font-medium">文件守护</div>
                        <div className="space-y-2">
                            <Label>受保护路径</Label>
                            <Textarea value={protectedPathsDraft} onChange={(event) => setProtectedPathsDraft(event.target.value)} className="min-h-[120px]" />
                        </div>
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>阻断路径模式</Label>
                                <Textarea value={blockedPathDraft} onChange={(event) => setBlockedPathDraft(event.target.value)} className="min-h-[110px]" />
                            </div>
                            <div className="space-y-2">
                                <Label>复核路径模式</Label>
                                <Textarea value={reviewPathDraft} onChange={(event) => setReviewPathDraft(event.target.value)} className="min-h-[110px]" />
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label>受保护文件扩展名</Label>
                            <Textarea value={protectedExtensionsDraft} onChange={(event) => setProtectedExtensionsDraft(event.target.value)} className="min-h-[84px]" />
                        </div>
                    </div>

                    <div className="space-y-3 rounded-2xl border border-border/70 bg-background/60 p-4">
                        <div className="text-sm font-medium">进程守护</div>
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>受保护进程模式</Label>
                                <Textarea value={protectedProcessDraft} onChange={(event) => setProtectedProcessDraft(event.target.value)} className="min-h-[120px]" />
                            </div>
                            <div className="space-y-2">
                                <Label>复核进程模式</Label>
                                <Textarea value={reviewProcessDraft} onChange={(event) => setReviewProcessDraft(event.target.value)} className="min-h-[120px]" />
                            </div>
                        </div>
                    </div>

                    <div className="space-y-3 rounded-2xl border border-border/70 bg-background/60 p-4">
                        <div className="text-sm font-medium">网络守护</div>
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>本地白名单主机</Label>
                                <Textarea value={localHostDraft} onChange={(event) => setLocalHostDraft(event.target.value)} className="min-h-[110px]" />
                            </div>
                            <div className="space-y-2">
                                <Label>阻断域名</Label>
                                <Textarea value={blockedHostDraft} onChange={(event) => setBlockedHostDraft(event.target.value)} className="min-h-[110px]" />
                            </div>
                            <div className="space-y-2">
                                <Label>复核域名</Label>
                                <Textarea value={reviewHostDraft} onChange={(event) => setReviewHostDraft(event.target.value)} className="min-h-[110px]" />
                            </div>
                            <div className="space-y-2">
                                <Label>需要复核的 HTTP 方法</Label>
                                <Textarea value={reviewMethodDraft} onChange={(event) => setReviewMethodDraft(event.target.value)} className="min-h-[110px]" />
                            </div>
                        </div>
                    </div>
                </div>

                <div className="space-y-3 rounded-2xl border border-border/70 bg-background/60 p-4">
                    <div className="text-sm font-medium">自动化守护</div>
                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                        <div className="space-y-2">
                            <Label>阻断动作类型</Label>
                            <Textarea value={blockedActionTypesDraft} onChange={(event) => setBlockedActionTypesDraft(event.target.value)} className="min-h-[110px]" />
                        </div>
                        <div className="space-y-2">
                            <Label>复核动作类型</Label>
                            <Textarea value={reviewActionTypesDraft} onChange={(event) => setReviewActionTypesDraft(event.target.value)} className="min-h-[110px]" />
                        </div>
                        <div className="space-y-2">
                            <Label>复核目标模式</Label>
                            <Textarea value={reviewTargetDraft} onChange={(event) => setReviewTargetDraft(event.target.value)} className="min-h-[110px]" />
                        </div>
                        <div className="space-y-2">
                            <Label>阻断目标模式</Label>
                            <Textarea value={blockedTargetDraft} onChange={(event) => setBlockedTargetDraft(event.target.value)} className="min-h-[110px]" />
                        </div>
                    </div>
                </div>

                <div className="space-y-3 rounded-2xl border border-border/70 bg-background/60 p-4">
                    <div className="text-sm font-medium">运行时前置策略</div>
                    <div className="grid gap-4 xl:grid-cols-3">
                        {RUNTIME_RULE_META.map((runtimeMeta) => (
                            <div key={runtimeMeta.key} className="space-y-3 rounded-2xl border border-border/60 bg-background/70 p-4">
                                <div className="text-sm font-medium">{runtimeMeta.label}</div>
                                <div className="space-y-2">
                                    <Label>需要人工确认的触发来源</Label>
                                    <Textarea
                                        value={runtimeDrafts[runtimeMeta.key].reviewTriggerSources}
                                        onChange={(event) =>
                                            setRuntimeDrafts((current) => ({
                                                ...current,
                                                [runtimeMeta.key]: { ...current[runtimeMeta.key], reviewTriggerSources: event.target.value },
                                            }))
                                        }
                                        className="min-h-[92px]"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>直接阻断的触发来源</Label>
                                    <Textarea
                                        value={runtimeDrafts[runtimeMeta.key].blockedTriggerSources}
                                        onChange={(event) =>
                                            setRuntimeDrafts((current) => ({
                                                ...current,
                                                [runtimeMeta.key]: { ...current[runtimeMeta.key], blockedTriggerSources: event.target.value },
                                            }))
                                        }
                                        className="min-h-[92px]"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>需要人工确认的范围模式</Label>
                                    <Textarea
                                        value={runtimeDrafts[runtimeMeta.key].reviewScopePatterns}
                                        onChange={(event) =>
                                            setRuntimeDrafts((current) => ({
                                                ...current,
                                                [runtimeMeta.key]: { ...current[runtimeMeta.key], reviewScopePatterns: event.target.value },
                                            }))
                                        }
                                        className="min-h-[92px]"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>直接阻断的范围模式</Label>
                                    <Textarea
                                        value={runtimeDrafts[runtimeMeta.key].blockedScopePatterns}
                                        onChange={(event) =>
                                            setRuntimeDrafts((current) => ({
                                                ...current,
                                                [runtimeMeta.key]: { ...current[runtimeMeta.key], blockedScopePatterns: event.target.value },
                                            }))
                                        }
                                        className="min-h-[92px]"
                                    />
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                <div className="space-y-3 rounded-2xl border border-border/70 bg-background/60 p-4">
                    <div className="text-sm font-medium">群聊危险会话拦截</div>
                    <div className="grid gap-4 xl:grid-cols-2">
                        <div className="space-y-3 rounded-2xl border border-border/60 bg-background/70 p-4">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <Label className="text-sm font-medium">启用群聊危险会话拦截</Label>
                                    <p className="mt-1 text-xs text-muted-foreground">开启后，群聊入站消息会在渠道宿主入口先做风险拦截或审计。</p>
                                </div>
                                <Switch
                                    checked={config.channelGroupGuard.enabled}
                                    onCheckedChange={(checked) =>
                                        setConfig((current) => ({
                                            ...current,
                                            channelGroupGuard: { ...current.channelGroupGuard, enabled: checked },
                                        }))
                                    }
                                />
                            </div>
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <Label className="text-sm font-medium">仅 allowlist 群自动响应</Label>
                                    <p className="mt-1 text-xs text-muted-foreground">不在 allowlist 的群将被拦截或审计。</p>
                                </div>
                                <Switch
                                    checked={config.channelGroupGuard.allowlistOnly}
                                    onCheckedChange={(checked) =>
                                        setConfig((current) => ({
                                            ...current,
                                            channelGroupGuard: { ...current.channelGroupGuard, allowlistOnly: checked },
                                        }))
                                    }
                                />
                            </div>
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <Label className="text-sm font-medium">必须 @ 机器人后继续</Label>
                                    <p className="mt-1 text-xs text-muted-foreground">适合群聊默认不自动响应、仅点名时继续的场景。</p>
                                </div>
                                <Switch
                                    checked={config.channelGroupGuard.requireMention}
                                    onCheckedChange={(checked) =>
                                        setConfig((current) => ({
                                            ...current,
                                            channelGroupGuard: { ...current.channelGroupGuard, requireMention: checked },
                                        }))
                                    }
                                />
                            </div>
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <Label className="text-sm font-medium">仅审计不拦截</Label>
                                    <p className="mt-1 text-xs text-muted-foreground">用于先观察真实群聊风险，再决定是否开启硬拦截。</p>
                                </div>
                                <Switch
                                    checked={config.channelGroupGuard.auditOnly}
                                    onCheckedChange={(checked) =>
                                        setConfig((current) => ({
                                            ...current,
                                            channelGroupGuard: { ...current.channelGroupGuard, auditOnly: checked },
                                        }))
                                    }
                                />
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label>allowlist 群 ID</Label>
                            <Textarea
                                value={channelGroupAllowlistDraft}
                                onChange={(event) => setChannelGroupAllowlistDraft(event.target.value)}
                                className="min-h-[200px]"
                                placeholder={"oc_group_alpha\nwx_room_beta"}
                            />
                        </div>
                    </div>
                </div>

                <div className="space-y-3 rounded-2xl border border-border/70 bg-background/60 p-4">
                    <div className="text-sm font-medium">后置观测策略</div>
                    <div className="grid gap-4 md:grid-cols-3">
                        <div className="space-y-2">
                            <Label>启用的动作族</Label>
                            <Textarea value={postActionEnabledDraft} onChange={(event) => setPostActionEnabledDraft(event.target.value)} className="min-h-[120px]" />
                        </div>
                        <div className="space-y-2">
                            <Label>高亮告警动作族</Label>
                            <Textarea value={postActionHighlightDraft} onChange={(event) => setPostActionHighlightDraft(event.target.value)} className="min-h-[120px]" />
                        </div>
                        <div className="space-y-2">
                            <Label>变更型 HTTP 方法</Label>
                            <Textarea value={postActionMutatingHttpDraft} onChange={(event) => setPostActionMutatingHttpDraft(event.target.value)} className="min-h-[120px]" />
                        </div>
                    </div>
                </div>

                <div className="flex justify-end">
                    <Button type="button" onClick={() => void handleSave()} disabled={saving || loading}>
                        <Save className="mr-2 h-4 w-4" />
                        {saving ? "保存中..." : "保存策略"}
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
}
