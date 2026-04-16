import { NextRequest, NextResponse } from "next/server";

import { resolveEngineOrigin } from "@/lib/server/runtime-config";

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
        const headers = new Headers();
        const range = req.headers.get("range");
        if (range) {
            headers.set("Range", range);
        }
        const response = await fetch(`${resolveEngineOrigin()}/v1/artifacts/${encodeURIComponent(id)}/content`, {
            cache: "no-store",
            headers,
        });
        if (!response.ok || !response.body) {
            const text = await response.text().catch(() => "");
            return NextResponse.json({ error: text || "Artifact content unavailable" }, { status: response.status || 502 });
        }
        return new NextResponse(response.body, {
            status: response.status,
            headers: passthroughContentHeaders(response),
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Artifact content unavailable" },
            { status: 500 },
        );
    }
}
