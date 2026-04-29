"use client";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { useEffect, useState } from "react";
import { XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line, PieChart, Pie, Cell } from "recharts";
import { Activity, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { formatLocalDateTime } from "@/lib/time";
import { useT } from "@/components/providers/LocaleProvider";
import { RuntimeDashboardCards } from "@/components/runtime/RuntimeDashboardCards";

const COLORS = ["#0088FE", "#00C49F", "#FFBB28", "#FF8042", "#7C3AED", "#F43F5E", "#14B8A6", "#F97316"];

type DashboardData = {
    stats: {
        totalSessions: number;
        totalMessages: number;
        totalRuns: number;
        totalInvocations: number;
        pendingApprovals: number;
        activeRuns: number;
        recentWindowTokens: number;
        recentWindowEstimatedCost: number;
    };
    charts: {
        dailyActivity: Array<{
            date: string;
            messages: number;
            runs: number;
            invocations: number;
            tokens: number;
        }>;
        modelUsage: Array<{
            name: string;
            provider: string;
            value: number;
            tokens: number;
            cost: number;
        }>;
        providerHealth: Array<{
            providerId: string;
            providerName: string;
            events: number;
            successCount: number;
            errorCount: number;
            avgLatencyMs: number;
            lastSeenAt?: string;
        }>;
    };
    recentInvocations: Array<{
        id: string;
        model_id: string;
        provider_name?: string;
        status: string;
        total_tokens: number;
        latency_ms: number;
        started_at?: string;
        role?: string;
    }>;
};

const EMPTY_DATA: DashboardData = {
    stats: {
        totalSessions: 0,
        totalMessages: 0,
        totalRuns: 0,
        totalInvocations: 0,
        pendingApprovals: 0,
        activeRuns: 0,
        recentWindowTokens: 0,
        recentWindowEstimatedCost: 0,
    },
    charts: {
        dailyActivity: [],
        modelUsage: [],
        providerHealth: [],
    },
    recentInvocations: [],
};

function formatWhen(value: string | undefined, fallback: string) {
    return formatLocalDateTime(value, { includeYear: false, includeSeconds: true, fallback });
}

export default function DashboardPage() {
    const t = useT();
    const [data, setData] = useState<DashboardData>(EMPTY_DATA);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetch("/api/stats", { cache: "no-store" })
            .then(res => res.json())
            .then((payload: DashboardData) => {
                setData(payload || EMPTY_DATA);
                setLoading(false);
            })
            .catch(err => {
                console.error(err);
                setLoading(false);
            });
    }, []);

    if (loading) return <div className="flex h-96 items-center justify-center"><Loader2 className="h-8 w-8 animate-spin" /></div>;

    return (
        <div className="space-y-8">
            <div>
                <h1 className="text-3xl font-bold">{t("app.admin.dashboard.page.kfe1bd304")}</h1>
                <p className="mt-2 text-sm text-muted-foreground">
                    {t("app.admin.dashboard.page.k77dba2de")}
                </p>
            </div>

            <RuntimeDashboardCards />

            {/* Key Metrics */}
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-4">
                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.page.kedf0b1c7")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-4xl font-bold">{data.stats.totalSessions}</p>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.page.kfb7fb55f")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-4xl font-bold">{data.stats.totalRuns}</p>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.page.kebfd23a5")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-4xl font-bold">{data.stats.totalInvocations}</p>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.page.k3a0aaa55")}</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        <p className="text-4xl font-bold">{data.stats.pendingApprovals}</p>
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Activity className="h-4 w-4" />
                            {t("app.admin.dashboard.page.ka8ff6de1")}: {data.stats.activeRuns}
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Charts */}
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <Card className="col-span-1 flex h-[420px] min-h-0 flex-col">
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.page.kad5f5f05")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.page.k1bfef4bf")}</CardDescription>
                    </CardHeader>
                    <CardContent className="flex-1 min-h-0">
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={data.charts.dailyActivity}>
                                <CartesianGrid strokeDasharray="3 3" opacity={0.1} />
                                <XAxis dataKey="date" />
                                <YAxis />
                                <Tooltip
                                    contentStyle={{ backgroundColor: 'rgba(0,0,0,0.8)', border: 'none', borderRadius: '8px', color: '#fff' }}
                                />
                                <Line type="monotone" dataKey="messages" name={t("app.admin.dashboard.page.kc199335d")} stroke="#8884d8" strokeWidth={2} />
                                <Line type="monotone" dataKey="runs" name={t("app.admin.dashboard.page.k3f539477")} stroke="#82ca9d" strokeWidth={2} />
                                <Line type="monotone" dataKey="invocations" name={t("app.admin.dashboard.page.k1a3ec470")} stroke="#f59e0b" strokeWidth={2} />
                            </LineChart>
                        </ResponsiveContainer>
                    </CardContent>
                </Card>

                <Card className="col-span-1 flex h-[420px] min-h-0 flex-col">
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.page.kd35fe722")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.page.k9eacfd89")}</CardDescription>
                    </CardHeader>
                    <CardContent className="flex min-h-0 flex-1 flex-col">
                        <div className="min-h-0 flex-1">
                            {data.charts.modelUsage.length === 0 ? (
                                <div className="flex h-full items-center justify-center rounded-2xl border border-dashed border-border/70 bg-muted/20 px-4 text-sm text-muted-foreground">
                                    {t("app.admin.dashboard.page.k393204db")}
                                </div>
                            ) : (
                                <ResponsiveContainer width="100%" height="100%">
                                    <PieChart>
                                        <Pie
                                            data={data.charts.modelUsage}
                                            cx="50%"
                                            cy="50%"
                                            innerRadius={60}
                                            outerRadius={80}
                                            paddingAngle={5}
                                            dataKey="value"
                                        >
                                            {data.charts.modelUsage.map((entry, index) => (
                                                <Cell key={`${entry.provider}:${entry.name}:${index}`} fill={COLORS[index % COLORS.length]} />
                                            ))}
                                        </Pie>
                                        <Tooltip />
                                    </PieChart>
                                </ResponsiveContainer>
                            )}
                        </div>
                    </CardContent>
                </Card>
            </div>

            <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_1fr]">
                <Card className="flex h-[520px] min-h-0 flex-col">
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.page.k5ba34231")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.page.k227a8742")}</CardDescription>
                    </CardHeader>
                    <CardContent className="flex-1 space-y-3 overflow-y-auto pr-1">
                        {data.charts.providerHealth.length === 0 ? (
                            <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 px-4 py-8 text-sm text-muted-foreground">
                                {t("app.admin.dashboard.page.ka9ef675b")}
                            </div>
                        ) : data.charts.providerHealth.map((item) => (
                            <div key={item.providerId} className="rounded-2xl border border-border/70 bg-background/70 p-4">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <div className="text-sm font-medium">{item.providerName}</div>
                                        <div className="mt-1 text-xs text-muted-foreground">
                                            {t("app.admin.dashboard.page.k2b47c0b8")} {item.events} · {t("app.admin.dashboard.page.kb475dfb9")} {item.avgLatencyMs} ms
                                        </div>
                                    </div>
                                    <Badge variant={item.errorCount > 0 ? "secondary" : "default"}>
                                        {item.errorCount > 0
                                            ? `${t("app.admin.dashboard.page.kdf52ae72")} ${item.errorCount}`
                                            : `${t("app.admin.dashboard.page.k0cbafd78")} ${item.successCount}`}
                                    </Badge>
                                </div>
                            </div>
                        ))}
                    </CardContent>
                </Card>

                <Card className="flex h-[520px] min-h-0 flex-col">
                    <CardHeader>
                        <CardTitle>{t("app.admin.dashboard.page.kc83084c3")}</CardTitle>
                        <CardDescription>{t("app.admin.dashboard.page.keafd86a3")}</CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-1 min-h-0 flex-col space-y-3">
                        <div className="grid grid-cols-2 gap-3">
                            <div className="rounded-2xl border border-border/70 bg-muted/20 p-4">
                                <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">{t("app.admin.dashboard.page.k4ad04328")}</div>
                                <div className="mt-2 text-2xl font-semibold">{data.stats.recentWindowTokens}</div>
                            </div>
                            <div className="rounded-2xl border border-border/70 bg-muted/20 p-4">
                                <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">{t("app.admin.dashboard.page.k8d68c49c")}</div>
                                <div className="mt-2 text-2xl font-semibold">{data.stats.recentWindowEstimatedCost.toFixed(4)}</div>
                            </div>
                        </div>
                        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
                            {data.recentInvocations.length === 0 ? (
                                <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 px-4 py-8 text-sm text-muted-foreground">
                                    {t("app.admin.dashboard.page.k393204db")}
                                </div>
                            ) : data.recentInvocations.map((item) => (
                                <div key={item.id} className="rounded-2xl border border-border/70 bg-background/70 p-4">
                                    <div className="flex items-center justify-between gap-3">
                                        <div>
                                            <div className="text-sm font-medium">{item.model_id}</div>
                                            <div className="mt-1 text-xs text-muted-foreground">
                                                {item.provider_name || t("app.admin.dashboard.page.k83399ef1")} · {item.role || t("app.admin.dashboard.page.kcdbb6b46")} · {formatWhen(item.started_at, t("app.admin.dashboard.page.kba48e747"))}
                                            </div>
                                        </div>
                                        <div className="text-right">
                                            <Badge variant={item.status === "completed" ? "default" : "secondary"}>
                                                {item.status}
                                            </Badge>
                                            <div className="mt-1 text-xs text-muted-foreground">
                                                {item.total_tokens} Tokens · {Math.round(item.latency_ms)} ms
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            </div>

        </div>
    );
}
