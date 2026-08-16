import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

type AdminFetchInit = RequestInit & { next?: { revalidate?: number } };

async function fetchEngineJson(
    engineBaseUrl: string,
    internalSecret: string,
    path: string,
    init?: AdminFetchInit,
) {
    const headers = new Headers(init?.headers);
    headers.set("x-v8-agent-os-secret", internalSecret);
    const response = await fetch(`${engineBaseUrl}/creative-media/${path}`, {
        ...init,
        headers,
    });
    return response.json().catch(() => ({}));
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    const engineBaseUrl = resolveEngineBaseUrl();

    try {
        const internalSecret = resolveInternalSecret();
        if (!internalSecret) {
            throw new Error("Internal service secret is unavailable");
        }
        const [catalog, resolutions, workOrders, recipes, assets, jobs, modelPreferences] = await Promise.all([
            fetchEngineJson(engineBaseUrl, internalSecret, "catalog", { next: { revalidate: 60 } }),
            fetchEngineJson(engineBaseUrl, internalSecret, "resolutions", { next: { revalidate: 60 } }),
            fetchEngineJson(engineBaseUrl, internalSecret, "work-orders", { cache: "no-store" }),
            fetchEngineJson(engineBaseUrl, internalSecret, "recipes", { cache: "no-store" }),
            fetchEngineJson(engineBaseUrl, internalSecret, "assets", { cache: "no-store" }),
            fetchEngineJson(engineBaseUrl, internalSecret, "jobs", { cache: "no-store" }),
            fetchEngineJson(engineBaseUrl, internalSecret, "model-preferences", { cache: "no-store" }),
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
