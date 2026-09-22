import { NextRequest } from "next/server";
import { resolveInternalSecret } from "@admin/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@admin/lib/server/request-auth";
import { proxyEngineCommand } from "@admin/lib/server/engine-command-proxy";

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) return unauthorizedJson();
    const { id } = await params;
    return proxyEngineCommand(req, `/approvals/${encodeURIComponent(id)}/reject`,
        { approvalId: id, command: "reject" }, {
            "Content-Type": "application/json", "x-v8-agent-os-user-email": userEmail,
            "x-v8-agent-os-secret": resolveInternalSecret(),
        });
}
