import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

function buildTarget(req: NextRequest, segments?: string[]) {
    const suffix = (segments || []).map((item) => encodeURIComponent(item)).join("/");
    const search = req.nextUrl.searchParams.toString();
    return `${ENGINE_URL}/terminal${suffix ? `/${suffix}` : ""}${search ? `?${search}` : ""}`;
}

async function proxy(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }, method: "GET" | "POST") {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    try {
        const { segments } = await context.params;
        const init: RequestInit = {
            method,
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-user-email": userEmail,
            },
            cache: "no-store",
        };
        if (method === "POST") {
            init.body = JSON.stringify(await req.json().catch(() => ({})));
        }
        const response = await fetch(buildTarget(req, segments), init);
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Client Terminal Proxy] failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function GET(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, context, "GET");
}

export async function POST(req: NextRequest, context: { params: Promise<{ segments?: string[] }> }) {
    return proxy(req, context, "POST");
}
