import { auth } from "@/lib/auth";
import { getClientProxyConfig } from "@/lib/server/runtime-config";

export async function GET() {
    try {
        const session = await auth();
        if (!session?.user?.email) {
            return Response.json({ error: "Unauthorized" }, { status: 401 });
        }
        const { clientApiBaseUrl, internalSecret } = await getClientProxyConfig();
        if (!internalSecret) {
            return Response.json({ error: "Configuration Error" }, { status: 500 });
        }
        const res = await fetch(`${clientApiBaseUrl}/audio/input-status`, {
            method: "GET",
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            cache: "no-store",
        });
        const payload = await res.json().catch(() => ({}));
        return Response.json(payload, { status: res.status });
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown audio input status proxy error";
        return Response.json({ error: message }, { status: 500 });
    }
}
