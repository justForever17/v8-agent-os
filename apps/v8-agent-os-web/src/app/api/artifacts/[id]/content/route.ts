import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";

import { resolveAdminApiBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

export async function GET(_req: Request, context: { params: Promise<{ id: string }> }) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const internalSecret = await resolveInternalSecret();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const { id } = await context.params;
        const response = await fetch(`${await resolveAdminApiBaseUrl()}/memory/artifacts/${encodeURIComponent(id)}/content`, {
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            cache: "no-store",
        });
        if (!response.ok || !response.body) {
            const data = await response.json().catch(() => ({}));
            return NextResponse.json(data, { status: response.status });
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
        console.error("[ArtifactsProxy] CONTENT failed:", error);
        return NextResponse.json({ error: "Backend Service Unavailable" }, { status: 502 });
    }
}
