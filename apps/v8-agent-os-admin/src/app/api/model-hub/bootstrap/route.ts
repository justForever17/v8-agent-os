import { NextRequest, NextResponse } from "next/server";

import { listEngineModels } from "@/lib/models/model-admin";
import { mapEngineProvider } from "@/lib/models/provider-admin";
import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

type EngineModelsPayload = {
    providers?: Record<string, unknown>;
    roles?: Record<string, unknown>;
};

type EngineProviderContainer = Parameters<typeof mapEngineProvider>[1];
type EngineProvidersMap = Parameters<typeof listEngineModels>[0];

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) {
        return unauthorized;
    }

    const engineBaseUrl = resolveEngineBaseUrl();

    try {
        const [hubResult, catalogResponse, audioResponse] = await Promise.all([
            proxyEngineJson("/config-registry/models"),
            fetch(`${engineBaseUrl}/models/catalog`, { next: { revalidate: 60 } }),
            fetch(`${engineBaseUrl}/audio/config`, { cache: "no-store" }),
        ]);

        const hubEnvelope = hubResult.data as { data?: { config?: EngineModelsPayload } };
        const routesData: EngineModelsPayload = hubEnvelope.data?.config || {};
        const providersDict = routesData.providers || {};
        const providers = Object.keys(providersDict).map((providerKey) =>
            mapEngineProvider(providerKey, providersDict[providerKey] as EngineProviderContainer)
        );
        const models = listEngineModels(providersDict as EngineProvidersMap);
        const catalog = await catalogResponse.json().catch(() => ({}));
        const audioConfig = await audioResponse.json().catch(() => ({}));
        const modelRef = String(routesData.roles?.default || "").trim() || null;

        return NextResponse.json({
            providers,
            models,
            hubEnvelope: hubResult.data,
            defaultModel: { modelId: modelRef, modelRef, value: modelRef, source: "models.json.roles.default" },
            catalog,
            audioConfig,
        });
    } catch (error) {
        console.error("[Model Hub Bootstrap] Failed to load:", error);
        return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
    }
}
