import Link from "next/link";
import { cookies } from "next/headers";
import { Cable, Clock3 } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { WakeIngressPolicyCard } from "@/components/automation/WakeIngressPolicyCard";
import { createTranslator, resolveText, parseLocale } from "@/lib/locale";

const ENTRY_CARDS = [
    {
        title:"app.admin.dashboard.automation.page.ka206d935",
        href: "/admin/automation/hooks",
        description:"app.admin.dashboard.automation.page.ka2781e5f",
        icon: Cable,
    },
    {
        title:"app.admin.dashboard.automation.page.k8164146c",
        href: "/admin/automation/cron",
        description:"app.admin.dashboard.automation.page.k6edbfeea",
        icon: Clock3,
    },
];

export default async function AutomationOverviewPage() {
    const locale = parseLocale((await cookies()).get("v8-agent-os-locale")?.value) || "zh-CN";
    const t = createTranslator(locale);

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="AUTOMATION RUNTIME"
                description={"app.admin.dashboard.automation.page.kf8a1ca9e"}
            />
            <div className="grid gap-6 lg:grid-cols-2">
                {ENTRY_CARDS.map((item) => {
                    const Icon = item.icon;
                    return (
                        <Link key={item.href} href={item.href} className="block">
                            <ConfigCard title={item.title} description={item.description} className="h-full transition hover:border-slate-300 hover:shadow-sm">
                                <div className="flex items-center gap-3 text-sm text-slate-600">
                                    <Icon className="h-4 w-4 text-slate-500" />
                                    <span>{t("app.admin.dashboard.automation.page.openCard", {
                                        title: resolveText(locale, item.title),
                                    })}</span>
                                </div>
                            </ConfigCard>
                        </Link>
                    );
                })}
                <div className="lg:col-span-2">
                    <WakeIngressPolicyCard />
                </div>
            </div>
        </AdminPageShell>
    );
}
