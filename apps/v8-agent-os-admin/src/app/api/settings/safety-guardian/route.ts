import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function GET() {
    try {
        const response = await fetch(`${ENGINE_URL}/v1/settings/safety-guardian`, {
            cache: "no-store",
        });
        if (!response.ok) {
            throw new Error(`Failed to fetch safety guardian config: ${response.status}`);
        }
        const data = await response.json();
        return NextResponse.json(data);
    } catch (error) {
        console.error("Error proxying GET /settings/safety-guardian:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}

export async function POST(req: Request) {
    try {
        const body = await req.json();
        const response = await fetch(`${ENGINE_URL}/v1/settings/safety-guardian`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(body),
        });
        if (!response.ok) {
            throw new Error(`Failed to update safety guardian config: ${response.status}`);
        }
        const data = await response.json();
        return NextResponse.json(data);
    } catch (error) {
        console.error("Error proxying POST /settings/safety-guardian:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
