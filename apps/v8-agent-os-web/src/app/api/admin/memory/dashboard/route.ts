import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@admin/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function GET() {
    try {
        const response = await engineFetch(`${ENGINE_URL}/v1/memory/dashboard`);
        if (!response.ok) {
            throw new Error(`Failed: ${response.status}`);
        }
        return NextResponse.json(await response.json());
    } catch (error) {
        console.error("Error proxying GET /memory/dashboard:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}

export async function DELETE() {
    try {
        const response = await engineFetch(`${ENGINE_URL}/v1/memory/dashboard`, { method: "DELETE" });
        if (!response.ok) {
            throw new Error(`Failed: ${response.status}`);
        }
        return NextResponse.json(await response.json());
    } catch (error) {
        console.error("Error proxying DELETE /memory/dashboard:", error);
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
