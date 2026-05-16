import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

function buildTarget(baseUrl: string, req: NextRequest, segments?: string[]) {
    const suffix = (segments || []).map((segment) => encodeURIComponent(segment)).join("/");
    return `${baseUrl}/mcp/apps${suffix ? `/${suffix}` : ""}${req.nextUrl.search || ""}`;
}

async function proxy(req: NextRequest, segments: string[] | undefined, method: "GET" | "POST" | "DELETE") {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    try {
        const engineBaseUrl = await resolveEngineBaseUrl();
        const body = method === "POST" ? await req.text() : undefined;
        const response = await fetch(buildTarget(engineBaseUrl, req, segments), {
            method,
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-user-email": session.user.email,
            },
            body,
            cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch {
        return NextResponse.json({ error: "Engine Service Unavailable" }, { status: 502 });
    }
}

export async function GET(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    const params = await context.params;
    return proxy(req, params.segments, "GET");
}

export async function POST(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    const params = await context.params;
    return proxy(req, params.segments, "POST");
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    const params = await context.params;
    return proxy(req, params.segments, "DELETE");
}
