import Link from "next/link";
import { cookies } from "next/headers";
import { Activity, ArrowRight, Search } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { ResearchRuntimeLedgerPanel } from "@/components/research/ResearchRuntimeLedgerPanel";
import { RuntimeConfigWorkbench } from "@/components/runtime/RuntimeConfigWorkbench";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { parseLocale, resolveText } from "@/lib/locale";

const RESEARCH_RUNTIME_LINKS = [
    {
        href: "/admin/subagents",
        title: "app.admin.dashboard.research.runtime.page.orchestrationTitle",
        description: "app.admin.dashboard.research.runtime.page.orchestrationDescription",
        icon: Search,
    },
    {
        href: "/admin/runtime-governance?kind=research",
        title: "app.admin.dashboard.research.runtime.page.governanceTitle",
        description: "app.admin.dashboard.research.runtime.page.governanceDescription",
        icon: Activity,
    },
];

export default async function ResearchRuntimePage() {
    const locale = parseLocale((await cookies()).get("v8-agent-os-locale")?.value) || "zh-CN";

    return (
        <AdminPageShell>
            <AdminPageHeader
                title="app.admin.dashboard.research.runtime.page.title"
                description="app.admin.dashboard.research.runtime.page.description"
            />
            <div className="space-y-6">
                <RuntimeConfigWorkbench
                    kind="research"
                    fallbackDisplayName={resolveText(locale, "lib.runtime.admin.researchRuntime")}
                    governanceHref="/admin/runtime-governance?kind=research"
                />
                <ResearchRuntimeLedgerPanel />
                <div className="grid gap-4 lg:grid-cols-2">
                    {RESEARCH_RUNTIME_LINKS.map((item) => (
                        <Link key={item.href} href={item.href}>
                            <Card className="rounded-3xl border-slate-200 bg-white/95 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
                                <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                                    <div className="flex items-center gap-3">
                                        <div className="rounded-2xl bg-sky-50 p-3 text-sky-700">
                                            <item.icon className="h-5 w-5" />
                                        </div>
                                        <CardTitle className="text-base font-semibold text-slate-900">
                                            {resolveText(locale, item.title)}
                                        </CardTitle>
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
                        <Search className="mt-0.5 h-5 w-5 shrink-0 text-sky-600" />
                        <div>{resolveText(locale, "app.admin.dashboard.research.runtime.page.runtimeNote")}</div>
                    </CardContent>
                </Card>
            </div>
        </AdminPageShell>
    );
}
