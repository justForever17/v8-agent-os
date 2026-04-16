import { NextRequest, NextResponse } from "next/server";
import { normalizeArtifactsForAdminSurface } from "@/lib/server/artifact-surface";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function GET(req: NextRequest) {
    try {
        const { searchParams } = new URL(req.url);
        const limit = searchParams.get("limit") || "120";
        const sessionId = searchParams.get("sessionId");
        const runId = searchParams.get("runId");
        const query = new URLSearchParams({ limit });
        if (sessionId) query.set("session_id", sessionId);
        if (runId) query.set("run_id", runId);

        const response = await fetch(`${ENGINE_URL}/v1/artifacts?${query.toString()}`, {
            cache: "no-store",
        });
        const data = await response.json().catch(() => ({}));
        if (Array.isArray(data?.artifacts)) {
            data.artifacts = normalizeArtifactsForAdminSurface(data.artifacts, req);
        }
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[ArtifactsAPI] GET failed:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
