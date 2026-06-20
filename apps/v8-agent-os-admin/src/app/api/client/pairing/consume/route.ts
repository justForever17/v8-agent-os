import { NextRequest, NextResponse } from "next/server";

import { issueMobileSessionForUser, mobileAuthUserResponse } from "@/lib/mobile-auth";
import { consumeDevicePairingTicket } from "@/lib/server/device-pairing";
import { buildClientLinkManifest } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest) {
    const payload = await req.json().catch(() => ({}));
    const result = consumeDevicePairingTicket({
        code: String(payload?.code || ""),
        instanceId: typeof payload?.instanceId === "string" ? payload.instanceId : undefined,
        deviceName: typeof payload?.deviceName === "string" ? payload.deviceName : undefined,
    });
    if (!result.ok) {
        const status = result.reason === "pairing_ticket_expired" || result.reason === "pairing_ticket_consumed"
            ? 410
            : 400;
        return NextResponse.json({ error: result.reason }, { status });
    }

    const sessionKind = String(payload?.sessionKind || "device_token").trim();
    if (sessionKind === "web_session") {
        if (result.surface !== "web") {
            return NextResponse.json({ error: "pairing_surface_mismatch" }, { status: 400 });
        }
        return NextResponse.json({
            instanceId: result.instanceId,
            surface: result.surface,
            adminBaseUrl: result.adminBaseUrl,
            user: mobileAuthUserResponse(result.owner),
            linkManifest: buildClientLinkManifest(result.adminBaseUrl),
        });
    }

    const session = issueMobileSessionForUser(result.owner, result.deviceName);
    return NextResponse.json({
        ...session,
        instanceId: result.instanceId,
        surface: result.surface,
        adminBaseUrl: result.adminBaseUrl,
        linkManifest: buildClientLinkManifest(result.adminBaseUrl),
    });
}
