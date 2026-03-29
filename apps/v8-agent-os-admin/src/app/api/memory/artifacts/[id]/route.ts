import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function GET(_req: Request, context: { params: Promise<{ id: string }> }) {
    try {
        const { id } = await context.params;
        const response = await fetch(`${ENGINE_URL}/v1/artifacts/${encodeURIComponent(id)}`, {
            cache: "no-store",
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[ArtifactsAPI] DETAIL failed:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
