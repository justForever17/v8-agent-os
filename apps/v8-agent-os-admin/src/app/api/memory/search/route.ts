import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function GET(req: Request) {
    try {
        const { searchParams } = new URL(req.url);
        const q = searchParams.get("q") || "";
        const scope = searchParams.get("scope") || "";
        
        const params = new URLSearchParams();
        if (q) params.set("q", q);
        if (scope) params.set("scope", scope);
        
        const response = await fetch(`${ENGINE_URL}/v1/memory/search?${params.toString()}`);
        if (!response.ok) throw new Error(`Failed: ${response.status}`);
        return NextResponse.json(await response.json());
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
