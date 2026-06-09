import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();
const ENGINE_V1_URL = ENGINE_URL.endsWith("/v1") ? ENGINE_URL : `${ENGINE_URL}/v1`;

export async function GET(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    const query = req.nextUrl.searchParams.toString();
    const suffix = query ? `?${query}` : "";
    try {
        const response = await fetch(`${ENGINE_V1_URL}/model-cache/stats${suffix}`, { cache: "no-store" });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Failed to fetch prompt cache stats";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}
