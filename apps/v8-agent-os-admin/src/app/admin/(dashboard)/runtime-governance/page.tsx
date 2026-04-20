"use client";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { RuntimeGovernanceWorkbench } from "@/components/runtime/RuntimeGovernanceWorkbench";

export default function RuntimeGovernancePage() {
    return (
        <AdminPageShell>
            <AdminPageHeader
                title="app.admin.dashboard.runtime.governance.page.ke6fff179"
                description="app.admin.dashboard.runtime.governance.page.kdfdc5c07"
            />

            <StatusNotice
                title="app.admin.dashboard.runtime.governance.page.kd8f17ad5"
                description="app.admin.dashboard.runtime.governance.page.k44283a5d"
                tone="warning"
            />

            <RuntimeGovernanceWorkbench />
        </AdminPageShell>
    );
}
