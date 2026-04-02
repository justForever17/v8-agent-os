import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveAdminRootUrl } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

const AVATAR_PATH_PATTERN = /^\/Avatar\/[^?#]+$/i;
const LOOPBACK_HOST_PATTERN = /^(?:127(?:\.\d{1,3}){3}|localhost|\[::1\]|::1)$/i;

function isAllowedAbsoluteAvatarUrl(target: URL, adminRoot: URL) {
    if (!AVATAR_PATH_PATTERN.test(target.pathname)) {
        return false;
    }
    return target.origin === adminRoot.origin || LOOPBACK_HOST_PATTERN.test(target.hostname);
}

export async function GET(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const source = String(req.nextUrl.searchParams.get("src") || "").trim();
    if (!source) {
        return NextResponse.json({ error: "Missing avatar source" }, { status: 400 });
    }

    let adminRoot: URL;
    try {
        adminRoot = new URL(await resolveAdminRootUrl());
    } catch {
        return NextResponse.json({ error: "Invalid admin root configuration" }, { status: 500 });
    }

    let target: URL;
    try {
        if (source.startsWith("/")) {
            if (!AVATAR_PATH_PATTERN.test(source)) {
                return NextResponse.json({ error: "Unsupported avatar path" }, { status: 400 });
            }
            target = new URL(source, adminRoot);
        } else {
            target = new URL(source);
            if (!isAllowedAbsoluteAvatarUrl(target, adminRoot)) {
                return NextResponse.json({ error: "Unsupported avatar source" }, { status: 400 });
            }
        }
    } catch {
        return NextResponse.json({ error: "Invalid avatar source" }, { status: 400 });
    }

    try {
        const upstream = await fetch(target, {
            cache: "force-cache",
            next: { revalidate: 300 },
        });

        if (!upstream.ok) {
            return NextResponse.json(
                { error: upstream.status === 404 ? "Avatar not found" : "Failed to load avatar" },
                { status: upstream.status === 404 ? 404 : 502 },
            );
        }

        const headers = new Headers();
        const contentType = upstream.headers.get("content-type");
        if (contentType) {
            headers.set("Content-Type", contentType);
        }
        const etag = upstream.headers.get("etag");
        if (etag) {
            headers.set("ETag", etag);
        }
        headers.set("Cache-Control", "private, max-age=300");

        return new NextResponse(upstream.body, {
            status: 200,
            headers,
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Failed to proxy avatar" },
            { status: 500 },
        );
    }
}
