import { NextRequest, NextResponse } from "next/server";

import { fetchClientEngine, requireClientContext } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) return context;

    try {
        const response = await fetchClientEngine(req, "/config-registry/supervisor");
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            return NextResponse.json({ error: "Supervisor profile unavailable" }, { status: response.status });
        }
        const data = payload && typeof payload === "object" && payload.data && typeof payload.data === "object"
            ? payload.data as Record<string, unknown>
            : {};
        const profile = data.profile && typeof data.profile === "object"
            ? data.profile as Record<string, unknown>
            : {};
        return NextResponse.json({
            name: String(profile.name || "智能主管"),
            roleLabel: String(profile.roleLabel || "主理人"),
            avatar: String(profile.avatar || ""),
        }, {
            headers: { "Cache-Control": "no-store" },
        });
    } catch {
        return NextResponse.json({ error: "Supervisor profile unavailable" }, { status: 502 });
    }
}
