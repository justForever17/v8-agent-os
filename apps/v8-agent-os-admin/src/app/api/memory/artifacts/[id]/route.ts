import { NextRequest, NextResponse } from "next/server";
import { normalizeArtifactForAdminSurface } from "@/lib/server/artifact-surface";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    try {
        const { id } = await context.params;
        const response = await fetch(`${ENGINE_URL}/v1/artifacts/${encodeURIComponent(id)}`, {
            cache: "no-store",
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(normalizeArtifactForAdminSurface(data, req), { status: response.status });
    } catch (error) {
        console.error("[ArtifactsAPI] DETAIL failed:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
