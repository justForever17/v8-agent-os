import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

const ENGINE_TARGET = `${resolveEngineBaseUrl()}/network-supervisor/openai/compat/tokens`;

function relayHeaders() {
    const internalSecret = resolveInternalSecret();
    const headers = new Headers({ "Content-Type": "application/json" });
    if (internalSecret) {
        headers.set("X-V8-Agent-OS-Secret", internalSecret);
    }
    return headers;
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    const response = await fetch(ENGINE_TARGET, {
        method: "GET",
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

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    const bodyText = await req.text();
    const response = await fetch(ENGINE_TARGET, {
        method: "POST",
        cache: "no-store",
        headers: relayHeaders(),
        body: bodyText || "{}",
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
