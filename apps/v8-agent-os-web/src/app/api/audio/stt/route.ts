import { NextRequest } from "next/server";
import { auth } from "@/lib/auth";
import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest) {
    try {
        const session = await auth();
        if (!session?.user?.email) {
            return Response.json({ error: "Unauthorized" }, { status: 401 });
        }
        const formData = await req.formData();
        const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
        if (!internalSecret) {
            return Response.json({ error: "Configuration Error" }, { status: 500 });
        }
        const res = await fetch(`${adminApiBaseUrl}/audio/stt`, {
            method: "POST",
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            body: formData,
            cache: "no-store",
        });

        const contentType = res.headers.get("Content-Type") || "application/json";
        if (contentType.includes("application/json")) {
            const payload = await res.json().catch(() => ({}));
            return Response.json(payload, { status: res.status });
        }

        const text = await res.text().catch(() => "");
        return new Response(text, {
            status: res.status,
            headers: { "Content-Type": contentType },
        });
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown STT proxy error";
        return Response.json({ error: message }, { status: 500 });
    }
}
