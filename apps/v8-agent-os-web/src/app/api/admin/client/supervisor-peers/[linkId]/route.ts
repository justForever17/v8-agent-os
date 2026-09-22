import { NextRequest, NextResponse } from "next/server";
import { fetchClientEngine } from "@admin/lib/server/client-proxy";
import { findSupervisorPeer } from "@admin/lib/server/client-supervisor-peers";
type Context = { params: Promise<{ linkId: string }> };

async function mutate(req: NextRequest, context: Context, method: "PATCH" | "DELETE") {
    try {
        const { linkId } = await context.params;
        const peer = await findSupervisorPeer(req, linkId);
        if (peer instanceof NextResponse) return peer;
        const body = method === "PATCH" ? await req.json() : null;
        if (body && (typeof body.remoteNickname !== "string" || !body.remoteNickname.trim() || body.remoteNickname.length > 80)) {
            return NextResponse.json({ error: "Enter a name of 1–80 characters." }, { status: 400 });
        }
        const response = await fetchClientEngine(req, `/network-supervisor/neighbors/${encodeURIComponent(linkId)}`, {
            method, signal: req.signal, headers: { "Content-Type": "application/json" },
            ...(body ? { body: JSON.stringify({ remoteNickname: body.remoteNickname.trim() }) } : {}),
        });
        if (!response.ok) return NextResponse.json({ error: "The Supervisor link could not be updated." }, { status: response.status });
        return NextResponse.json({ ok: true, linkId, servingInstanceId: peer.servingInstanceId });
    } catch { return NextResponse.json({ error: "Unable to update this Supervisor link. Retry." }, { status: 502 }); }
}
export const PATCH = (req: NextRequest, context: Context) => mutate(req, context, "PATCH");
export const DELETE = (req: NextRequest, context: Context) => mutate(req, context, "DELETE");
