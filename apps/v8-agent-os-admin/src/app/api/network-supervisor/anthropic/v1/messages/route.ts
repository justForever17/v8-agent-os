import { NextRequest, NextResponse } from "next/server";

import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

const ENGINE_TARGET = `${resolveEngineBaseUrl()}/network-supervisor/anthropic/v1/messages`;

function copyCompatHeaders(req: NextRequest) {
    const headers = new Headers();
    const contentType = req.headers.get("content-type") || "application/json";
    headers.set("Content-Type", contentType);
    const auth = req.headers.get("authorization");
    const apiKey = req.headers.get("x-api-key");
    if (auth) headers.set("Authorization", auth);
    if (apiKey) headers.set("x-api-key", apiKey);
    const projectId = req.headers.get("x-v8-project-id");
    const workspaceId = req.headers.get("x-v8-workspace-id");
    const scopeHint = req.headers.get("x-v8-scope-hint");
    if (projectId) headers.set("x-v8-project-id", projectId);
    if (workspaceId) headers.set("x-v8-workspace-id", workspaceId);
    if (scopeHint) headers.set("x-v8-scope-hint", scopeHint);
    const internalSecret = resolveInternalSecret();
    if (internalSecret) {
        headers.set("X-V8-Agent-OS-Secret", internalSecret);
    }
    return headers;
}

export async function POST(req: NextRequest) {
    try {
        const bodyText = await req.text();
        const response = await fetch(ENGINE_TARGET, {
            method: "POST",
            cache: "no-store",
            headers: copyCompatHeaders(req),
            body: bodyText,
        });

        const contentType = response.headers.get("content-type") || "application/json";
        if (contentType.includes("text/event-stream")) {
            return new Response(response.body, {
                status: response.status,
                headers: {
                    "Content-Type": contentType,
                    "Cache-Control": "no-cache, no-transform",
                    Connection: "keep-alive",
                },
            });
        }

        const payload = await response.text();
        return new NextResponse(payload, {
            status: response.status,
            headers: {
                "Content-Type": contentType,
                "Cache-Control": "no-store",
            },
        });
    } catch (error) {
        console.error("[Admin Network Supervisor Anthropic Relay] failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
