import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { buildModelMutationPayload, buildModelRef, parseModelRef } from "@/lib/models/model-admin";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function PUT(
    req: NextRequest,
    { params }: { params: Promise<{ id: string }> }
) {
    const session = await auth();
    if (!session?.user?.email) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    try {
        const { id: rawId } = await params;
        const id = decodeURIComponent(rawId);
        const parsedRef = parseModelRef(id);
        const data = await req.json();
        const providerId = parsedRef?.providerId || req.nextUrl.searchParams.get("providerId")?.trim() || "";
        const sourceModelId = parsedRef?.modelId || id;
        const desiredProviderId = String(data?.providerId || "").trim() || providerId;
        const desiredModelId = String(data?.modelId || sourceModelId).trim();

        const resGet = await fetch(`${ENGINE_URL}/models`);
        if (!resGet.ok) throw new Error(`Python API returned ${resGet.status} on GET`);
        const routesData = await resGet.json();

        if (!routesData.providers) {
            return NextResponse.json({ error: "Model not found" }, { status: 404 });
        }

        // Find the provider that owns this model (since we only have the model ID from the URL params)
        let targetProviderId = providerId && routesData.providers[providerId]?.models?.[sourceModelId] ? providerId : null;
        if (!targetProviderId) {
            const matches: string[] = [];
            for (const providerKey of Object.keys(routesData.providers)) {
                if (routesData.providers[providerKey].models && routesData.providers[providerKey].models[sourceModelId]) {
                    matches.push(providerKey);
                }
            }
            if (matches.length > 1) {
                return NextResponse.json({ error: "Ambiguous modelId; provider-qualified modelRef is required", matches }, { status: 409 });
            }
            targetProviderId = matches[0] || null;
        }

        if (!targetProviderId) {
            return NextResponse.json({ error: "Model not found" }, { status: 404 });
        }

        if (!desiredProviderId) {
            return NextResponse.json({ error: "providerId is required" }, { status: 400 });
        }

        if (!routesData.providers[desiredProviderId]) {
            return NextResponse.json({ error: "Target provider not found" }, { status: 404 });
        }
        if (!routesData.providers[desiredProviderId].models) {
            routesData.providers[desiredProviderId].models = {};
        }

        if (!desiredModelId) {
            return NextResponse.json({ error: "modelId is required" }, { status: 400 });
        }

        const existingModel = {
            ...(routesData.providers[targetProviderId].models[sourceModelId] || {}),
            ...buildModelMutationPayload(data),
        };
        delete existingModel.name;
        delete routesData.providers[targetProviderId].models[sourceModelId];
        routesData.providers[desiredProviderId].models[desiredModelId] = existingModel;

        const resPost = await fetch(`${ENGINE_URL}/models`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(routesData),
        });

        if (!resPost.ok) throw new Error(`Python API returned ${resPost.status} on POST`);

        const modelRef = buildModelRef(desiredProviderId, desiredModelId);
        return NextResponse.json({
             id: modelRef,
             modelRef,
             providerId: desiredProviderId,
             modelId: desiredModelId,
             ...routesData.providers[desiredProviderId].models[desiredModelId]
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
        const { id: rawId } = await params;
        const id = decodeURIComponent(rawId);
        const parsedRef = parseModelRef(id);
        const providerId = parsedRef?.providerId || req.nextUrl.searchParams.get("providerId")?.trim() || "";
        const sourceModelId = parsedRef?.modelId || id;

        const resGet = await fetch(`${ENGINE_URL}/models`);
        if (!resGet.ok) throw new Error(`Python API returned ${resGet.status} on GET`);
        const routesData = await resGet.json();

        if (routesData.providers) {
            let modelFound = false;
            if (providerId && routesData.providers[providerId]?.models?.[sourceModelId]) {
                delete routesData.providers[providerId].models[sourceModelId];
                modelFound = true;
            } else {
                const matches: string[] = [];
                for (const providerKey of Object.keys(routesData.providers)) {
                    if (routesData.providers[providerKey].models && routesData.providers[providerKey].models[sourceModelId]) {
                        matches.push(providerKey);
                    }
                }
                if (matches.length > 1) {
                    return NextResponse.json({ error: "Ambiguous modelId; provider-qualified modelRef is required", matches }, { status: 409 });
                }
                if (matches[0]) {
                    delete routesData.providers[matches[0]].models[sourceModelId];
                    modelFound = true;
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
