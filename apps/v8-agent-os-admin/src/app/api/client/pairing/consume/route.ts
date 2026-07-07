import { NextRequest, NextResponse } from "next/server";

import { issueMobileSessionForUser } from "@/lib/mobile-auth";
import { consumeDevicePairingTicket } from "@/lib/server/device-pairing";
import { buildClientLinkManifest } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest) {
    const payload = await req.json().catch(() => ({}));
    const sessionKind = String(payload?.sessionKind || "device_token").trim();
    if (sessionKind === "web_session") {
        return NextResponse.json({ error: "web_local_session_required" }, { status: 400 });
    }

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

    if (result.surface !== "phone") {
        return NextResponse.json({ error: "phone_pairing_only" }, { status: 400 });
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
