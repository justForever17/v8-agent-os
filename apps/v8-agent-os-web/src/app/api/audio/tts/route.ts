import { NextRequest } from "next/server";
import { auth } from "@/lib/auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest) {
    try {
        const session = await auth();
        if (!session?.user?.email) {
            return Response.json({ error: "Unauthorized" }, { status: 401 });
        }
        const body = await req.json();
        const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
        if (!internalSecret) {
            return Response.json({ error: "Configuration Error" }, { status: 500 });
        }
        const res = await fetch(`${adminApiBaseUrl}/audio/tts`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            body: JSON.stringify(body),
            cache: "no-store",
        });

        if (!res.ok || !res.body) {
            const detail = await res.text().catch(() => "");
            return new Response(detail || "TTS request failed", { status: res.status || 500 });
        }

        return new Response(res.body, {
            status: res.status,
            headers: {
                "Content-Type": res.headers.get("Content-Type") || "audio/mpeg",
                "Cache-Control": "no-store",
            },
        });
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown TTS proxy error";
        return Response.json({ error: message }, { status: 500 });
    }
}
