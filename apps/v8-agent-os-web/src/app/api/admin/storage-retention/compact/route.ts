import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@admin/lib/server/runtime-config";

export async function POST(request: Request) {
    try {
        const body = await request.json().catch(() => ({}));
        const response = await engineFetch(`${resolveEngineOrigin()}/v1/storage-retention/compact`, {
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
