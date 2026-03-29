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
            <div className="flex min-h-[280px] items-center justify-center rounded-2xl border border-slate-200 bg-white shadow-sm">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        ),
    }
);

export default function AdvancedGovernancePage() {
    return (
        <AdminPageShell>
            <AdminPageHeader
                title="高级附录"
                description="查看高级治理细节与排障信息。"
            />

            <AdvancedSection
                title="高级细节"
                defaultOpen={false}
            >
                <RuntimeGovernanceWorkbench />
            </AdvancedSection>
        </AdminPageShell>
    );
}
