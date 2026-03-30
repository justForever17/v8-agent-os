import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

type RouteContext = {
    params: Promise<{ path?: string[] }>;
};

function buildTarget(req: NextRequest, segments?: string[]) {
    const suffix = (segments || []).map((item) => encodeURIComponent(item)).join("/");
    const search = req.nextUrl.searchParams.toString();
    return `${resolveEngineBaseUrl()}/network-supervisor${suffix ? `/${suffix}` : ""}${search ? `?${search}` : ""}`;
}

async function proxy(req: NextRequest, context: RouteContext, method: "GET" | "POST" | "PATCH" | "DELETE") {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    try {
        const { path } = await context.params;
        const target = buildTarget(req, path);
        const init: RequestInit = {
            method,
            cache: "no-store",
            headers: {},
        };
        if (method !== "GET") {
            const payload = await req.json().catch(() => ({}));
            init.headers = { "Content-Type": "application/json" };
            init.body = JSON.stringify(payload);
        }
        const response = await fetch(target, init);
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Admin Network Supervisor Proxy] failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function GET(req: NextRequest, context: RouteContext) {
    return proxy(req, context, "GET");
}

export async function POST(req: NextRequest, context: RouteContext) {
    return proxy(req, context, "POST");
}

export async function PATCH(req: NextRequest, context: RouteContext) {
    return proxy(req, context, "PATCH");
}

export async function DELETE(req: NextRequest, context: RouteContext) {
    return proxy(req, context, "DELETE");
}
