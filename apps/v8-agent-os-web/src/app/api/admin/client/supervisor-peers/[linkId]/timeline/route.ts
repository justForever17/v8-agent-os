import { NextRequest, NextResponse } from "next/server";
import { fetchClientEngine } from "@admin/lib/server/client-proxy";
import { findSupervisorPeer } from "@admin/lib/server/client-supervisor-peers";

export async function GET(req: NextRequest, context: { params: Promise<{ linkId: string }> }) {
    try {
        const { linkId } = await context.params;
        const peer = await findSupervisorPeer(req, linkId);
        if (peer instanceof NextResponse) return peer;
        const query = new URLSearchParams({ limit: "50" });
        for (const key of ["cursor", "before"]) {
            const value = req.nextUrl.searchParams.get(key);
            if (value && /^\d{1,20}$/.test(value)) query.set(key, value);
        }
        const response = await fetchClientEngine(req, `/network-supervisor/neighbors/${encodeURIComponent(linkId)}/timeline?${query}`, { signal: req.signal });
        if (!response.ok) return NextResponse.json({ error: "This conversation is unavailable." }, { status: response.status });
        const payload = await response.json();
        return NextResponse.json({ peer, nextCursor: payload.nextCursor, previousCursor: payload.previousCursor,
            items: (Array.isArray(payload.items) ? payload.items : []).map((item: Record<string, unknown>) => ({
                id: item.id || item.messageId, seq: item.seq, body: item.body, direction: item.direction,
                status: item.status, createdAt: item.createdAt, fromNickname: item.fromNickname,
            })) });
    } catch { return NextResponse.json({ error: "Unable to load this conversation. Retry." }, { status: 502 }); }
}
