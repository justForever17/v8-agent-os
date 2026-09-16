import { NextRequest, NextResponse } from "next/server";

import { listMobileDeviceSessions, revokeMobileDeviceSession } from "@/lib/mobile-auth";
import { resolveAdminIdentity } from "@/lib/server/engine-proxy";
import { findUserByIdentifier } from "@/lib/users";

async function requireOwner(req: NextRequest) {
    const identifier = await resolveAdminIdentity(req);
    const owner = identifier ? await findUserByIdentifier(identifier) : null;
    return owner?.role === "ADMIN" ? owner : null;
}

export async function GET(req: NextRequest) {
    const owner = await requireOwner(req);
    if (!owner) {
        return NextResponse.json({ error: "owner_admin_required" }, { status: 403 });
    }
    return NextResponse.json({ devices: await listMobileDeviceSessions(owner.id) });
}

export async function DELETE(req: NextRequest) {
    const owner = await requireOwner(req);
    if (!owner) {
        return NextResponse.json({ error: "owner_admin_required" }, { status: 403 });
    }
    const payload = await req.json().catch(() => ({}));
    const deviceSessionId = String(payload?.deviceSessionId || "").trim();
    if (!deviceSessionId) {
        return NextResponse.json({ error: "device_session_id_required" }, { status: 400 });
    }
    const revoked = await revokeMobileDeviceSession(owner.id, deviceSessionId);
    return NextResponse.json({ revoked }, { status: revoked ? 200 : 404 });
}
