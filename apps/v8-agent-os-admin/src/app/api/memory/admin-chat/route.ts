import { NextRequest, NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_BASE = resolveEngineOrigin();

export async function POST(req: NextRequest) {
    const body = await req.json();

    const engineRes = await fetch(`${ENGINE_BASE}/v1/memory/admin-chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });

    if (!engineRes.ok || !engineRes.body) {
        return NextResponse.json({ error: "Engine unavailable" }, { status: 502 });
    }

    // 透传流式响应
    return new NextResponse(engineRes.body, {
        headers: {
            "Content-Type": "application/x-ndjson",
            "Transfer-Encoding": "chunked",
            "Cache-Control": "no-cache",
        },
    });
}
