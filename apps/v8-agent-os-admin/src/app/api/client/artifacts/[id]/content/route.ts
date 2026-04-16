import { NextRequest, NextResponse } from "next/server";

import { fetchClientAdmin } from "@/lib/server/client-proxy";
import { fetchSignedClientAdminPath, verifySignedClientSurfaceRequest } from "@/lib/server/client-surface-resource";

function passthroughContentHeaders(response: Response) {
    const headers = new Headers();
    for (const name of [
        "Content-Type",
        "Content-Disposition",
        "Content-Length",
        "Accept-Ranges",
        "Content-Range",
        "ETag",
        "Last-Modified",
    ]) {
        const value = response.headers.get(name);
        if (value) {
            headers.set(name, value);
        }
    }
    headers.set("Cache-Control", "no-store");
    return headers;
}

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    try {
        const { id } = await context.params;
        const range = req.headers.get("range");
        const headers = range ? { Range: range } : undefined;
        const response = verifySignedClientSurfaceRequest(req)
            ? await fetchSignedClientAdminPath(`/memory/artifacts/${encodeURIComponent(id)}/content`, { method: "GET", headers })
            : await fetchClientAdmin(req, `/memory/artifacts/${encodeURIComponent(id)}/content`, {
                method: "GET",
                headers,
            });
        if (!response.ok || !response.body) {
            const payload = await response.json().catch(() => ({}));
            return NextResponse.json(payload, { status: response.status });
        }
        return new NextResponse(response.body, {
            status: response.status,
            headers: passthroughContentHeaders(response),
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
