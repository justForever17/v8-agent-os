import { NextRequest, NextResponse } from "next/server";

import { resolveClientUser } from "@/lib/server/client-request-auth";
import { createDevicePairingTicket } from "@/lib/server/device-pairing";
import { buildClientLinkManifest, resolvePairingAdminBaseUrlFromRequest } from "@/lib/server/runtime-config";

function pairingScheme(surface: string) {
    if (surface === "cyber") return "v8agentoscyber";
    if (surface === "web") return "v8agentosweb";
    if (surface === "custom") return "v8agentos";
    return "v8agentosphone";
}

function collectAdminUrls(linkManifest: ReturnType<typeof buildClientLinkManifest>, fallbackBaseUrl: string) {
    const urls = [
        fallbackBaseUrl,
        linkManifest.admin?.baseUrl || "",
        ...(linkManifest.profiles || []).map((profile) => profile.adminBaseUrl || ""),
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
    const adminBaseUrl = resolvePairingAdminBaseUrlFromRequest(req);
    try {
        const ticket = createDevicePairingTicket({
            owner,
            surface: payload?.surface,
            adminBaseUrl,
            deviceName: payload?.deviceName,
            ttlMs: payload?.ttlMs,
        });
        const linkManifest = buildClientLinkManifest(adminBaseUrl);
        const pairingManifest = {
            kind: "v8_device_pairing_manifest",
            version: 1,
            serverId: linkManifest.serverId || linkManifest.instanceId,
            instanceId: ticket.instanceId,
            adminUrls: collectAdminUrls(linkManifest, ticket.adminBaseUrl),
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
            pairingUri: `${pairingScheme(ticket.surface)}://pair?${query.toString()}`,
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "pairing_ticket_create_failed" },
            { status: 400 },
        );
    }
}
