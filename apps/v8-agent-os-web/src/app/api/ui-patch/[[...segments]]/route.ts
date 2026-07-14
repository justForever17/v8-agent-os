import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";


function enginePath(segments: string[]) {
    const decoded = segments.map((segment) => decodeURIComponent(segment));
    if (decoded.length < 3 || decoded[0] !== "sessions" || !/^[A-Za-z0-9_-]+$/.test(decoded[1])) {
        return "";
    }
    const tail = decoded.slice(2);
    const previewRoute = tail[0] === "previews"
        && (
            tail.length === 1
            || (tail.length === 2 && /^[A-Za-z0-9_-]+$/.test(tail[1]))
            || (tail.length === 3 && /^[A-Za-z0-9_-]+$/.test(tail[1]) && new Set(["selections", "commits"]).has(tail[2]))
        );
    const transactionRoute = tail[0] === "transactions"
        && tail.length === 3
        && /^[A-Za-z0-9_-]+$/.test(tail[1])
        && new Set(["verification", "undo"]).has(tail[2]);
    if (!previewRoute && !transactionRoute) return "";
    return `/sessions/${encodeURIComponent(decoded[1])}/ui-patch/${tail.map(encodeURIComponent).join("/")}`;
}

async function proxy(
    request: NextRequest,
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
        return NextResponse.json({ error: "Unsupported UI Patch route" }, { status: 404 });
    }
    try {
        const response = await fetch(`${await resolveEngineBaseUrl()}${path}${request.nextUrl.search}`, {
            method,
            headers: {
                "Content-Type": request.headers.get("Content-Type") || "application/json",
                "x-v8-agent-os-user-email": session.user.email,
            },
            body: method === "POST" ? await request.text() : undefined,
            cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        console.error("[UiPatchProxy] request failed", error);
        return NextResponse.json({ error: "Engine Service Unavailable" }, { status: 502 });
    }
}

export function GET(request: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(request, context, "GET");
}

export function POST(request: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(request, context, "POST");
}

export function DELETE(request: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(request, context, "DELETE");
}
