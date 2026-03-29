import { NextResponse } from "next/server";
import { readJson, writeJson } from "@/lib/storage";

export async function GET() {
    try {
        const settingsData = readJson<{ settings?: { key: string, value: unknown }[], s3?: unknown }>("settings.json", { settings: [] });
        const setting = settingsData.settings?.find((s) => s.key === "S3_CONFIG")?.value || settingsData.s3 || {
            endpoint: "",
            region: "",
            bucket: "",
            accessKeyId: "",
            secretAccessKey: ""
        };
        return NextResponse.json({ value: setting });
    } catch (error) {
        console.error("Error reading S3 config setting:", error);
        return NextResponse.json({ error: "Failed to read setting" }, { status: 500 });
    }
}

export async function POST(request: Request) {
    try {
        const { value } = await request.json();
        
        if (!value) {
            return NextResponse.json({ error: "Value is required" }, { status: 400 });
        }

        const settingsData = readJson<{ settings?: { key: string, value: unknown }[], s3?: unknown }>("settings.json", { settings: [] });
        
        if (!settingsData.settings) {
            settingsData.settings = [];
        }

        const index = settingsData.settings.findIndex((s) => s.key === "S3_CONFIG");
        if (index > -1) {
            settingsData.settings[index].value = value;
        } else {
            settingsData.settings.push({ key: "S3_CONFIG", value });
        }

        // Also save a raw "s3" top-level key for backward compatibility with older Engine versions
        (settingsData as Record<string, unknown>).s3 = value;

        writeJson("settings.json", settingsData);

        return NextResponse.json({ success: true, value });
    } catch (error) {
        console.error("Error saving S3 config setting:", error);
        return NextResponse.json({ error: "Failed to save setting" }, { status: 500 });
    }
}
