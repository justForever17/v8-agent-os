import Link from "next/link";
import { cookies } from "next/headers";
import { ArrowRight, Bot, Crown, MessageSquare } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { RuntimeConfigWorkbench } from "@/components/runtime/RuntimeConfigWorkbench";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { parseLocale, resolveText } from "@/lib/locale";

const CHAT_RUNTIME_LINKS = [
    {
        href: "/admin/supervisor",
        title: "app.admin.dashboard.chat.runtime.page.kbae659f3",
        description: "app.admin.dashboard.chat.runtime.page.k2cada92c",
        icon: Crown,
    },
    {
        href: "/admin/subagents",
        title: "app.admin.dashboard.chat.runtime.page.k0354845a",
        description: "app.admin.dashboard.chat.runtime.page.kba4cdd66",
        icon: Bot,
    },
];

export default async function ChatRuntimePage() {
    const locale = parseLocale((await cookies()).get("v8-agent-os-locale")?.value) || "zh-CN";

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="CHAT RUNTIME"
                description={"app.admin.dashboard.chat.runtime.page.keea91eab"}
            />
            <div className="space-y-6">
                <RuntimeConfigWorkbench kind="chat" fallbackDisplayName="CHAT RUNTIME" showGovernanceLink={false} />
                <div className="grid gap-4 lg:grid-cols-2">
                    {CHAT_RUNTIME_LINKS.map((item) => (
                        <Link key={item.href} href={item.href}>
                            <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
                                <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                                    <div className="flex items-center gap-3">
                                        <div className="rounded-2xl bg-sky-50 p-3 text-sky-700">
                                            <item.icon className="h-5 w-5" />
                                        </div>
                                        <CardTitle className="text-base font-semibold text-slate-900">{resolveText(locale, item.title)}</CardTitle>
                                    </div>
                                    <ArrowRight className="h-4 w-4 text-slate-400" />
                                </CardHeader>
                                <CardContent className="pt-0 text-sm leading-6 text-slate-500">
                                    {resolveText(locale, item.description)}
                                </CardContent>
                            </Card>
                        </Link>
                    ))}
                </div>
                <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm">
                    <CardContent className="flex items-start gap-3 p-6 text-sm text-slate-500">
                        <MessageSquare className="mt-0.5 h-5 w-5 shrink-0 text-sky-600" />
                        <div>
                            {resolveText(locale, "app.admin.dashboard.chat.runtime.page.runtimeNote")}
                        </div>
                    </CardContent>
                </Card>
            </div>
        </AdminPageShell>
    );
}
