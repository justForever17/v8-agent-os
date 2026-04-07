import { NextRequest, NextResponse } from "next/server";

import { fetchClientAdmin } from "@/lib/server/client-proxy";
import { fetchSignedClientAdminPath, verifySignedClientSurfaceRequest } from "@/lib/server/client-surface-resource";

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    try {
        const { id } = await context.params;
        const response = verifySignedClientSurfaceRequest(req)
            ? await fetchSignedClientAdminPath(`/memory/artifacts/${encodeURIComponent(id)}/content`, { method: "GET" })
            : await fetchClientAdmin(req, `/memory/artifacts/${encodeURIComponent(id)}/content`, {
                method: "GET",
            });
        if (!response.ok || !response.body) {
            const payload = await response.json().catch(() => ({}));
            return NextResponse.json(payload, { status: response.status });
        }
        return new NextResponse(response.body, {
            status: response.status,
            headers: {
                "Content-Type": response.headers.get("Content-Type") || "application/octet-stream",
                "Content-Disposition": response.headers.get("Content-Disposition") || "",
                "Cache-Control": "no-store",
            },
        });
    } catch (error) {
        if (error instanceof Error && error.message === "Unauthorized") {
            return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
        }
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Artifact content unavailable" },
            { status: 502 },
        );
    }
}
