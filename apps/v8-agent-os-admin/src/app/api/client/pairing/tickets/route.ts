import { NextRequest, NextResponse } from "next/server";

import { resolveClientUser } from "@/lib/server/client-request-auth";
import { createDevicePairingTicket } from "@/lib/server/device-pairing";
import { resolveClientSurfaceOriginFromRequest, resolveAdminPublicBaseUrl, resolveRequestOrigin } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest) {
    const owner = await resolveClientUser(req);
    if (!owner || owner.role !== "ADMIN") {
        return NextResponse.json({ error: "owner_admin_required" }, { status: 403 });
    }

    const payload = await req.json().catch(() => ({}));
    const adminBaseUrl = (
        resolveClientSurfaceOriginFromRequest(req, { allowTrustedHeader: false })
        || resolveRequestOrigin(req)
        || resolveAdminPublicBaseUrl()
    ).replace(/\/$/, "");
    try {
        const ticket = createDevicePairingTicket({
            owner,
            surface: payload?.surface,
            adminBaseUrl,
            deviceName: payload?.deviceName,
            ttlMs: payload?.ttlMs,
        });
        return NextResponse.json({
            ok: true,
            kind: "v8_device_pairing_ticket",
            ...ticket,
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "pairing_ticket_create_failed" },
            { status: 400 },
        );
    }
}
