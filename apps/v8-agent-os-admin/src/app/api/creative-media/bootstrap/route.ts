import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

type AdminFetchInit = RequestInit & { next?: { revalidate?: number } };

async function fetchEngineJson(engineBaseUrl: string, path: string, init?: AdminFetchInit) {
    const response = await fetch(`${engineBaseUrl}/creative-media/${path}`, init);
    return response.json().catch(() => ({}));
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    const engineBaseUrl = resolveEngineBaseUrl();

    try {
        const [catalog, resolutions, workOrders, recipes, assets, jobs, modelPreferences] = await Promise.all([
            fetchEngineJson(engineBaseUrl, "catalog", { next: { revalidate: 60 } }),
            fetchEngineJson(engineBaseUrl, "resolutions", { next: { revalidate: 60 } }),
            fetchEngineJson(engineBaseUrl, "work-orders", { cache: "no-store" }),
            fetchEngineJson(engineBaseUrl, "recipes", { cache: "no-store" }),
            fetchEngineJson(engineBaseUrl, "assets", { cache: "no-store" }),
            fetchEngineJson(engineBaseUrl, "jobs", { cache: "no-store" }),
            fetchEngineJson(engineBaseUrl, "model-preferences", { cache: "no-store" }),
        ]);

        return NextResponse.json({
            catalog,
            resolutions,
            workOrders,
            recipes,
            assets,
            jobs,
            modelPreferences,
        });
    } catch (error) {
        console.error("[Creative Media Bootstrap] Failed to load:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
