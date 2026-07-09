import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity, proxyEngineJson } from "@/lib/server/engine-proxy";

function buildQuery(req: NextRequest) {
    const params = new URLSearchParams();
    for (const key of ["workspacePath", "workspaceId", "projectId", "sessionId"]) {
        const value = req.nextUrl.searchParams.get(key);
        if (value) params.set(key, value);
    }
    const query = params.toString();
    return query ? `?${query}` : "";
}

function normalizeError(data: unknown) {
    const payload = (data && typeof data === "object") ? data as Record<string, unknown> : {};
    const detail = payload.detail;
    if (detail && typeof detail === "object") {
        return { ...(detail as Record<string, unknown>), error: String((detail as Record<string, unknown>).error || (detail as Record<string, unknown>).kind || "workspace_rules_failed") };
    }
    return payload;
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const { response, data } = await proxyEngineJson(`/workspace/agents-rules${buildQuery(req)}`);
    return NextResponse.json(response.ok ? data : normalizeError(data), { status: response.status });
}

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const body = await req.json().catch(() => ({}));
    const { response, data } = await proxyEngineJson("/workspace/agents-rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    return NextResponse.json(response.ok ? data : normalizeError(data), { status: response.status });
}
