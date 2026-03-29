import { NextRequest, NextResponse } from "next/server";

import { resolveEngineOrigin } from "@/lib/server/runtime-config";

export async function GET(_req: NextRequest, context: { params: Promise<{ id: string }> }) {
    try {
        const { id } = await context.params;
        const response = await fetch(`${resolveEngineOrigin()}/artifacts/${encodeURIComponent(id)}/content`, {
            cache: "no-store",
        });
        if (!response.ok || !response.body) {
            const text = await response.text().catch(() => "");
            return NextResponse.json({ error: text || "Artifact content unavailable" }, { status: response.status || 502 });
        }
        return new NextResponse(response.body, {
            status: response.status,
            headers: {
                "Content-Type": response.headers.get("Content-Type") || "application/octet-stream",
                "Content-Disposition": response.headers.get("Content-Disposition") || "",
                "Cache-Control": "no-store",
            },
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Artifact content unavailable" },
            { status: 500 },
        );
    }
}
