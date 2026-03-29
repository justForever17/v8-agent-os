import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { RuntimeConfigWorkbench } from "@/components/runtime/RuntimeConfigWorkbench";
import { lt } from "@/lib/locale";

export default function WorkflowRuntimePage() {
    return (
        <AdminPageShell>
            <AdminPageHeader
                title="WorkflowRuntime"
                description={lt("管理 WorkflowRuntime 的状态入口。", "Manage WorkflowRuntime status and entry.")}
            />
            <RuntimeConfigWorkbench kind="workflow" fallbackDisplayName="WorkflowRuntime" />
        </AdminPageShell>
    );
}
