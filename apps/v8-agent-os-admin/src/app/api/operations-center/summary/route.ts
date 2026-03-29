import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const [{ data: approvals }, { data: runs }, { data: health }] = await Promise.all([
            proxyEngineJson("/approvals"),
            proxyEngineJson("/runs?limit=12"),
            proxyEngineJson("/health"),
        ]);

        const approvalItems = Array.isArray((approvals as { approvals?: unknown[] })?.approvals)
            ? ((approvals as { approvals?: unknown[] }).approvals || [])
            : [];
        const runItems = Array.isArray((runs as { runs?: Array<{ status?: string }> })?.runs)
            ? ((runs as { runs?: Array<{ status?: string }> }).runs || [])
            : [];

        const runningCount = runItems.filter((run) => run.status === "running").length;
        const recoverableCount = runItems.filter((run) => ["paused", "failed", "waiting_input", "cancelled"].includes(run.status || "")).length;

        return NextResponse.json({
            pendingApprovals: approvalItems.length,
            recentRuns: runItems.length,
            runningCount,
            recoverableCount,
            health: health || {},
        });
    } catch (error) {
        console.error("[Operations Center] Failed to load summary:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
