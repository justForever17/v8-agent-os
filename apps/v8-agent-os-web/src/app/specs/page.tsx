import { Suspense } from "react";

import { requireAdminConnection } from "@/lib/server/page-guards";
import SpecApprovalClient from "./SpecApprovalClient";

export const dynamic = "force-dynamic";

export default async function SpecsPage({
    searchParams,
}: {
    searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
    const params = await searchParams;
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params || {})) {
        if (typeof value === "string") {
            query.set(key, value);
        }
    }
    const nextPath = `/specs${query.toString() ? `?${query.toString()}` : ""}`;
    await requireAdminConnection(nextPath);
    const workspacePath = typeof params.workspace === "string" ? params.workspace : "";
    return (
        <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">正在读取 Spec 审批台…</div>}>
            <SpecApprovalClient initialWorkspacePath={workspacePath} />
        </Suspense>
    );
}
