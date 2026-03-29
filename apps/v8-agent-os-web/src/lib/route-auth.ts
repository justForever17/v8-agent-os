import type { NextRequest } from "next/server";
import { getToken } from "next-auth/jwt";

const SESSION_COOKIE_CANDIDATES = [
    "v8-agent-os-web.session-token",
    "__Secure-v8-agent-os-web.session-token",
    "__Host-v8-agent-os-web.session-token",
];

async function resolveViaSessionEndpoint(req: NextRequest): Promise<string | null> {
    const cookieHeader =
        req.headers.get("cookie")
        || req.cookies
            .getAll()
            .map(({ name, value }) => `${name}=${value}`)
            .join("; ");
    if (!cookieHeader) {
        return null;
    }

    const sessionUrl = new URL("/api/auth/session", req.nextUrl.origin);
    const response = await fetch(sessionUrl, {
        method: "GET",
        headers: { cookie: cookieHeader },
        cache: "no-store",
    }).catch(() => null);

    if (!response?.ok) {
        return null;
    }

    const session = await response.json().catch(() => null);
    const email = typeof session?.user?.email === "string" ? session.user.email : null;
    return email;
}

export async function resolveRouteUserEmail(req: NextRequest): Promise<string | null> {
    const secret = process.env.AUTH_SECRET || process.env.NEXTAUTH_SECRET;
    if (secret) {
        const cookieName = SESSION_COOKIE_CANDIDATES.find((name) => req.cookies.has(name));
        const token = await getToken({
            req,
            secret,
            cookieName,
        }).catch(() => null);

        const tokenEmail = typeof token?.email === "string" ? token.email : null;
        if (tokenEmail) {
            return tokenEmail;
        }
    }

    return resolveViaSessionEndpoint(req);
}
