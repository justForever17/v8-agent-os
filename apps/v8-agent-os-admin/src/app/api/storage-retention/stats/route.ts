import { engineFetch } from "@/lib/server/engine-fetch";
import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function GET() {
    try {
        const response = await engineFetch(`${ENGINE_URL}/v1/storage-retention/stats`, { cache: "no-store" });
        if (!response.ok) throw new Error(`Engine responded with ${response.status}`);
        return NextResponse.json(await response.json());
    } catch (error) {
        console.error("Error proxying GET /storage-retention/stats:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
