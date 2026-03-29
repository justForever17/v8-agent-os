import { NextRequest, NextResponse } from "next/server";
import { readJson, writeJson } from "@/lib/storage";
import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET() {
    try {
        const unauthorized = await requireAdminIdentity();
        if (unauthorized) return unauthorized;

        const { response, data } = await proxyEngineJson("/config-registry/supervisor");
        if (!response.ok) {
            return NextResponse.json(data, { status: response.status });
        }
        const modelId = (data as { data?: { bindings?: { defaultReplyModel?: string } } }).data?.bindings?.defaultReplyModel || null;
        return NextResponse.json({ modelId, value: modelId, source: "models.json.roles.default" });
    } catch (e) {
        console.error("GET default-agent-model error:", e);
        return NextResponse.json({ error: "Failed to fetch setting" }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    try {
        const unauthorized = await requireAdminIdentity();
        if (unauthorized) return unauthorized;

        const { modelId } = await req.json();
        const { response, data } = await proxyEngineJson("/config-registry/supervisor", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                data: {
                    bindings: {
                        defaultReplyModel: modelId,
                    },
                },
            }),
        });
        if (!response.ok) {
            return NextResponse.json(data, { status: response.status });
        }
        const resolved = (data as { data?: { bindings?: { defaultReplyModel?: string } } }).data?.bindings?.defaultReplyModel || null;
        const settingsData = readJson<{ settings?: { key: string, value: string }[] }>("settings.json", { settings: [] });
        const nextSettings = (settingsData.settings || []).filter(s => s.key !== "DEFAULT_AGENT_MODEL_ID");
        writeJson("settings.json", {
            ...settingsData,
            settings: nextSettings,
        });

        return NextResponse.json({
            key: "DEFAULT_AGENT_MODEL_ID",
            modelId: resolved,
            value: resolved,
            source: "models.json.roles.default",
        });
    } catch (e) {
        console.error("POST default-agent-model error:", e);
        return NextResponse.json({ error: "Failed to save setting" }, { status: 500 });
    }
}
