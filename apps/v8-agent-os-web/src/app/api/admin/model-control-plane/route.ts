import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@admin/lib/auth";
import { resolveEngineBaseUrl } from "@admin/lib/server/runtime-config";

const ENGINE_URL = `${resolveEngineBaseUrl()}/models/control-plane`;

export async function GET() {
    const session = await auth();
    if (!session?.user?.email || session.user.adminAuthenticated !== true || session.user.role !== "ADMIN") {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const response = await engineFetch(ENGINE_URL, { cache: "no-store" });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown proxy error";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email || session.user.adminAuthenticated !== true || session.user.role !== "ADMIN") {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const payload = await req.json();
        const response = await engineFetch(ENGINE_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown proxy error";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}
