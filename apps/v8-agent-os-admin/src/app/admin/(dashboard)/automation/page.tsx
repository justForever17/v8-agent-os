import Link from "next/link";
import { cookies } from "next/headers";
import { Cable, Clock3 } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { SupervisorHeartbeatCard } from "@/components/automation/SupervisorHeartbeatCard";
import { localizeAdminText } from "@/lib/admin-copy";
import { lt } from "@/lib/locale";
import { parseLocale } from "@/lib/locale";

const ENTRY_CARDS = [
    {
        title: "动作钩子",
        href: "/admin/automation/hooks",
        description: "管理生命周期钩子、触发动作和系统自动化入口。",
        icon: Cable,
    },
    {
        title: "定时任务",
        href: "/admin/automation/cron",
        description: "管理 Cron、计划任务和周期性执行。",
        icon: Clock3,
    },
];

export default async function AutomationOverviewPage() {
    const locale = parseLocale((await cookies()).get("v8-agent-os-locale")?.value) || "zh-CN";
    const t = (value: string) => localizeAdminText(locale, value);

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="AUTOMATION RUNTIME"
                description={lt("管理 Hooks、Cron 与系统心跳唤醒。", "Manage hooks, cron jobs, and supervisor heartbeat automation.")}
            />
            <div className="grid gap-6 lg:grid-cols-2">
                {ENTRY_CARDS.map((item) => {
                    const Icon = item.icon;
                    return (
                        <Link key={item.href} href={item.href} className="block">
                            <ConfigCard title={item.title} description={item.description} className="h-full transition hover:border-slate-300 hover:shadow-sm">
                                <div className="flex items-center gap-3 text-sm text-slate-600">
                                    <Icon className="h-4 w-4 text-slate-500" />
                                    <span>{locale === "en" ? `Open ${t(item.title)}` : `进入 ${item.title}`}</span>
                                </div>
                            </ConfigCard>
                        </Link>
                    );
                })}
                <div className="lg:col-span-2">
                    <SupervisorHeartbeatCard />
                </div>
            </div>
        </AdminPageShell>
    );
}
