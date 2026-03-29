import { NextRequest, NextResponse } from "next/server";
import { readJson, writeJson } from "@/lib/storage";

export async function GET() {
    try {
        const settingsData = readJson<{ settings: { key: string, value: string }[] }>("settings.json", { settings: [] });
        const setting = settingsData.settings.find(s => s.key === "SUPERVISOR_MODEL_ID");
        return NextResponse.json({ modelId: setting?.value || null });
    } catch {
        return NextResponse.json({ error: "Failed to fetch setting" }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    try {
        const { modelId } = await req.json();

        if (!modelId) {
            return NextResponse.json({ error: "Model ID is required" }, { status: 400 });
        }

        const settingsData = readJson<{ settings: { key: string, value: string }[] }>("settings.json", { settings: [] });
        const settingIndex = settingsData.settings.findIndex(s => s.key === "SUPERVISOR_MODEL_ID");
        
        if (settingIndex !== -1) {
            settingsData.settings[settingIndex].value = modelId;
        } else {
            settingsData.settings.push({ key: "SUPERVISOR_MODEL_ID", value: modelId });
        }
        writeJson("settings.json", settingsData);

        return NextResponse.json({ key: "SUPERVISOR_MODEL_ID", value: modelId });
    } catch {
        return NextResponse.json({ error: "Failed to save setting" }, { status: 500 });
    }
}
