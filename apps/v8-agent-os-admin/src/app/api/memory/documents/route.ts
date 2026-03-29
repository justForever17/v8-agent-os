import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export const dynamic = "force-dynamic";

export async function GET() {
    try {
        const response = await fetch(`${ENGINE_URL}/v1/memory/documents`);
        if (!response.ok) throw new Error(`Failed: ${response.status}`);
        return NextResponse.json(await response.json());
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
