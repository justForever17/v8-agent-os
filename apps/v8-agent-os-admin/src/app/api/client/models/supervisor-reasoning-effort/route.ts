import { NextRequest } from "next/server";

import { proxyClientEngineJson } from "@/lib/server/client-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
    const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
    const suffix = sessionId ? `?sessionId=${encodeURIComponent(sessionId)}` : "";
    return proxyClientEngineJson(req, `/models/supervisor-reasoning-effort${suffix}`);
}

export async function PATCH(req: NextRequest) {
    return proxyClientEngineJson(req, "/models/supervisor-reasoning-effort", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: await req.text(),
    });
}
