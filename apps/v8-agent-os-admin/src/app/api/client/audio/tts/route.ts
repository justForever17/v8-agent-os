import { NextRequest } from "next/server";

import { fetchClientAdmin } from "@/lib/server/client-proxy";

export async function POST(req: NextRequest) {
    try {
        const body = await req.text();
        const response = await fetchClientAdmin(req, "/audio/tts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
        });

        if (!response.ok || !response.body) {
            const detail = await response.text().catch(() => "");
            return new Response(detail || "TTS request failed", { status: response.status || 500 });
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
