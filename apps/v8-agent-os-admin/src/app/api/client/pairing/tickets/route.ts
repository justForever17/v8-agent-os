import { NextRequest, NextResponse } from "next/server";

import { resolveAdminIdentity } from "@/lib/server/engine-proxy";
import { findUserByIdentifier } from "@/lib/users";
import { createDevicePairingTicket, revokeDevicePairingTicket } from "@/lib/server/device-pairing";
import { identityErrorResponse } from "@/lib/server/identity-route";
import { buildClientLinkManifest, resolvePairingAdminBaseUrlFromRequest } from "@/lib/server/runtime-config";

function collectAdminUrls(linkManifest: Awaited<ReturnType<typeof buildClientLinkManifest>>, fallbackBaseUrl: string) {
    const urls = [
        fallbackBaseUrl,
        linkManifest.admin?.baseUrl || "",
        ...(linkManifest.endpoints || []).map((endpoint) => endpoint.baseUrl || ""),
    ];
    return urls
        .map((url) => String(url || "").trim().replace(/\/+$/, "").replace(/\/api$/, ""))
        .filter((url, index, all) => Boolean(url) && all.indexOf(url) === index);
}

export async function POST(req: NextRequest) {
    const identifier = await resolveAdminIdentity(req);
    const owner = identifier ? await findUserByIdentifier(identifier) : null;
    if (!owner || owner.role !== "ADMIN") {
        return NextResponse.json({ error: "owner_admin_required" }, { status: 403 });
    }

    const payload = await req.json().catch(() => ({}));
    const requestedSurface = String(payload?.surface || "phone").trim().toLowerCase();
    if (requestedSurface !== "phone") {
        return NextResponse.json({ error: "phone_pairing_only" }, { status: 400 });
    }

    const adminBaseUrl = resolvePairingAdminBaseUrlFromRequest(req);
    try {
        const ticket = await createDevicePairingTicket({
            owner,
            surface: "phone",
            adminBaseUrl,
            deviceName: payload?.deviceName,
            ttlMs: payload?.ttlMs,
        });
        const linkManifest = await buildClientLinkManifest(adminBaseUrl);
        const pairingManifest = {
            kind: "v8_device_pairing_manifest",
            version: 2,
            serverId: linkManifest.serverId || linkManifest.instanceId,
            instanceId: ticket.instanceId,
            adminUrls: collectAdminUrls(linkManifest, ticket.adminBaseUrl),
            lanUrls: (linkManifest.endpoints || [])
                .filter((endpoint) => endpoint.kind === "lan" || endpoint.kind === "lan_ipv6")
                .map((endpoint) => endpoint.baseUrl),
            tailscaleUrls: (linkManifest.endpoints || [])
                .filter((endpoint) => endpoint.kind === "tailscale" || endpoint.kind === "headscale")
                .map((endpoint) => endpoint.baseUrl),
            cloudflareUrls: (linkManifest.endpoints || [])
                .filter((endpoint) => endpoint.kind === "cloudflare_tunnel")
                .map((endpoint) => endpoint.baseUrl),
            endpoints: linkManifest.endpoints || [],
            pairingCode: ticket.pairingCode,
            surface: ticket.surface,
        };
        const query = new URLSearchParams({
            admin: ticket.adminBaseUrl,
            code: ticket.pairingCode,
            instance: ticket.instanceId,
            surface: ticket.surface,
            manifest: JSON.stringify(pairingManifest),
        });
        return NextResponse.json({
            ok: true,
            kind: "v8_device_pairing_ticket",
            ...ticket,
            serverId: pairingManifest.serverId,
            adminUrls: pairingManifest.adminUrls,
            pairingManifest,
            pairingUri: `v8agentosphone://pair?${query.toString()}`,
        });
    } catch (error) {
        return identityErrorResponse(error);
    }
}

export async function DELETE(req: NextRequest) {
    try {
        if (!await resolveAdminIdentity(req)) return NextResponse.json({ error: "owner_admin_required" }, { status: 403 });
        const { pairingId } = await req.json();
        if (typeof pairingId !== "string" || !pairingId) return NextResponse.json({ error: "pairing_id_required" }, { status: 400 });
        return NextResponse.json(await revokeDevicePairingTicket(pairingId));
    } catch (error) { return identityErrorResponse(error); }
}
