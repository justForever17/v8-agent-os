import { NextResponse } from "next/server";
import { readJson, writeJson } from "@/lib/storage";
import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

export async function GET() {
    try {
        const unauthorized = await requireAdminIdentity();
        if (unauthorized) return unauthorized;

        const { response, data } = await proxyEngineJson("/config-registry/computer-use");
        if (!response.ok) {
            return NextResponse.json(data, { status: response.status });
        }
        const value = (data as { data?: { modelBindings?: { ocrAssistModel?: string } } }).data?.modelBindings?.ocrAssistModel || null;
        return NextResponse.json({ value, source: "models.json.roles.vision" });
    } catch (error) {
        console.error("Error reading vision model setting:", error);
        return NextResponse.json({ error: "Failed to read setting" }, { status: 500 });
    }
}

export async function POST(request: Request) {
    try {
        const unauthorized = await requireAdminIdentity();
        if (unauthorized) return unauthorized;

        const { value } = await request.json();
        const { response, data } = await proxyEngineJson("/config-registry/computer-use", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                data: {
                    modelBindings: {
                        ocrAssistModel: value,
                    },
                },
            }),
        });
        if (!response.ok) {
            return NextResponse.json(data, { status: response.status });
        }
        const resolved = (data as { data?: { modelBindings?: { ocrAssistModel?: string } } }).data?.modelBindings?.ocrAssistModel || null;
        const settingsData = readJson<{ settings?: { key: string, value: string }[]; vision_model_id?: string }>("settings.json", { settings: [] });
        const nextSettings = (settingsData.settings || []).filter((item) => item.key !== "VISION_MODEL_ID");
        const nextData = {
            ...settingsData,
            settings: nextSettings,
        };
        delete nextData.vision_model_id;
        writeJson("settings.json", nextData);

        return NextResponse.json({ success: true, value: resolved, source: "models.json.roles.vision" });
    } catch (error) {
        console.error("Error saving vision model setting:", error);
        return NextResponse.json({ error: "Failed to save setting" }, { status: 500 });
    }
}
