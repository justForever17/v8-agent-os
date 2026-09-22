import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextRequest, NextResponse } from "next/server";
import { normalizeArtifactForAdminSurface } from "@admin/lib/server/artifact-surface";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@admin/lib/server/request-auth";
import { resolveEngineOrigin } from "@admin/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const { id } = await context.params;
        const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
        if (!sessionId) {
            return NextResponse.json({ error: "sessionId is required" }, { status: 400 });
        }
        const query = new URLSearchParams({ sessionId });
        const response = await engineFetch(`${ENGINE_URL}/v1/artifacts/${encodeURIComponent(id)}?${query.toString()}`, {
            cache: "no-store",
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(await normalizeArtifactForAdminSurface(data, req), { status: response.status });
    } catch (error) {
        console.error("[ArtifactsAPI] DETAIL failed:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
