import { Suspense } from "react";

import { requireAdminConnection } from "@/lib/server/page-guards";
import SpecApprovalClient, { SpecApprovalLoading } from "./SpecApprovalClient";

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
    const specId = typeof params.specId === "string" ? params.specId : "";
    const stage = typeof params.stage === "string" ? params.stage : "";
    return (
        <Suspense fallback={<SpecApprovalLoading />}>
            <SpecApprovalClient initialWorkspacePath={workspacePath} initialSpecId={specId} initialStage={stage} />
        </Suspense>
    );
}
