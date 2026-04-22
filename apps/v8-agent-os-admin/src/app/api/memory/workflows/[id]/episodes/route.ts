import { NextResponse } from "next/server";

import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export const dynamic = "force-dynamic";

type RouteContext = {
    params: Promise<{ id: string }> | { id: string };
};

export async function GET(req: Request, context: RouteContext) {
    try {
        const params = await context.params;
        const { searchParams } = new URL(req.url);
        const query = new URLSearchParams();
        const limit = searchParams.get("limit");
        if (limit) query.set("limit", limit);
        const response = await fetch(`${ENGINE_URL}/v1/memory/workflows/${encodeURIComponent(params.id)}/episodes?${query.toString()}`, {
            cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
