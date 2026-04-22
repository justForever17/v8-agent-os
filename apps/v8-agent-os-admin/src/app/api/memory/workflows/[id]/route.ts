import { NextResponse } from "next/server";

import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export const dynamic = "force-dynamic";

type RouteContext = {
    params: Promise<{ id: string }> | { id: string };
};

async function getId(context: RouteContext) {
    const params = await context.params;
    return encodeURIComponent(params.id);
}

export async function GET(_req: Request, context: RouteContext) {
    try {
        const id = await getId(context);
        const response = await fetch(`${ENGINE_URL}/v1/memory/workflows/${id}`, { cache: "no-store" });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}

export async function PATCH(req: Request, context: RouteContext) {
    try {
        const id = await getId(context);
        const body = await req.json().catch(() => ({}));
        const response = await fetch(`${ENGINE_URL}/v1/memory/workflows/${id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}

export async function DELETE(_req: Request, context: RouteContext) {
    try {
        const id = await getId(context);
        const response = await fetch(`${ENGINE_URL}/v1/memory/workflows/${id}`, { method: "DELETE" });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
