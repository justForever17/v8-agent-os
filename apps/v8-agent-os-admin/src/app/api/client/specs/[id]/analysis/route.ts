import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> },
) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const { id } = await params;
    try {
        const search = req.nextUrl.searchParams.toString();
        const suffix = search ? `?${search}` : "";
        const response = await fetch(`${ENGINE_URL}/specs/${encodeURIComponent(id)}/analysis${suffix}`, {
            method: "GET",
            headers: { "Content-Type": "application/json" },
            cache: "no-store",
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[Client Specs] Analysis failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
