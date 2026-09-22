import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@admin/lib/auth";
import { resolveEngineBaseUrl } from "@admin/lib/server/runtime-config";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email || session.user.adminAuthenticated !== true || session.user.role !== "ADMIN") {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const days = req.nextUrl.searchParams.get("days") || "7";
        const response = await engineFetch(`${resolveEngineBaseUrl()}/telemetry/overview?days=${encodeURIComponent(days)}`, {
            cache: "no-store",
            signal: AbortSignal.timeout(8_000),
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("Failed to fetch telemetry overview:", error);
        const timedOut = error instanceof Error && error.name === "TimeoutError";
        return NextResponse.json(
            { error: timedOut ? "telemetry_timeout" : "telemetry_unavailable" },
            { status: timedOut ? 504 : 502 },
        );
    }
}
