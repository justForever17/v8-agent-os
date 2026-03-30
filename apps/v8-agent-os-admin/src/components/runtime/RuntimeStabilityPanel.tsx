"use client";

import { useCallback, useEffect, useState } from "react";
import { Database, Loader2, Save, ShieldCheck, TrafficCone } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/use-toast";

type SessionLanePolicy = "queue" | "reject" | "interrupt_then_replace";

type RuntimeStabilityPayload = {
    strictSupervisorDurability?: boolean;
    sessionLanePolicy?: SessionLanePolicy;
    paths?: {
        configPath?: string;
        stateDbPath?: string;
        checkpointDbPath?: string;
    };
    summaries?: {
        strictSupervisorDurability?: string;
        sessionLanePolicy?: Record<string, string>;
    };
};

const POLICY_OPTIONS: Array<{
    value: SessionLanePolicy;
    title: string;
    description: string;
}> = [
    {
        value: "queue",
        title: "稳妥排队",
        description: "同一会话只跑一个任务，后来的任务排队等待，最适合长期稳定运行。",
    },
    {
        value: "reject",
        title: "忙时拒绝",
        description: "当前会话忙碌时直接拒绝新任务，避免互踩，但会牺牲连续性。",
    },
    {
        value: "interrupt_then_replace",
        title: "抢占替换",
        description: "新任务先打断旧任务再接管会话，只适合强交互或强时效场景。",
    },
];

export function RuntimeStabilityPanel() {
    const { toast } = useToast();
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [config, setConfig] = useState<RuntimeStabilityPayload>({
        strictSupervisorDurability: true,
        sessionLanePolicy: "queue",
        paths: {},
        summaries: {},
    });

    const loadConfig = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetch("/api/runtime-stability", { cache: "no-store" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(data?.detail || data?.error || "加载运行稳定性配置失败");
            }
            setConfig({
                strictSupervisorDurability: Boolean(data?.strictSupervisorDurability ?? true),
                sessionLanePolicy: (data?.sessionLanePolicy || "queue") as SessionLanePolicy,
                paths: data?.paths || {},
                summaries: data?.summaries || {},
            });
        } catch (error) {
            toast({
                variant: "destructive",
                title: "加载失败",
                description: error instanceof Error ? error.message : "未知错误",
            });
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => {
        void loadConfig();
    }, [loadConfig]);

    const saveConfig = async () => {
        setSaving(true);
        try {
            const res = await fetch("/api/runtime-stability", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    strictSupervisorDurability: Boolean(config.strictSupervisorDurability),
                    sessionLanePolicy: config.sessionLanePolicy || "queue",
                }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(data?.detail || data?.error || "保存运行稳定性配置失败");
            }
            toast({
                title: "已保存",
                description: "新的稳定性策略已经写入 Engine 配置源。",
            });
            setConfig({
                strictSupervisorDurability: Boolean(data?.strictSupervisorDurability ?? true),
                sessionLanePolicy: (data?.sessionLanePolicy || "queue") as SessionLanePolicy,
                paths: data?.paths || {},
                summaries: data?.summaries || {},
            });
        } catch (error) {
            toast({
                variant: "destructive",
                title: "保存失败",
                description: error instanceof Error ? error.message : "未知错误",
            });
        } finally {
            setSaving(false);
        }
    };

    if (loading) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">运行稳定性</h1>
                    <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
                        这里管理长期任务是否稳、是否会互相打断，以及保存后是否容易恢复。
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Badge variant="outline">长期任务</Badge>
                    <Badge variant="secondary">保存后生效</Badge>
                </div>
            </div>

            <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
                <Card className="border-border/60">
                    <CardHeader>
                        <div className="flex items-center gap-2">
                            <ShieldCheck className="h-5 w-5 text-emerald-600" />
                            <CardTitle>主理人持久化保护</CardTitle>
                        </div>
                        <CardDescription>
                            {config.summaries?.strictSupervisorDurability || "要求主理人的长任务必须显式使用持久化检查点，避免静默退回临时内存模式。"}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex items-center justify-between rounded-xl border border-border/60 bg-muted/30 px-4 py-3">
                            <div className="space-y-1">
                                <div className="text-sm font-medium">禁止 MemorySaver 回退</div>
                                <div className="text-xs text-muted-foreground">
                                    建议长期保持开启。关闭后，某些旧入口可能重新回到仅内存级 checkpointer。
                                </div>
                            </div>
                            <Switch
                                checked={Boolean(config.strictSupervisorDurability)}
                                onCheckedChange={(checked) => setConfig((current) => ({ ...current, strictSupervisorDurability: checked }))}
                            />
                        </div>

                        <div className="rounded-xl border border-border/60 bg-background px-4 py-3 text-sm">
                            <div className="font-medium">当前配置文件</div>
                            <div className="mt-1 font-mono text-xs text-muted-foreground">{config.paths?.configPath || "~/.v8-agent-os/config.json#runtimeStability"}</div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-border/60">
                    <CardHeader>
                        <div className="flex items-center gap-2">
                            <Database className="h-5 w-5 text-sky-600" />
                            <CardTitle>持久化落点</CardTitle>
                        </div>
                        <CardDescription>运行账本和图检查点已经拆成两个文件，减少锁竞争和语义混杂。</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3 text-sm">
                        <div className="rounded-xl border border-border/60 bg-muted/30 px-4 py-3">
                            <div className="font-medium">运行账本</div>
                            <div className="mt-1 font-mono text-xs text-muted-foreground">{config.paths?.stateDbPath || "~/.v8-agent-os/state.db"}</div>
                        </div>
                        <div className="rounded-xl border border-border/60 bg-muted/30 px-4 py-3">
                            <div className="font-medium">Graph Checkpoint</div>
                            <div className="mt-1 font-mono text-xs text-muted-foreground">{config.paths?.checkpointDbPath || "~/.v8-agent-os/checkpoints.db"}</div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            <Card className="border-border/60">
                <CardHeader>
                    <div className="flex items-center gap-2">
                        <TrafficCone className="h-5 w-5 text-amber-600" />
                        <CardTitle>同会话任务策略</CardTitle>
                    </div>
                    <CardDescription>
                        {config.summaries?.sessionLanePolicy?.[config.sessionLanePolicy || "queue"] || "同一会话一次只允许一个 authoritative run。"}
                    </CardDescription>
                </CardHeader>
                <CardContent className="grid gap-3 lg:grid-cols-3">
                    {POLICY_OPTIONS.map((option) => {
                        const active = (config.sessionLanePolicy || "queue") === option.value;
                        return (
                            <button
                                key={option.value}
                                type="button"
                                className={cn(
                                    "rounded-2xl border px-4 py-4 text-left transition-colors",
                                    active
                                        ? "border-emerald-500/60 bg-emerald-500/10 shadow-sm"
                                        : "border-border/60 bg-background hover:border-foreground/20 hover:bg-muted/30"
                                )}
                                onClick={() => setConfig((current) => ({ ...current, sessionLanePolicy: option.value }))}
                            >
                                <div className="text-sm font-semibold">{option.title}</div>
                                <div className="mt-2 text-xs leading-5 text-muted-foreground">{option.description}</div>
                            </button>
                        );
                    })}
                </CardContent>
            </Card>

            <div className="flex flex-wrap items-center justify-end gap-2">
                <Button variant="outline" onClick={() => void loadConfig()} disabled={saving}>
                    重新读取
                </Button>
                <Button onClick={() => void saveConfig()} disabled={saving}>
                    {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                    保存稳定性配置
                </Button>
            </div>
        </div>
    );
}
