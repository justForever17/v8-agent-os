import { NextRequest, NextResponse } from "next/server";

import { issueMobileSessionForUser } from "@/lib/mobile-auth";
import { buildClientLinkManifest, resolveRequestOrigin } from "@/lib/server/runtime-config";
import { listUsers } from "@/lib/users";

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);
const TRUSTED_LOCAL_SURFACES = new Set(["web", "cyber"]);

function normalizeHost(value: string | null) {
    const text = String(value || "")
        .trim()
        .toLowerCase();
    if (!text) return "";
    if (text.startsWith("[") && text.includes("]")) {
        return text.slice(1, text.indexOf("]"));
    }
    const colonCount = (text.match(/:/g) || []).length;
    if (colonCount > 1 && !text.includes(".")) {
        return text;
    }
    return text.split(":")[0];
}

function isLoopbackRequest(req: NextRequest) {
    const host = normalizeHost(req.headers.get("host"));
    if (!LOOPBACK_HOSTS.has(host)) {
        return false;
    }

    const forwardedFor = String(req.headers.get("x-forwarded-for") || "")
        .split(",")
        .map((item) => normalizeHost(item))
        .filter(Boolean);
    const realIp = normalizeHost(req.headers.get("x-real-ip"));
    const peerHints = [...forwardedFor, realIp].filter(Boolean);
    return peerHints.every((hint) => LOOPBACK_HOSTS.has(hint));
}

export async function POST(req: NextRequest) {
    if (!isLoopbackRequest(req)) {
        return NextResponse.json({ error: "local_session_requires_loopback" }, { status: 403 });
    }

    const body = await req.json().catch(() => ({}));
    const surface = String(body?.surface || "").trim().toLowerCase();
    if (!TRUSTED_LOCAL_SURFACES.has(surface)) {
        return NextResponse.json({ error: "unsupported_local_surface" }, { status: 400 });
    }

    const owner = listUsers().find((user) => user.role === "ADMIN");
    if (!owner) {
        return NextResponse.json({ error: "owner_not_initialized" }, { status: 409 });
    }

    const deviceName = String(body?.deviceName || `v8-local-${surface}`).trim();
    const tokenPair = issueMobileSessionForUser(owner, deviceName);
    const adminBaseUrl = resolveRequestOrigin(req);

    return NextResponse.json({
        ok: true,
        kind: "v8_local_client_session",
        ownerMode: "single_owner",
        surface,
        trustedLocal: true,
        adminBaseUrl,
        linkManifest: buildClientLinkManifest(adminBaseUrl),
        ...tokenPair,
    });
}
