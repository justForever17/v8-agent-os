import { NextRequest } from "next/server";

import { proxyClientEngineStream } from "@/lib/server/client-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
    return proxyClientEngineStream(
        req,
        "/sessions/activity-stream",
        "text/event-stream; charset=utf-8",
        { method: "GET" },
    );
}
