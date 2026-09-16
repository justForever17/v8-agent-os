import { engineFetch } from "@/lib/server/engine-fetch";
import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

export const dynamic = "force-dynamic";

export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
    try {
        const { id } = await context.params;
        const body = await request.json();
        const response = await engineFetch(
            `${resolveEngineOrigin()}/v1/memory/knowledge-resolution-candidates/${encodeURIComponent(id)}/resolve`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            },
        );
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
