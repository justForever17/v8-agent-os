import { NextRequest } from "next/server";

import { fetchClientEngine } from "@/lib/server/client-proxy";

export async function POST(req: NextRequest) {
    try {
        const body = await req.text();
        const response = await fetchClientEngine(req, "/audio/tts/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
        });

        if (!response.ok || !response.body) {
            const detail = await response.text().catch(() => "");
            return Response.json(
                {
                    error: "TTS request failed",
                    detail: detail || "Engine TTS stream unavailable",
                    status: response.status || 500,
                },
                { status: response.status || 500 },
            );
        }

        return new Response(response.body, {
            status: response.status,
            headers: {
                "Content-Type": response.headers.get("Content-Type") || "audio/mpeg",
                "Cache-Control": "no-store",
            },
        });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown TTS proxy error";
        return Response.json({ error: message }, { status: message === "Unauthorized" ? 401 : 502 });
    }
}
