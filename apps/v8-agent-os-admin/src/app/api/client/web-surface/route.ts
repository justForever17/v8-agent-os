import { NextResponse } from "next/server";

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

function resolveGovernedWebSurfaceUrl(value = process.env.V8_WEB_BASE_URL) {
    const candidate = new URL(String(value || "http://127.0.0.1:9527"));
    if (
        candidate.protocol !== "http:"
        || !LOOPBACK_HOSTS.has(candidate.hostname.toLowerCase())
        || candidate.username
        || candidate.password
    ) {
        throw new Error("invalid_governed_web_surface");
    }
    candidate.pathname = "/chat";
    candidate.search = "";
    candidate.hash = "";
    return candidate;
}

export async function GET() {
    const response = NextResponse.redirect(resolveGovernedWebSurfaceUrl(), 307);
    response.headers.set("Cache-Control", "no-store");
    return response;
}
