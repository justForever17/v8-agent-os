import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function DELETE(req: Request, { params }: { params: Promise<{ filename: string }> }) {
    try {
        const { filename: rawFilename } = await params;
        const filename = encodeURIComponent(rawFilename);
        
        const response = await fetch(`${ENGINE_URL}/v1/memory/documents/${filename}`, {
            method: "DELETE"
        });
        
        if (!response.ok) throw new Error(`Failed: ${response.status}`);
        return NextResponse.json(await response.json());
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
