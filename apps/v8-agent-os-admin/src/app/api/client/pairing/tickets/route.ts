import { NextRequest, NextResponse } from "next/server";

import { resolveClientUser } from "@/lib/server/client-request-auth";
import { createDevicePairingTicket } from "@/lib/server/device-pairing";
import { buildClientLinkManifest, resolvePairingAdminBaseUrlFromRequest } from "@/lib/server/runtime-config";

function collectAdminUrls(linkManifest: ReturnType<typeof buildClientLinkManifest>, fallbackBaseUrl: string) {
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
    const owner = await resolveClientUser(req);
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
        const ticket = createDevicePairingTicket({
            owner,
            surface: "phone",
            adminBaseUrl,
            deviceName: payload?.deviceName,
            ttlMs: payload?.ttlMs,
        });
        const linkManifest = buildClientLinkManifest(adminBaseUrl);
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
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "pairing_ticket_create_failed" },
            { status: 400 },
        );
    }
}
