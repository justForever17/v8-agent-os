import { NextRequest } from "next/server";

import { proxyClientAdminJson } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest) {
    return proxyClientAdminJson(req, "/auth/profile", { method: "GET" });
}

export async function PATCH(req: NextRequest) {
    const body = await req.text();
    return proxyClientAdminJson(req, "/auth/profile", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body,
    });
}
