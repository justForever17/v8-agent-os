import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";

import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export async function GET(_req: Request, context: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const { id } = await context.params;
        const response = await fetch(`${adminApiBaseUrl}/memory/artifacts/${encodeURIComponent(id)}`, {
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            cache: "no-store",
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("[ArtifactsProxy] DETAIL failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}
