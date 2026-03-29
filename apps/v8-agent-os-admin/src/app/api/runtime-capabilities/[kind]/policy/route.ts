import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { verifyServiceAuth } from "@/lib/service-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

async function resolveUserEmail(req: NextRequest) {
    let userEmail: string | null | undefined = await verifyServiceAuth(req);
    if (!userEmail) {
        const session = await auth();
        userEmail = session?.user?.email;
    }
    return userEmail;
}

async function proxy(req: NextRequest, context: { params: Promise<{ kind: string }> }, method: "POST" | "DELETE") {
    const userEmail = await resolveUserEmail(req);
    if (!userEmail) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const { kind } = await context.params;
        const init: RequestInit = {
            method,
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        };
        if (method === "POST") {
            const payload = await req.json().catch(() => ({}));
            init.body = JSON.stringify(payload);
        }
        const res = await fetch(`${ENGINE_URL}/runtime-capabilities/${encodeURIComponent(kind)}/policy`, init);
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error("[Admin Runtime Policy] Engine proxy failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}

export async function POST(req: NextRequest, context: { params: Promise<{ kind: string }> }) {
    return proxy(req, context, "POST");
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ kind: string }> }) {
    return proxy(req, context, "DELETE");
}
