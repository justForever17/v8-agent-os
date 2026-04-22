import { NextResponse } from "next/server";

import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export const dynamic = "force-dynamic";

type RouteContext = {
    params: Promise<{ id: string }> | { id: string };
};

export async function POST(req: Request, context: RouteContext) {
    try {
        const params = await context.params;
        const body = await req.json().catch(() => ({}));
        const response = await fetch(`${ENGINE_URL}/v1/memory/workflows/${encodeURIComponent(params.id)}/merge`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
