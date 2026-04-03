import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function PUT(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const session = await auth();
    if (!session?.user?.email) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        const { id } = await params;
        const data = await req.json();
        const providerId = req.nextUrl.searchParams.get("providerId")?.trim() || "";

        const resGet = await fetch(`${ENGINE_URL}/models`);
        if (!resGet.ok) throw new Error(`Python API returned ${resGet.status} on GET`);
        const routesData = await resGet.json();

        if (!routesData.providers) {
            return NextResponse.json({ error: "Model not found" }, { status: 404 });
        }

        // Find the provider that owns this model (since we only have the model ID from the URL params)
        let targetProviderId = providerId && routesData.providers[providerId]?.models?.[id] ? providerId : null;
        if (!targetProviderId) {
            for (const providerKey of Object.keys(routesData.providers)) {
                if (routesData.providers[providerKey].models && routesData.providers[providerKey].models[id]) {
                    targetProviderId = providerKey;
                    break;
                }
            }
        }

        if (!targetProviderId) {
            return NextResponse.json({ error: "Model not found" }, { status: 404 });
        }

        // Merge properties
        routesData.providers[targetProviderId].models[id] = {
            ...routesData.providers[targetProviderId].models[id],
            name: data.name,
            type: data.type,
            contextWindow: data.contextWindow ? parseInt(data.contextWindow) : undefined,
            maxTokens: data.maxTokens ? parseInt(data.maxTokens) : undefined,
            temperature: data.temperature !== undefined ? parseFloat(data.temperature) : undefined,
            costPerInput: data.costPerInput ? parseFloat(data.costPerInput) : undefined,
            costPerOutput: data.costPerOutput ? parseFloat(data.costPerOutput) : undefined,
            rerank_api_flavor: data.rerankApiFlavor || undefined,
        };

        const resPost = await fetch(`${ENGINE_URL}/models`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(routesData),
        });

        if (!resPost.ok) throw new Error(`Python API returned ${resPost.status} on POST`);

        return NextResponse.json({
             id,
             providerId: targetProviderId,
             ...routesData.providers[targetProviderId].models[id]
        });

    } catch (error: unknown) {
        const errorMessage = error instanceof Error ? error.message : "An unknown error occurred";
        console.error("Failed to update model via Python API:", errorMessage);
        return NextResponse.json({ error: errorMessage }, { status: 500 });
    }
}

export async function DELETE(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const session = await auth();
    if (!session?.user?.email) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        const { id } = await params;
        const providerId = req.nextUrl.searchParams.get("providerId")?.trim() || "";

        const resGet = await fetch(`${ENGINE_URL}/models`);
        if (!resGet.ok) throw new Error(`Python API returned ${resGet.status} on GET`);
        const routesData = await resGet.json();

        if (routesData.providers) {
            let modelFound = false;
            if (providerId && routesData.providers[providerId]?.models?.[id]) {
                delete routesData.providers[providerId].models[id];
                modelFound = true;
            } else {
                for (const providerKey of Object.keys(routesData.providers)) {
                    if (routesData.providers[providerKey].models && routesData.providers[providerKey].models[id]) {
                        delete routesData.providers[providerKey].models[id];
                        modelFound = true;
                        break;
                    }
                }
            }

            if (modelFound) {
                const resPost = await fetch(`${ENGINE_URL}/models`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(routesData),
                });
                if (!resPost.ok) throw new Error(`Python API returned ${resPost.status} on POST`);
            } else {
                return NextResponse.json({ error: "Model not found" }, { status: 404 });
            }
        }

        return NextResponse.json({ success: true });

    } catch (error: unknown) {
        const errorMessage = error instanceof Error ? error.message : "An unknown error occurred";
        console.error("Failed to delete model via Python API:", errorMessage);
        return NextResponse.json({ error: errorMessage }, { status: 500 });
    }
}
