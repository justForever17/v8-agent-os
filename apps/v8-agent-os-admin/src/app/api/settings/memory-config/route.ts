import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function GET() {
    try {
        const response = await fetch(`${ENGINE_URL}/v1/memory/config`);
        if (!response.ok) {
            throw new Error(`Failed to fetch memory config: ${response.status}`);
        }
        const data = await response.json();
        return NextResponse.json(data);
    } catch (error) {
        console.error("Error proxying GET /memory/config:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}

export async function POST(req: Request) {
    try {
        const body = await req.json();
        const response = await fetch(`${ENGINE_URL}/v1/memory/config`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(body)
        });
        if (!response.ok) {
            throw new Error(`Failed to update memory config: ${response.status}`);
        }
        const data = await response.json();
        return NextResponse.json(data);
    } catch (error) {
        console.error("Error proxying POST /memory/config:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
