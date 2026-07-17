import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { buildModelMutationPayload, parseModelRef } from "@/lib/models/model-admin";
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

        if (!desiredProviderId) {
            return NextResponse.json({ error: "providerId is required" }, { status: 400 });
        }
        if (!desiredModelId) {
            return NextResponse.json({ error: "modelId is required" }, { status: 400 });
        }
        const resPost = await fetch(`${ENGINE_URL}/models/bindings`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                providerId: desiredProviderId,
                modelId: desiredModelId,
                sourceProviderId: providerId,
                sourceModelId,
                source: "manual",
                model: buildModelMutationPayload(data),
            }),
        });
        const payload = await resPost.json().catch(() => ({}));
        return NextResponse.json(payload, { status: resPost.status });

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

        if (!providerId) {
            return NextResponse.json({ error: "provider-qualified modelRef is required" }, { status: 400 });
        }
        const response = await fetch(`${ENGINE_URL}/models/bindings`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ providerId, modelId: sourceModelId }),
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });

    } catch (error: unknown) {
        const errorMessage = error instanceof Error ? error.message : "An unknown error occurred";
        console.error("Failed to delete model via Python API:", errorMessage);
        return NextResponse.json({ error: errorMessage }, { status: 500 });
    }
}
