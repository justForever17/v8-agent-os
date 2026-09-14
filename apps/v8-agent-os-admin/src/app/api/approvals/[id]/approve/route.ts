import { NextRequest } from "next/server";
import { resolveInternalSecret } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import { proxyEngineCommand } from "@/lib/server/engine-command-proxy";

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) return unauthorizedJson();
    const { id } = await params;
    return proxyEngineCommand(req, `/approvals/${encodeURIComponent(id)}/approve`,
        { approvalId: id, command: "approve" }, {
            "Content-Type": "application/json", "x-v8-agent-os-user-email": userEmail,
            "x-v8-agent-os-secret": resolveInternalSecret(),
        });
}
