import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const days = req.nextUrl.searchParams.get("days") || "7";
        const response = await fetch(`${ENGINE_URL}/telemetry/overview?days=${encodeURIComponent(days)}`, {
            cache: "no-store",
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("Failed to fetch telemetry overview:", error);
        return NextResponse.json({ error: "Failed to fetch telemetry overview" }, { status: 500 });
    }
}
