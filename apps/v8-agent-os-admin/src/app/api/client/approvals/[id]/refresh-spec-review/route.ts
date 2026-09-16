import { engineFetch } from "@/lib/server/engine-fetch";
import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) return unauthorizedClientJson();
    const { id } = await params;
    try {
        const payload = await req.json();
        const response = await engineFetch(`${resolveEngineBaseUrl()}/approvals/${encodeURIComponent(id)}/refresh-spec-review`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "x-v8-agent-os-user-email": userEmail,
                "x-v8-agent-os-secret": resolveInternalSecret() },
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        return NextResponse.json(data, { status: response.status });
    } catch {
        return NextResponse.json({ error: "Unable to refresh the Spec review." }, { status: 502 });
    }
}
