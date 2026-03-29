import { NextResponse } from "next/server";
import { readJson, writeJson } from "@/lib/storage";

export async function GET() {
    try {
        const settingsData = readJson<{ settings: { key: string, value: string }[] }>("settings.json", { settings: [] });
        const setting = settingsData.settings.find(s => s.key === "supervisor-name");
        return NextResponse.json({ value: setting?.value || "智能主管" });
    } catch {
        return NextResponse.json({ error: "Failed to fetch setting" }, { status: 500 });
    }
}

export async function POST(req: Request) {
    try {
        const { value } = await req.json();

        const settingsData = readJson<{ settings: { key: string, value: string }[] }>("settings.json", { settings: [] });
        const settingIndex = settingsData.settings.findIndex(s => s.key === "supervisor-name");
        
        if (settingIndex !== -1) {
            settingsData.settings[settingIndex].value = value;
        } else {
            settingsData.settings.push({ key: "supervisor-name", value });
        }
        writeJson("settings.json", settingsData);

        return NextResponse.json({ key: "supervisor-name", value });
    } catch {
        return NextResponse.json({ error: "Failed to save setting" }, { status: 500 });
    }
}
