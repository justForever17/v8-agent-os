import { NextRequest, NextResponse } from "next/server";
import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET() {
    try {
        const unauthorized = await requireAdminIdentity();
        if (unauthorized) return unauthorized;

        const { response, data } = await proxyEngineJson("/config-registry/supervisor");
        if (!response.ok) {
            return NextResponse.json(data, { status: response.status });
        }
        const modelRef = (data as { data?: { bindings?: { defaultReplyModel?: string } } }).data?.bindings?.defaultReplyModel || null;
        return NextResponse.json({ modelId: modelRef, modelRef, value: modelRef, source: "models.json.roles.default" });
    } catch (e) {
        console.error("GET default-agent-model error:", e);
        return NextResponse.json({ error: "Failed to fetch setting" }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    try {
        const unauthorized = await requireAdminIdentity();
        if (unauthorized) return unauthorized;

        const incoming = await req.json();
        const modelRef = String(incoming?.modelRef || incoming?.modelId || "").trim();
        const { response, data } = await proxyEngineJson("/config-registry/supervisor", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                data: {
                    bindings: {
                        defaultReplyModel: modelRef,
                    },
                },
            }),
        });
        if (!response.ok) {
            return NextResponse.json(data, { status: response.status });
        }
        const resolved = (data as { data?: { bindings?: { defaultReplyModel?: string } } }).data?.bindings?.defaultReplyModel || null;

        return NextResponse.json({
            key: "DEFAULT_AGENT_MODEL_ID",
            modelId: resolved,
            modelRef: resolved,
            value: resolved,
            source: "config.json#models.roles.default",
        });
    } catch (e) {
        console.error("POST default-agent-model error:", e);
        return NextResponse.json({ error: "Failed to save setting" }, { status: 500 });
    }
}
