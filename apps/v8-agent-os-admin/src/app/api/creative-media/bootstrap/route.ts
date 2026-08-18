import { NextRequest, NextResponse } from "next/server";

import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import {
    resolveCreativeMediaGovernanceSecret,
    resolveEngineBaseUrl,
    resolveInternalSecret,
} from "@/lib/server/runtime-config";

type AdminFetchInit = RequestInit & { next?: { revalidate?: number } };
type AdminFetchOptions = { requireOk?: boolean };

async function fetchEngineJson(
    engineBaseUrl: string,
    internalSecret: string,
    path: string,
    init?: AdminFetchInit,
    options: AdminFetchOptions = {},
) {
    const headers = new Headers(init?.headers);
    headers.set("x-v8-agent-os-secret", internalSecret);
    const response = await fetch(`${engineBaseUrl}/creative-media/${path}`, {
        ...init,
        headers,
    });
    if (options.requireOk && !response.ok) {
        throw new Error(`Creative Media ${path} request failed (${response.status})`);
    }
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
        const governanceSecret = resolveCreativeMediaGovernanceSecret();
        const [catalog, resolutions, governance, modelPreferences, reconcilerStatus] = await Promise.all([
            fetchEngineJson(engineBaseUrl, internalSecret, "catalog", { next: { revalidate: 60 } }),
            fetchEngineJson(engineBaseUrl, internalSecret, "resolutions", { next: { revalidate: 60 } }),
            fetchEngineJson(engineBaseUrl, internalSecret, "governance/snapshot", {
                cache: "no-store",
                headers: { "x-v8-agent-os-admin-governance-secret": governanceSecret },
            }),
            fetchEngineJson(engineBaseUrl, internalSecret, "model-preferences", { cache: "no-store" }),
            fetchEngineJson(engineBaseUrl, internalSecret, "reconciler/status", { cache: "no-store" }, { requireOk: true }).catch(() => ({
                schema: "v8.creative_media_reconciler_status.v1",
                unavailable: true,
                detailCode: "reconciler_status_unavailable",
                hasError: true,
                worker: { state: "unavailable", running: false, lastCycle: {} },
                uncertain: 0,
                projectionPending: 0,
                oldest: { at: null, ageSeconds: null },
                adapterDistribution: {},
                detailCodeDistribution: {},
                quarantineCount: 0,
            })),
        ]);

        return NextResponse.json({
            catalog,
            resolutions,
            ...governance,
            modelPreferences,
            reconcilerStatus,
        });
    } catch (error) {
        console.error("[Creative Media Bootstrap] Failed to load:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
