import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { buildModelMutationPayload, buildModelRef, listEngineModels } from "@/lib/models/model-admin";
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
        
        const routesData = await res.json();
        const providersDict = routesData.providers || {};
        return NextResponse.json(listEngineModels(providersDict, providerIdFilter));

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
        providerData.models[modelCode] = buildModelMutationPayload(data);
        
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
        const modelRef = buildModelRef(providerCode, modelCode);
        return NextResponse.json({
            id: modelRef,
            modelRef,
            providerId: providerCode,
            modelId: modelCode,
        });

    } catch (error: unknown) {
        const errorMessage = error instanceof Error ? error.message : "An unknown error occurred";
        console.error("Failed to save model via Python API:", errorMessage);
        return NextResponse.json({ error: errorMessage }, { status: 500 });
    }
}
