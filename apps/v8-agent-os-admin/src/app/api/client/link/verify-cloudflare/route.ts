import dns from "node:dns/promises";
import net from "node:net";
import { NextRequest, NextResponse } from "next/server";

import { resolveClientUser } from "@/lib/server/client-request-auth";
import { readOrCreateInstanceIdentity } from "@/lib/server/instance-identity";

function isBlockedAddress(address: string) {
    if (net.isIPv4(address)) {
        const octets = address.split(".").map((value) => Number(value));
        const [first, second] = octets;
        return first === 0
            || first === 10
            || first === 127
            || (first === 169 && second === 254)
            || (first === 172 && second >= 16 && second <= 31)
            || (first === 192 && second === 168)
            || first >= 224;
    }
    if (net.isIPv6(address)) {
        const normalized = address.toLowerCase();
        return normalized === "::" || normalized === "::1"
            || normalized.startsWith("fe80:")
            || normalized.startsWith("fc")
            || normalized.startsWith("fd")
            || normalized.startsWith("ff");
    }
    return true;
}

function normalizeStableTunnelOrigin(value: unknown) {
    const raw = String(value || "").trim();
    const parsed = new URL(raw);
    if (parsed.protocol !== "https:") throw new Error("cloudflare_https_required");
    if (parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error("cloudflare_origin_invalid");
    if (parsed.pathname && parsed.pathname !== "/") throw new Error("cloudflare_origin_must_not_include_path");
    const hostname = parsed.hostname.toLowerCase();
    if (!hostname || hostname === "localhost" || hostname.endsWith(".trycloudflare.com")) {
        throw new Error(hostname.endsWith(".trycloudflare.com") ? "quick_tunnel_not_supported_for_phone_realtime" : "cloudflare_origin_invalid");
    }
    return parsed.origin;
}

export async function POST(req: NextRequest) {
    const owner = await resolveClientUser(req);
    if (!owner || owner.role !== "ADMIN") {
        return NextResponse.json({ error: "owner_admin_required" }, { status: 403 });
    }

    try {
        const payload = await req.json().catch(() => ({}));
        const origin = normalizeStableTunnelOrigin(payload?.adminBaseUrl);
        const hostname = new URL(origin).hostname;
        const addresses = await dns.lookup(hostname, { all: true, verbatim: true });
        if (addresses.length === 0 || addresses.some((item) => isBlockedAddress(item.address))) {
            throw new Error("cloudflare_origin_must_resolve_publicly");
        }

        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 5_000);
        const response = await fetch(`${origin}/api/client/instance`, {
            cache: "no-store",
            redirect: "error",
            signal: controller.signal,
            headers: { Accept: "application/json" },
        }).finally(() => clearTimeout(timer));
        if (!response.ok) throw new Error(`cloudflare_probe_http_${response.status}`);
        const remote = await response.json().catch(() => ({}));
        const local = readOrCreateInstanceIdentity();
        if (String(remote?.instanceId || "") !== local.instanceId) {
            throw new Error("cloudflare_instance_mismatch");
        }
        return NextResponse.json({
            ok: true,
            kind: "phone_remote_link_verification",
            origin,
            instanceId: local.instanceId,
            transport: "cloudflare_tunnel",
            quickTunnel: false,
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "cloudflare_tunnel_probe_failed" },
            { status: 400 },
        );
    }
}
