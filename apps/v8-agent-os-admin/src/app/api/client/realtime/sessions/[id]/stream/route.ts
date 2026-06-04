import { NextRequest } from "next/server";

import { proxyClientAdminStream } from "@/lib/server/client-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const { id } = await context.params;
    const search = req.nextUrl.search || "";
    return proxyClientAdminStream(
        req,
        `/realtime/sessions/${encodeURIComponent(id)}/stream${search}`,
        "text/event-stream; charset=utf-8",
        { method: "GET" },
    );
}
