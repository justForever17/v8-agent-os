import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";


function buildTarget(baseUrl: string, segments?: string[]) {
    const suffix = (segments || []).map((segment) => encodeURIComponent(segment)).join("/");
    return `${baseUrl}/ui/actions${suffix ? `/${suffix}` : ""}`;
}

async function proxy(req: NextRequest, segments: string[] | undefined, method: "GET" | "POST") {
    const session = await auth();
    if (!session?.user?.email) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    try {
        const body = method === "POST" ? await req.text() : undefined;
        const response = await fetch(buildTarget(await resolveEngineBaseUrl(), segments), {
            method,
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-user-email": session.user.email,
                "x-v8-session-id": req.headers.get("x-v8-session-id") || "",
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
    return proxy(req, (await context.params).segments, "GET");
}

export async function POST(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, (await context.params).segments, "POST");
}
