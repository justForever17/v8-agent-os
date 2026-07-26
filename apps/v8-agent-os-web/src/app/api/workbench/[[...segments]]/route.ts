import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";


const ALLOWED_PATHS = [
    /^sessions\/[A-Za-z0-9_-]+\/files(?:\/(?:resolve|read))?$/,
];

function enginePath(segments: string[]) {
    const decoded = segments.map((segment) => decodeURIComponent(segment));
    const joined = decoded.join("/");
    if (!ALLOWED_PATHS.some((pattern) => pattern.test(joined))) {
        return "";
    }
    if (joined.startsWith("sessions/")) {
        const [sessions, sessionId, resource, ...rest] = decoded;
        return `/${sessions}/${encodeURIComponent(sessionId)}/workbench/${resource}${rest.length ? `/${rest.map(encodeURIComponent).join("/")}` : ""}`;
    }
    return "";
}

async function proxy(
    req: NextRequest,
    context: { params: Promise<{ segments?: string[] }> },
    method: "GET" | "POST" | "DELETE",
) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { segments = [] } = await context.params;
    const path = enginePath(segments);
    if (!path) {
        return NextResponse.json({ error: "Unsupported Workbench route" }, { status: 404 });
    }
    try {
        const response = await fetch(`${await resolveEngineBaseUrl()}${path}${req.nextUrl.search}`, {
            method,
            headers: {
                "Content-Type": req.headers.get("Content-Type") || "application/json",
                "x-v8-agent-os-user-email": session.user.email,
            },
            body: method === "POST" ? await req.text() : undefined,
            cache: "no-store",
        });
        const contentType = response.headers.get("Content-Type") || "application/octet-stream";
        if (contentType.includes("application/json")) {
            const payload = await response.json().catch(() => ({}));
            return NextResponse.json(payload, { status: response.status });
        }
        if (!response.body) {
            return new NextResponse(null, { status: response.status });
        }
        const headers = new Headers({
            "Content-Type": contentType,
            "Cache-Control": "no-store",
        });
        for (const key of ["Content-Disposition", "ETag", "X-V8-Workspace-Path"]) {
            const value = response.headers.get(key);
            if (value) headers.set(key, value);
        }
        return new NextResponse(response.body, { status: response.status, headers });
    } catch (error) {
        console.error("[WorkbenchProxy] request failed", error);
        return NextResponse.json({ error: "Engine Service Unavailable" }, { status: 502 });
    }
}

export function GET(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, context, "GET");
}

export function POST(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, context, "POST");
}

export function DELETE(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, context, "DELETE");
}
