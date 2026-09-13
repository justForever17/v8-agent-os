import { NextRequest, NextResponse } from "next/server";
import { fetchClientEngine, requireClientContext } from "@/lib/server/client-proxy";
import { buildClientLinkManifest, resolveRequestOrigin } from "@/lib/server/runtime-config";

type Link = Record<string, unknown>;
export function publicSupervisorPeer(link: Link, servingInstanceId: string) {
    const linkId = String(link.linkId || link.id || "");
    return {
        linkId, peerId: String(link.peerId || ""), servingInstanceId,
        sessionId: `network_neighbor_${linkId}`, sessionKind: "local_neighbor" as const,
        displayName: String(link.remoteNickname || link.displayName || link.peerId || ""),
        online: link.online === true, lastSeenAt: String(link.lastSeenAt || ""),
        localRole: String(link.localRole || ""), remoteRole: String(link.remoteRole || ""),
        trustStatus: String(link.trustStatus || ""), description: String(link.description || ""),
    };
}

export async function supervisorPeerContext(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) return context;
    if (String(context.user.role).toUpperCase() !== "ADMIN") {
        return NextResponse.json({ error: "Owner access is required to manage paired Supervisors." }, { status: 403 });
    }
    const response = await fetchClientEngine(req, "/network-supervisor/neighbors/links", { signal: req.signal });
    if (!response.ok) return NextResponse.json({ error: "Paired Supervisors are unavailable." }, { status: response.status });
    const payload = await response.json();
    // Network discovery is deliberately excluded. Only previously authorized links.
    const links: Link[] = (Array.isArray(payload.items) ? payload.items : [])
        .filter((link: Link) => link.trustStatus === "trusted" && (link.linkId || link.id));
    const manifest = buildClientLinkManifest(resolveRequestOrigin(req));
    return { links, servingInstanceId: manifest.instanceId };
}

export async function findSupervisorPeer(req: NextRequest, linkId: string) {
    const context = await supervisorPeerContext(req);
    if (context instanceof NextResponse) return context;
    const link = context.links.find((item) => String(item.linkId || item.id) === linkId);
    if (!link) return NextResponse.json({ error: "This Supervisor link is unavailable or revoked." }, { status: 404 });
    return publicSupervisorPeer(link, context.servingInstanceId);
}
