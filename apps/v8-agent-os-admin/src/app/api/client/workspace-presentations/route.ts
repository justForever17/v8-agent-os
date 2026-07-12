import { NextRequest } from "next/server";

import { proxyClientAdminJson } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest) {
    return proxyClientAdminJson(req, "/workspace-presentations", { method: "GET" });
}

export async function PUT(req: NextRequest) {
    const body = await req.json().catch(() => ({}));
    return proxyClientAdminJson(req, "/workspace-presentations", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}
