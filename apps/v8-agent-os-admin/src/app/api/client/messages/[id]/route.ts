import { NextRequest } from "next/server";

import { proxyClientAdminJson } from "@/lib/server/client-proxy";

export async function DELETE(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const { id } = await context.params;
    const query = req.nextUrl.search || "";
    return proxyClientAdminJson(req, `/messages/${encodeURIComponent(id)}${query}`, { method: "DELETE" });
}
