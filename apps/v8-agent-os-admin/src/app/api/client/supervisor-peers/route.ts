import { NextRequest, NextResponse } from "next/server";
import { publicSupervisorPeer, supervisorPeerContext } from "@/lib/server/client-supervisor-peers";

export async function GET(req: NextRequest) {
    try {
        const context = await supervisorPeerContext(req);
        if (context instanceof NextResponse) return context;
        const query = (req.nextUrl.searchParams.get("q") || "").toLocaleLowerCase();
        const offset = Math.max(0, Number(req.nextUrl.searchParams.get("cursor")) || 0);
        const limit = Math.min(100, Math.max(1, Number(req.nextUrl.searchParams.get("limit")) || 40));
        const items = context.links.map((link) => publicSupervisorPeer(link, context.servingInstanceId))
            .filter((peer) => `${peer.displayName} ${peer.peerId}`.toLocaleLowerCase().includes(query))
            .sort((a, b) => a.linkId.localeCompare(b.linkId));
        return NextResponse.json({ servingInstanceId: context.servingInstanceId, items: items.slice(offset, offset + limit),
            nextCursor: offset + limit < items.length ? String(offset + limit) : null, total: items.length });
    } catch { return NextResponse.json({ error: "Unable to load paired Supervisors. Retry." }, { status: 502 }); }
}
