import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { buildStoredCredential, mapEngineProvider } from "@/lib/models/provider-admin";
import { canonicalizeOauthCredentialReference } from "@/lib/models/oauth-store";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET() {
    const session = await auth();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        const res = await fetch(`${ENGINE_URL}/models`);
        if (!res.ok) throw new Error(`Python API returned ${res.status}`);
        const routesData = await res.json();
        const providersDict = routesData.providers || {};
        const providersList = Object.keys(providersDict).map((providerKey) =>
            mapEngineProvider(providerKey, providersDict[providerKey])
        );

        return NextResponse.json(providersList);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
        console.error("Failed to fetch providers via Python API:", error);
        return NextResponse.json({ error: error.message }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const data = await req.json() as any;
        
        // 1. Fetch existing routes payload
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        let routesData: any = { providers: {} };
        try {
            const resGet = await fetch(`${ENGINE_URL}/models`);
            if (resGet.ok) {
                routesData = await resGet.json();
            }
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        } catch(e) { /* ignore and use default */ }
        
        const providerCode = String(data.code || data.name || "")
            .toLowerCase()
            .replace(/[^a-z0-9]/g, "-");
        
        // Ensure providers object exists
        if (!routesData.providers) routesData.providers = {};
        
        // Preserve existing models if an update is occurring
        const existingModels = routesData.providers[providerCode]?.models || {};
        const existingProvider = routesData.providers[providerCode]?.provider || {};

        // 2. Mutate provider data — preserve existing api_key if new value is masked or empty
        const existingCredential = String(existingProvider.api_key || "");
        let storedCredential = buildStoredCredential({
            providerType: data.type,
            credentialMode: data.credentialMode,
            apiKey: data.apiKey,
            oauthPath: data.oauthPath,
            existingRawCredential: existingCredential,
        });
        let oauthRef = String(existingProvider.oauth_ref || "").trim();
        if (storedCredential.startsWith("oauth:")) {
            const canonicalized = (
                await canonicalizeOauthCredentialReference({
                    providerId: providerCode,
                    rawReference: storedCredential,
                    platformLoginPreset: data.platformLoginPreset,
                })
            );
            storedCredential = canonicalized.storedCredential;
            oauthRef = canonicalized.oauthRef;
        }

        routesData.providers[providerCode] = {
            provider: {
                ...existingProvider,
                name: data.name || existingProvider.name || providerCode,
                description: data.description ?? existingProvider.description ?? "",
                icon: data.icon ?? existingProvider.icon ?? "",
                base_url: data.baseUrl ?? existingProvider.base_url ?? "",
                api_key: storedCredential,
                api_standard: data.apiStandard || existingProvider.api_standard || "openai",
                type: data.type || existingProvider.type || "API",
                is_enabled: data.isEnabled !== undefined ? data.isEnabled : (existingProvider.is_enabled !== false),
                credential_mode: data.credentialMode || existingProvider.credential_mode || "apiKey",
                oauth_preset: data.platformLoginPreset || existingProvider.oauth_preset || "",
                oauth_ref: oauthRef,
                local_backend_preset: data.localBackendPreset || existingProvider.local_backend_preset || "",
            },
            models: existingModels
        };

        // 3. Save it back
        const resPost = await fetch(`${ENGINE_URL}/models`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(routesData),
        });

        if (!resPost.ok) throw new Error(`Python API returned ${resPost.status}`);

        const savedProvider = routesData.providers[providerCode];
        return NextResponse.json(mapEngineProvider(providerCode, savedProvider));
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
         console.error("Failed to save provider via Python API:", error);
        return NextResponse.json({ error: error.message }, { status: 500 });
    }
}
