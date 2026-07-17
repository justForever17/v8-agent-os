import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { buildModelMutationPayload, listEngineModels } from "@/lib/models/model-admin";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

    const searchParams = req.nextUrl.searchParams;
    const providerIdFilter = searchParams.get("providerId");

    try {
        const res = await fetch(`${ENGINE_URL}/models/public`, { cache: "no-store" });
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
        const data = await req.json() as Record<string, unknown>;
        const providerCode = String(data.providerId || "").trim();
        if (!providerCode) throw new Error("providerId is required");
        const modelCode = String(data.modelId || "").trim();
        if (!modelCode) throw new Error("modelId is required");
        const resPost = await fetch(`${ENGINE_URL}/models/bindings`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                providerId: providerCode,
                modelId: modelCode,
                source: "manual",
                model: buildModelMutationPayload(data),
            }),
        });
        const payload = await resPost.json().catch(() => ({}));
        if (!resPost.ok) {
            return NextResponse.json(payload, { status: resPost.status });
        }
        return NextResponse.json(payload);

    } catch (error: unknown) {
        const errorMessage = error instanceof Error ? error.message : "An unknown error occurred";
        console.error("Failed to save model via Python API:", errorMessage);
        return NextResponse.json({ error: errorMessage }, { status: 500 });
    }
}
