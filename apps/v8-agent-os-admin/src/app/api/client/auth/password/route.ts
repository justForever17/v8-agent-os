import { NextRequest } from "next/server";

import { proxyClientAdminJson } from "@/lib/server/client-proxy";

export async function POST(req: NextRequest) {
    const body = await req.text();
    return proxyClientAdminJson(req, "/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
    });
}
