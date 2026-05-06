import { NextRequest } from "next/server";

import { proxyClientEngineJson } from "@/lib/server/client-proxy";

function buildTargetPath(req: NextRequest) {
    const target = new URL("/workspace/folders", "http://engine.local");
    const search = req.nextUrl.searchParams;
    for (const key of ["path", "maxDepth", "maxChildren", "cursor"]) {
        const value = search.get(key);
        if (value !== null) target.searchParams.set(key, value);
    }
    return `${target.pathname}${target.search}`;
}

export async function GET(req: NextRequest) {
    return proxyClientEngineJson(req, buildTargetPath(req), { method: "GET" });
}

export async function POST(req: NextRequest) {
    const body = await req.json().catch(() => ({}));
    return proxyClientEngineJson(req, "/workspace/folders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}
