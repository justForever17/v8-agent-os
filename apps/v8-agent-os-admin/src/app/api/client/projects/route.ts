import { NextRequest } from "next/server";

import { proxyClientAdminJson } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest) {
    return proxyClientAdminJson(req, "/projects", { method: "GET" });
}

export async function POST(req: NextRequest) {
    const body = await req.json().catch(() => ({}));
    return proxyClientAdminJson(req, "/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}
