import { NextResponse } from "next/server";

import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
    try {
        const { searchParams } = new URL(req.url);
        const params = new URLSearchParams();
        for (const key of ["status", "q", "limit"]) {
            const value = searchParams.get(key);
            if (value) params.set(key, value);
        }
        const response = await fetch(`${ENGINE_URL}/v1/memory/workflows?${params.toString()}`, {
            cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
