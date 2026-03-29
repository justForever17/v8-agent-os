import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
    try {
        const { searchParams } = new URL(req.url);
        const scope = searchParams.get("scope") || "";
        const limit = searchParams.get("limit") || "50";
        
        const params = new URLSearchParams();
        if (scope) params.set("scope", scope);
        params.set("limit", limit);
        
        const response = await fetch(`${ENGINE_URL}/v1/memory/knowledge?${params.toString()}`);
        if (!response.ok) throw new Error(`Failed: ${response.status}`);
        return NextResponse.json(await response.json());
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
