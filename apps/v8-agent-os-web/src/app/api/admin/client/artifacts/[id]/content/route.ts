import { NextRequest, NextResponse } from "next/server";

import { fetchClientAdmin } from "@admin/lib/server/client-proxy";
import { fetchEngineClientIdentity } from "@admin/lib/server/engine-identity";

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
        const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
        if (!sessionId) {
            return NextResponse.json({ error: "sessionId is required" }, { status: 400 });
        }
        const range = req.headers.get("range");
        const headers = range ? { Range: range } : undefined;
        const query = new URLSearchParams({ sessionId });
        if (req.nextUrl.searchParams.get("download") === "1") {
            query.set("download", "1");
        }
        const target = `/artifacts/${encodeURIComponent(id)}/content?${query.toString()}${req.nextUrl.searchParams.get("v8sig") ? `&v8exp=${encodeURIComponent(req.nextUrl.searchParams.get("v8exp") || "")}&v8sig=${encodeURIComponent(req.nextUrl.searchParams.get("v8sig") || "")}` : ""}`;
        const response = req.nextUrl.searchParams.has("v8sig")
            ? await fetchEngineClientIdentity(target, { method: "GET", headers })
            : await fetchClientAdmin(req, target, {
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
