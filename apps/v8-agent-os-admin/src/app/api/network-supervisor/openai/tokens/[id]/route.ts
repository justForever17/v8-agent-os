import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

function relayHeaders() {
    const internalSecret = resolveInternalSecret();
    const headers = new Headers();
    if (internalSecret) {
        headers.set("X-V8-Agent-OS-Secret", internalSecret);
    }
    return headers;
}

export async function DELETE(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    const { id } = await params;
    const response = await fetch(`${resolveEngineBaseUrl()}/network-supervisor/openai/compat/tokens/${encodeURIComponent(id)}`, {
        method: "DELETE",
        cache: "no-store",
        headers: relayHeaders(),
    });
    const payload = await response.text();
    return new NextResponse(payload, {
        status: response.status,
        headers: {
            "Content-Type": response.headers.get("content-type") || "application/json",
            "Cache-Control": "no-store",
        },
    });
}
