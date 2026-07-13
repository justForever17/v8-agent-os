import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
    try {
        const limit = new URL(request.url).searchParams.get("limit") || "100";
        const response = await fetch(
            `${resolveEngineOrigin()}/v1/memory/knowledge-resolution-candidates?limit=${encodeURIComponent(limit)}`,
            { cache: "no-store" },
        );
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
