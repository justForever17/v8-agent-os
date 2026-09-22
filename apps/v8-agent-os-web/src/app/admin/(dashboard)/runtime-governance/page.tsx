"use client";

import { AdminPageHeader } from "@admin/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@admin/components/admin-shell/AdminPageShell";
import { StatusNotice } from "@admin/components/admin-shell/StatusNotice";
import { RuntimeGovernanceWorkbench } from "@admin/components/runtime/RuntimeGovernanceWorkbench";

export default function RuntimeGovernancePage() {
    return (
        <AdminPageShell className="max-w-none">
            <AdminPageHeader
                title="app.admin.dashboard.runtime.governance.page.ke6fff179"
                description="app.admin.dashboard.runtime.governance.page.kdfdc5c07"
            />

            <StatusNotice
                title="app.admin.dashboard.runtime.governance.page.kd8f17ad5"
                description="app.admin.dashboard.runtime.governance.page.k44283a5d"
                tone="warning"
            />

            <RuntimeGovernanceWorkbench embedded />
        </AdminPageShell>
    );
}
