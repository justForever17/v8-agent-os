import { NextRequest } from "next/server";

import { proxyClientEngineJson } from "@/lib/server/client-proxy";


function target(segments?: string[]) {
    const suffix = (segments || []).map((segment) => encodeURIComponent(segment)).join("/");
    return `/ui/actions${suffix ? `/${suffix}` : ""}`;
}

export async function GET(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxyClientEngineJson(req, target((await context.params).segments), {
        method: "GET",
        headers: { "x-v8-session-id": req.headers.get("x-v8-session-id") || "" },
    });
}

export async function POST(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxyClientEngineJson(req, target((await context.params).segments), {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "x-v8-session-id": req.headers.get("x-v8-session-id") || "",
        },
        body: await req.text(),
    });
}
