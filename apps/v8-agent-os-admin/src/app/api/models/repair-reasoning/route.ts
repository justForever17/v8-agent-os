import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = `${resolveEngineBaseUrl()}/models/repair-reasoning`;

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    try {
        const incoming = await req.json().catch(() => ({}));
        const modelRef = String(incoming?.modelRef || incoming?.model_ref || incoming?.id || "").trim();
        const modelId = String(incoming?.modelId || incoming?.model_id || "").trim();
        const providerId = String(incoming?.providerId || incoming?.provider_id || "").trim();
        const target = modelRef || modelId;
        if (!target) {
            return NextResponse.json({ error: "modelId is required" }, { status: 422 });
        }
        const response = await fetch(ENGINE_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                modelRef,
                model_ref: modelRef,
                modelId: modelId || target,
                model_id: modelId || target,
                providerId,
                provider_id: providerId,
            }),
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown proxy error";
        return NextResponse.json({ error: message }, { status: 500 });
    }
}
