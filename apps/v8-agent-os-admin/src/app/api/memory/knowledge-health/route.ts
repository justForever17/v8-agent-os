import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

export const dynamic = "force-dynamic";

export async function GET() {
    try {
        const response = await fetch(`${resolveEngineOrigin()}/v1/memory/knowledge-health`, { cache: "no-store" });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
