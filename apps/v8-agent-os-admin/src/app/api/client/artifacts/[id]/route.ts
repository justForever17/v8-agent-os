import { NextRequest } from "next/server";

import { proxyClientAdminJson } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const { id } = await context.params;
    return proxyClientAdminJson(req, `/memory/artifacts/${encodeURIComponent(id)}`, { method: "GET" });
}
