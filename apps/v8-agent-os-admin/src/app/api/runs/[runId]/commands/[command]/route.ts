import { NextRequest } from "next/server";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import { proxyEngineCommand } from "@/lib/server/engine-command-proxy";

export async function POST(req: NextRequest, { params }: { params: Promise<{ runId: string; command: string }> }) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) return unauthorizedJson();
    const { runId, command } = await params;
    return proxyEngineCommand(req, `/runs/${encodeURIComponent(runId)}/commands/${encodeURIComponent(command)}`,
        { runId, command }, { "Content-Type": "application/json", "x-v8-agent-os-user-email": userEmail });
}
