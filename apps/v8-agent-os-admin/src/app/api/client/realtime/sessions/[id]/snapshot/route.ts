import { NextRequest } from "next/server";

import { proxyClientAdminJson } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const { id } = await context.params;
    const search = req.nextUrl.search || "";
    return proxyClientAdminJson(req, `/realtime/sessions/${encodeURIComponent(id)}/snapshot${search}`, { method: "GET" });
}
