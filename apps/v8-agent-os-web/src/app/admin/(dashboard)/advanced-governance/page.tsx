"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useT } from "@admin/components/providers/LocaleProvider";
import { ADMIN_NAV_GROUPS } from "@admin/lib/admin-navigation";
import { Loader2 } from "lucide-react";

import { AdminPageHeader } from "@admin/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@admin/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@admin/components/admin-shell/AdvancedSection";

const RuntimeGovernanceWorkbench = dynamic(
    () => import("@admin/components/runtime/RuntimeGovernanceWorkbench").then((mod) => mod.RuntimeGovernanceWorkbench),
    {
        loading: () => (
            <div className="flex min-h-[280px] items-center justify-center rounded-2xl border border-border bg-card shadow-sm">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground/80" />
            </div>
        ),
    }
);

export default function AdvancedGovernancePage() {
    const t = useT();
    const routes = ADMIN_NAV_GROUPS.flatMap(group => group.items).filter(item => ["/admin/runtime-governance", "/admin/operations-center", "/admin/system-base", "/admin/projects-workspaces", "/admin/safety-control"].includes(item.href));
    return (
        <AdminPageShell>
            <AdminPageHeader
                title="admin.experience.diagnostics"
                description="app.admin.dashboard.advanced.governance.page.k57c98d38"
            />
            <div className="grid gap-2 sm:grid-cols-2">{routes.map(item => <Link key={item.href} href={item.href} prefetch={false} className="rounded-xl border border-border bg-card p-4 text-sm hover:bg-muted">{t(item.title)}</Link>)}
                <Link href="/admin/stability-strategy" prefetch={false} className="rounded-xl border border-border bg-card p-4 text-sm hover:bg-muted">{t("app.admin.dashboard.stability.strategy.page.kb80fddf8")}</Link>
                <Link href="/admin/memory?tab=config" prefetch={false} className="rounded-xl border border-border bg-card p-4 text-sm hover:bg-muted">{t("components.memory.MemorySectionNav.k0e1a1cef")}</Link>
                <Link href="/admin/memory?tab=runtime" prefetch={false} className="rounded-xl border border-border bg-card p-4 text-sm hover:bg-muted">{t("components.memory.MemorySectionNav.kc9691c8b")}</Link>
            </div>

            <AdvancedSection
                title="app.admin.dashboard.advanced.governance.page.keb9326e5"
                defaultOpen={false}
                keepMounted={false}
            >
                <RuntimeGovernanceWorkbench />
            </AdvancedSection>
        </AdminPageShell>
    );
}
