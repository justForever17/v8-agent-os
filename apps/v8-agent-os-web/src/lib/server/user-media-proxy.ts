import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveAdminRootUrl } from "@/lib/server/runtime-config";

type UserMediaProxyOptions = {
    allowedKinds: Array<"avatar" | "background">;
    allowLegacyAvatar?: boolean;
};

const LOOPBACK_HOST_PATTERN = /^(?:127(?:\.\d{1,3}){3}|localhost|\[::1\]|::1)$/i;

function isAllowedPath(pathname: string, options: UserMediaProxyOptions) {
    if (options.allowLegacyAvatar && /^\/(?:Avatar|user)\/[^/?#]+$/i.test(pathname)) {
        return true;
    }
    return options.allowedKinds.some((kind) => {
        const extension = kind === "background" ? "(?:webp|mp4)" : "webp";
        return new RegExp(`^/user-assets/${kind}/[A-Za-z0-9][A-Za-z0-9._-]{0,180}\\.${extension}$`, "i").test(pathname);
    });
}

export async function proxyUserMedia(req: NextRequest, options: UserMediaProxyOptions) {
    const session = await auth();
    const identifier = String(session?.user?.email || session?.user?.login || "").trim();
    if (!identifier) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const source = String(req.nextUrl.searchParams.get("src") || "").trim();
    if (!source) {
        return NextResponse.json({ error: "Missing user media source" }, { status: 400 });
    }

    let adminRoot: URL;
    try {
        adminRoot = new URL(await resolveAdminRootUrl());
    } catch {
        return NextResponse.json({ error: "Invalid admin root configuration" }, { status: 500 });
    }

    let target: URL;
    try {
        target = source.startsWith("/") ? new URL(source, adminRoot) : new URL(source);
        if (!isAllowedPath(target.pathname, options)) {
            return NextResponse.json({ error: "Unsupported user media source" }, { status: 400 });
        }
        if (target.origin !== adminRoot.origin && !LOOPBACK_HOST_PATTERN.test(target.hostname)) {
            return NextResponse.json({ error: "Unsupported user media origin" }, { status: 400 });
        }
    } catch {
        return NextResponse.json({ error: "Invalid user media source" }, { status: 400 });
    }

    try {
        const range = req.headers.get("range");
        const upstream = await fetch(target, {
            cache: "no-store",
            headers: range ? { Range: range } : undefined,
        });
        if (upstream.status === 416) {
            const headers = new Headers({
                "Content-Range": upstream.headers.get("content-range") || "bytes */0",
                "Accept-Ranges": "bytes",
            });
            return new NextResponse(null, { status: 416, headers });
        }
        if (!upstream.ok || !upstream.body) {
            return NextResponse.json(
                { error: upstream.status === 404 ? "User media not found" : "Failed to load user media" },
                { status: upstream.status === 404 ? 404 : 502 },
            );
        }
        const headers = new Headers({
            "Content-Type": upstream.headers.get("content-type") || "application/octet-stream",
            "Cache-Control": upstream.headers.get("cache-control") || "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        });
        for (const name of ["etag", "accept-ranges", "content-range", "content-length"]) {
            const value = upstream.headers.get(name);
            if (value) headers.set(name, value);
        }
        return new NextResponse(upstream.body, { status: upstream.status, headers });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Failed to proxy user media" },
            { status: 500 },
        );
    }
}
