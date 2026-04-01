import Link from "next/link";
import { cookies } from "next/headers";
import { ArrowRight, Bot, Crown, MessageSquare } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { RuntimeConfigWorkbench } from "@/components/runtime/RuntimeConfigWorkbench";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { lt, parseLocale, pickLocalizedText } from "@/lib/locale";

const CHAT_RUNTIME_LINKS = [
    {
        href: "/admin/supervisor",
        title: lt("Lead Prompt", "Lead Prompt"),
        description: lt("查看并调整主理人的 runtime orchestration prompt、默认模型与运行规则。", "Inspect and tune the lead runtime orchestration prompt, default model, and operating rules."),
        icon: Crown,
    },
    {
        href: "/admin/subagents",
        title: lt("Subagents", "Subagents"),
        description: lt("管理子 Agent 的 tool_mode、继承能力、显式工具与并发委派能力。", "Manage subagent tool_mode, inherited capabilities, explicit tools, and parallel delegation."),
        icon: Bot,
    },
];

export default async function ChatRuntimePage() {
    const locale = parseLocale((await cookies()).get("v8-agent-os-locale")?.value) || "zh-CN";

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="CHAT RUNTIME"
                description={lt("主理人、子 Agent、技能继承与聊天编排入口。", "Lead settings, subagents, skill inheritance, and chat orchestration entry.")}
            />
            <div className="space-y-6">
                <RuntimeConfigWorkbench kind="chat" fallbackDisplayName="CHAT RUNTIME" />
                <div className="grid gap-4 lg:grid-cols-2">
                    {CHAT_RUNTIME_LINKS.map((item) => (
                        <Link key={item.href} href={item.href}>
                            <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
                                <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                                    <div className="flex items-center gap-3">
                                        <div className="rounded-2xl bg-sky-50 p-3 text-sky-700">
                                            <item.icon className="h-5 w-5" />
                                        </div>
                                        <CardTitle className="text-base font-semibold text-slate-900">{pickLocalizedText(locale, item.title)}</CardTitle>
                                    </div>
                                    <ArrowRight className="h-4 w-4 text-slate-400" />
                                </CardHeader>
                                <CardContent className="pt-0 text-sm leading-6 text-slate-500">
                                    {pickLocalizedText(locale, item.description)}
                                </CardContent>
                            </Card>
                        </Link>
                    ))}
                </div>
                <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                    <CardContent className="flex items-start gap-3 p-6 text-sm text-slate-500">
                        <MessageSquare className="mt-0.5 h-5 w-5 shrink-0 text-sky-600" />
                        <div>
                            {pickLocalizedText(
                                locale,
                                lt(
                                    "CHAT RUNTIME 负责主理人的提示词、子 Agent、skills/tool route 与多 Agent 调度。Supervisor 与 Subagents 的兼容路径仍然保留，但侧边栏主入口统一收口到这里。",
                                    "CHAT RUNTIME owns the lead prompt, subagents, skills/tool routing, and multi-agent orchestration. Legacy Supervisor/Subagents routes remain available, but the sidebar entry is unified here.",
                                ),
                            )}
                        </div>
                    </CardContent>
                </Card>
            </div>
        </AdminPageShell>
    );
}
