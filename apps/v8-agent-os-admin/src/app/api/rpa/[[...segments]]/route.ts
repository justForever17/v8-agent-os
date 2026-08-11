import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { verifyServiceAuth } from "@/lib/service-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { projectPublicRpaAvailability } from "@/lib/server/rpa-public-surface.mjs";

const ENGINE_URL = resolveEngineBaseUrl();

async function resolveUserEmail(req: NextRequest) {
    let userEmail: string | null | undefined = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }
    return userEmail;
}

function buildTarget(req: NextRequest, segments?: string[]) {
    const suffix = (segments || []).map((item) => encodeURIComponent(item)).join("/");
    const search = req.nextUrl.searchParams.toString();
    return `${ENGINE_URL}/rpa${suffix ? `/${suffix}` : ""}${search ? `?${search}` : ""}`;
}

async function proxy(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }, method: "GET" | "POST" | "DELETE") {
    const userEmail = await resolveUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const { segments } = await context.params;
        const target = buildTarget(req, segments);
        const init: RequestInit = {
            method,
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        };
        if (method === "POST") {
            const payload = await req.json().catch(() => ({}));
            init.body = JSON.stringify(payload);
        }
        const isAvailability = method === "GET" && segments?.length === 1 && segments[0] === "availability";
        if (isAvailability) init.signal = AbortSignal.timeout(7_000);
        const res = await fetch(target, init);
        const data = await res.json().catch(() => ({}));
        const publicData = isAvailability
            ? projectPublicRpaAvailability(data)
            : data;
        return NextResponse.json(publicData, { status: res.status });
    } catch (error) {
        if (error instanceof Error && (error.name === "AbortError" || error.name === "TimeoutError")) {
            console.warn("[Admin RPA Proxy] availability timed out");
            return NextResponse.json({ error: "rpa_availability_timeout" }, { status: 504 });
        }
        console.error("[Admin RPA Proxy] failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function GET(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, context, "GET");
}

export async function POST(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, context, "POST");
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, context, "DELETE");
}
