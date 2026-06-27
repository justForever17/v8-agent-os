import { NextRequest } from "next/server";

import { fetchClientAdmin } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest) {
    try {
        const response = await fetchClientAdmin(req, "/audio/input-status", {
            method: "GET",
        });
        const payload = await response.json().catch(() => ({}));
        return Response.json(payload, { status: response.status });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown audio input status proxy error";
        return Response.json({ error: message }, { status: message === "Unauthorized" ? 401 : 502 });
    }
}
