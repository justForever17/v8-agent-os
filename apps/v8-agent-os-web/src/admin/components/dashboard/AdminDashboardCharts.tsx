"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@admin/components/ui/card";
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, PieChart, Pie, Cell } from "recharts";

const COLORS = ["#0088FE", "#00C49F", "#FFBB28", "#FF8042", "#7C3AED", "#F43F5E", "#14B8A6", "#F97316"];

export type DailyActivityItem = {
    date: string;
    messages: number;
    runs: number;
    invocations: number;
};

export type ModelUsageItem = {
    name: string;
    value: number;
    provider: string;
};

export interface AdminDashboardChartsProps {
    dailyActivity: DailyActivityItem[];
    modelUsage: ModelUsageItem[];
    labels: {
        dailyActivityTitle: string;
        messagesName: string;
        runsName: string;
        invocationsName: string;
        modelUsageTitle: string;
        noDataText: string;
    };
}

export function AdminDashboardCharts({ dailyActivity, modelUsage, labels }: AdminDashboardChartsProps) {
    return (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card className="col-span-1 flex h-[420px] min-h-0 flex-col">
                <CardHeader>
                    <CardTitle>{labels.dailyActivityTitle}</CardTitle>
                </CardHeader>
                <CardContent className="flex-1 min-h-0">
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={dailyActivity}>
                            <CartesianGrid strokeDasharray="3 3" opacity={0.1} />
                            <XAxis dataKey="date" />
                            <YAxis />
                            <Tooltip
                                contentStyle={{ backgroundColor: 'rgba(0,0,0,0.8)', border: 'none', borderRadius: '8px', color: '#fff' }}
                            />
                            <Line type="monotone" dataKey="messages" name={labels.messagesName} stroke="#8884d8" strokeWidth={2} />
                            <Line type="monotone" dataKey="runs" name={labels.runsName} stroke="#82ca9d" strokeWidth={2} />
                            <Line type="monotone" dataKey="invocations" name={labels.invocationsName} stroke="#f59e0b" strokeWidth={2} />
                        </LineChart>
                    </ResponsiveContainer>
                </CardContent>
            </Card>

            <Card className="col-span-1 flex h-[420px] min-h-0 flex-col">
                <CardHeader>
                    <CardTitle>{labels.modelUsageTitle}</CardTitle>
                </CardHeader>
                <CardContent className="flex min-h-0 flex-1 flex-col">
                    <div className="min-h-0 flex-1">
                        {modelUsage.length === 0 ? (
                            <div className="flex h-full items-center justify-center rounded-2xl border border-dashed border-border/70 bg-muted/20 px-4 text-sm text-muted-foreground">
                                {labels.noDataText}
                            </div>
                        ) : (
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie
                                        data={modelUsage}
                                        cx="50%"
                                        cy="50%"
                                        innerRadius={60}
                                        outerRadius={80}
                                        paddingAngle={5}
                                        dataKey="value"
                                    >
                                        {modelUsage.map((entry, index) => (
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
    );
}
