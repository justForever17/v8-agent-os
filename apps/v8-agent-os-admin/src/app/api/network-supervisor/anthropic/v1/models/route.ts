import { NextRequest, NextResponse } from "next/server";

import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

const ENGINE_TARGET = `${resolveEngineBaseUrl()}/network-supervisor/anthropic/v1/models`;

function copyCompatHeaders(req: NextRequest) {
    const headers = new Headers();
    const auth = req.headers.get("authorization");
    const apiKey = req.headers.get("x-api-key");
    if (auth) headers.set("Authorization", auth);
    if (apiKey) headers.set("x-api-key", apiKey);
    const internalSecret = resolveInternalSecret();
    if (internalSecret) {
        headers.set("X-V8-Agent-OS-Secret", internalSecret);
    }
    return headers;
}

export async function GET(req: NextRequest) {
    try {
        const response = await fetch(ENGINE_TARGET, {
            method: "GET",
            cache: "no-store",
            headers: copyCompatHeaders(req),
        });
        const payload = await response.text();
        return new NextResponse(payload, {
            status: response.status,
            headers: {
                "Content-Type": response.headers.get("content-type") || "application/json",
                "Cache-Control": "no-store",
            },
        });
    } catch (error) {
        console.error("[Admin Network Supervisor Anthropic Models Relay] failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
