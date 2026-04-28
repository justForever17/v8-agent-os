import { NextRequest, NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function POST(request: NextRequest) {
    try {
        const payload = await request.json().catch(() => ({}));
        const response = await fetch(`${ENGINE_URL}/v1/storage-retention/dry-run`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload || {}),
        });
        if (!response.ok) throw new Error(`Engine responded with ${response.status}`);
        return NextResponse.json(await response.json());
    } catch (error) {
        console.error("Error proxying POST /storage-retention/dry-run:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
