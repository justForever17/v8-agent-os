import { NextRequest } from "next/server";

import { proxyClientEngineJson } from "@/lib/server/client-proxy";

function buildTarget(req: NextRequest, segments?: string[]) {
    const suffix = (segments || []).map((segment) => encodeURIComponent(segment)).join("/");
    const search = req.nextUrl.search || "";
    return `/mcp/apps${suffix ? `/${suffix}` : ""}${search}`;
}

export async function GET(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    const params = await context.params;
    return proxyClientEngineJson(req, buildTarget(req, params.segments), { method: "GET" });
}

export async function POST(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    const params = await context.params;
    const body = await req.text();
    return proxyClientEngineJson(req, buildTarget(req, params.segments), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
    });
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    const params = await context.params;
    return proxyClientEngineJson(req, buildTarget(req, params.segments), { method: "DELETE" });
}
