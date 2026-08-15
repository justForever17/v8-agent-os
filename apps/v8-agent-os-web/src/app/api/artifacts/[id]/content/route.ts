import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";

import { resolveAdminApiBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

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
        if (value) headers.set(name, value);
    }
    headers.set("Cache-Control", "no-store");
    return headers;
}

export async function GET(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const internalSecret = await resolveInternalSecret();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const { id } = await context.params;
        const sessionId = String(req.nextUrl.searchParams.get("sessionId") || "").trim();
        if (!sessionId) {
            return NextResponse.json({ error: "sessionId is required" }, { status: 400 });
        }
        const query = new URLSearchParams({ sessionId });
        if (req.nextUrl.searchParams.get("download") === "1") query.set("download", "1");
        const headers = new Headers({
            "x-v8-agent-os-secret": internalSecret,
            "x-v8-agent-os-user-email": session.user.email,
        });
        const range = req.headers.get("range");
        if (range) headers.set("Range", range);
        const response = await fetch(`${await resolveAdminApiBaseUrl()}/memory/artifacts/${encodeURIComponent(id)}/content?${query.toString()}`, {
            headers: {
                ...Object.fromEntries(headers.entries()),
            },
            cache: "no-store",
        });
        if (!response.ok || !response.body) {
            const data = await response.json().catch(() => ({}));
            return NextResponse.json(data, { status: response.status });
        }
        return new NextResponse(response.body, {
            status: response.status,
            headers: passthroughContentHeaders(response),
        });
    } catch (error) {
        console.error("[ArtifactsProxy] CONTENT failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}
