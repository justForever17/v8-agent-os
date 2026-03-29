import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    const searchParams = req.nextUrl.searchParams;
    const providerIdFilter = searchParams.get("providerId");

    try {
        const res = await fetch(`${ENGINE_URL}/models`);
        if (!res.ok) throw new Error(`Python API returned ${res.status}`);
        
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const routesData: any = await res.json();
        const providersDict = routesData.providers || {};
        
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const modelsList: any[] = [];

        Object.keys(providersDict).forEach(providerKey => {
            // Apply filter if specified
            if (providerIdFilter && providerKey !== providerIdFilter) return;

            const providerData = providersDict[providerKey];
            const modelsDict = providerData.models || {};
            
            Object.keys(modelsDict).forEach(modelKey => {
                const modelMeta = modelsDict[modelKey];
                modelsList.push({
                    id: modelKey,
                    providerId: providerKey,
                    modelId: modelKey,
                    name: modelMeta.name || modelKey,
                    type: modelMeta.type || "LLM",
                    contextWindow: modelMeta.contextWindow || null,
                    maxTokens: modelMeta.maxTokens || null,
                    temperature: modelMeta.temperature || 0.7,
                    isEnabled: true,
                    provider: {
                        name: providerData.provider?.name || providerKey,
                        icon: providerData.provider?.icon
                    }
                });
            });
        });

        return NextResponse.json(modelsList);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } catch (error: any) {
        console.error("Failed to fetch models via Python API:", error);
        return NextResponse.json({ error: error.message }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const data = await req.json() as any;
        
        // 1. Fetch existing routes
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        let routesData: any = { providers: {} };
        try {
            const resGet = await fetch(`${ENGINE_URL}/models`);
            if (resGet.ok) {
                routesData = await resGet.json();
            }
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        } catch(e) { /* ignore */ }
        
        if (!routesData.providers) routesData.providers = {};
        
        const providerCode = data.providerId;
        if (!providerCode) throw new Error("providerId is required");
        
        // Ensure provider entity exists in the dict
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const providerData: any = routesData.providers[providerCode] || { provider: {}, models: {} };
        if (!providerData.models) providerData.models = {};
        
        const modelCode = data.modelId;
        if (!modelCode) throw new Error("modelId is required");

        // 2. Merge model configurations
        providerData.models[modelCode] = {
            name: data.name,
            type: data.type,
            contextWindow: data.contextWindow ? parseInt(data.contextWindow) : undefined,
            maxTokens: data.maxTokens ? parseInt(data.maxTokens) : undefined,
            temperature: data.temperature ? parseFloat(data.temperature) : undefined,
        };
        
        // Remap to structure
        routesData.providers[providerCode] = providerData;

        // 3. Save
        const resPost = await fetch(`${ENGINE_URL}/models`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(routesData),
        });

        if (!resPost.ok) throw new Error(`Python API returned ${resPost.status}`);

        // Return a mock structure simulating the newly added model
        return NextResponse.json({
            id: modelCode,
            providerId: providerCode,
            name: data.name,
            modelId: modelCode,
        });

    } catch (error: unknown) {
        const errorMessage = error instanceof Error ? error.message : "An unknown error occurred";
        console.error("Failed to save model via Python API:", errorMessage);
        return NextResponse.json({ error: errorMessage }, { status: 500 });
    }
}
