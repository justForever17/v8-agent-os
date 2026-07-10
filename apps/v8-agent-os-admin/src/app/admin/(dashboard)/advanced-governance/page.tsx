"use client";

import dynamic from "next/dynamic";
import { Loader2 } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";

const RuntimeGovernanceWorkbench = dynamic(
    () => import("@/components/runtime/RuntimeGovernanceWorkbench").then((mod) => mod.RuntimeGovernanceWorkbench),
    {
        loading: () => (
            <div className="flex min-h-[280px] items-center justify-center rounded-2xl border border-border bg-card shadow-sm">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground/80" />
            </div>
        ),
    }
);

export default function AdvancedGovernancePage() {
    return (
        <AdminPageShell>
            <AdminPageHeader
                title="app.admin.dashboard.advanced.governance.page.k3ae82fd9"
                description="app.admin.dashboard.advanced.governance.page.k57c98d38"
            />

            <AdvancedSection
                title="app.admin.dashboard.advanced.governance.page.keb9326e5"
                defaultOpen={false}
            >
                <RuntimeGovernanceWorkbench />
            </AdvancedSection>
        </AdminPageShell>
    );
}
