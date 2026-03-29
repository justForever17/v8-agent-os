import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function POST(req: Request) {
    try {
        const formData = await req.formData();
        
        const response = await fetch(`${ENGINE_URL}/v1/memory/upload`, {
            method: "POST",
            body: formData, // passing raw FormData
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || `Failed: ${response.status}`);
        }
        
        return NextResponse.json(data);
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
