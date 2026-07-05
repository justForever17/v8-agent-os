import { NextRequest, NextResponse } from "next/server";

import { listEngineModels } from "@/lib/models/model-admin";
import { mapEngineProvider } from "@/lib/models/provider-admin";
import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

type EngineModelsPayload = {
    providers?: Record<string, unknown>;
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
        const [modelsResponse, hubResult, supervisorResult, catalogResponse, audioResponse] = await Promise.all([
            fetch(`${engineBaseUrl}/models`, { cache: "no-store" }),
            proxyEngineJson("/config-registry/models"),
            proxyEngineJson("/config-registry/supervisor"),
            fetch(`${engineBaseUrl}/models/catalog`, { next: { revalidate: 60 } }),
            fetch(`${engineBaseUrl}/audio/config`, { cache: "no-store" }),
        ]);

        const routesData = (await modelsResponse.json().catch(() => ({}))) as EngineModelsPayload;
        const providersDict = routesData.providers || {};
        const providers = Object.keys(providersDict).map((providerKey) =>
            mapEngineProvider(providerKey, providersDict[providerKey] as EngineProviderContainer)
        );
        const models = listEngineModels(providersDict as EngineProvidersMap);
        const catalog = await catalogResponse.json().catch(() => ({}));
        const audioConfig = await audioResponse.json().catch(() => ({}));
        const modelRef = (
            supervisorResult.data as { data?: { bindings?: { defaultReplyModel?: string } } }
        ).data?.bindings?.defaultReplyModel || null;

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
