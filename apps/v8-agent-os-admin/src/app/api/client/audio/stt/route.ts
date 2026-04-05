import { NextRequest } from "next/server";

import { fetchClientAdmin } from "@/lib/server/client-proxy";

export async function POST(req: NextRequest) {
    try {
        const formData = await req.formData();
        const response = await fetchClientAdmin(req, "/audio/stt", {
            method: "POST",
            body: formData,
        });
        const contentType = response.headers.get("Content-Type") || "application/json";
        if (contentType.includes("application/json")) {
            const payload = await response.json().catch(() => ({}));
            return Response.json(payload, { status: response.status });
        }
        const text = await response.text().catch(() => "");
        return new Response(text, {
            status: response.status,
            headers: { "Content-Type": contentType },
        });
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown STT proxy error";
        return Response.json({ error: message }, { status: message === "Unauthorized" ? 401 : 502 });
    }
}
