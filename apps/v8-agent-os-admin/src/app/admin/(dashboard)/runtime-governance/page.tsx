"use client";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { StatusNotice } from "@/components/admin-shell/StatusNotice";
import { RuntimeGovernanceWorkbench } from "@/components/runtime/RuntimeGovernanceWorkbench";

export default function RuntimeGovernancePage() {
    return (
        <AdminPageShell>
            <AdminPageHeader
                title="运行时治理"
                description="查看运行时状态、治理策略和关闭边界。"
            />

            <StatusNotice
                title="非核心 runtime 关闭后会 cold stop。"
                description="关闭后应释放后台资源，不只隐藏 UI。"
                tone="warning"
            />

            <RuntimeGovernanceWorkbench />
        </AdminPageShell>
    );
}
