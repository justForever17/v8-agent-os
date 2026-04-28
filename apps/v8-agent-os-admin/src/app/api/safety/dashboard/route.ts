import { NextRequest, NextResponse } from "next/server";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const search = req.nextUrl.searchParams.toString();
        const suffix = search ? `?${search}` : "";
        const res = await fetch(`${ENGINE_URL}/safety/dashboard${suffix}`, { cache: "no-store" });
        const data = await res.json().catch(() => ({}));
        return NextResponse.json(data, { status: res.status });
    } catch (error) {
        console.error("[Admin Safety Dashboard] Engine proxy failed:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
