import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";

import { getAdminProxyConfig } from "@/lib/server/runtime-config";

function toWebResourceUrl(value: unknown) {
    const url = String(value || "").trim();
    const adminPrefix = "/api/client/workspace/resource";
    return url.startsWith(adminPrefix)
        ? `/api/workspace/resource${url.slice(adminPrefix.length)}`
        : url;
}

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();

    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        if (!req.body) {
            return NextResponse.json({ error: "Missing upload body" }, { status: 400 });
        }
        const headers = new Headers({
            "x-v8-agent-os-secret": internalSecret,
            "x-v8-agent-os-user-email": session.user.email,
            "x-v8-upload-default-source-kind": "web_upload",
        });
        const contentType = req.headers.get("content-type");
        const contentLength = req.headers.get("content-length");
        if (contentType) headers.set("content-type", contentType);
        if (contentLength) headers.set("content-length", contentLength);
        const res = await fetch(`${adminApiBaseUrl}/client/upload`, {
            method: "POST",
            headers,
            body: req.body,
            signal: req.signal,
            duplex: "half",
        } as RequestInit & { duplex: "half" });

        if (!res.ok) {
            const errorText = await res.text();
            console.error("Upload proxy failed:", errorText);
            return NextResponse.json({ error: "Upload failed" }, { status: res.status });
        }

        const data = await res.json();
        const normalizedUrl = toWebResourceUrl(data?.url || data?.publicUrl || data?.workspacePath || data?.path);
        return NextResponse.json({
            ...data,
            url: normalizedUrl || data?.url,
            publicUrl: toWebResourceUrl(data?.publicUrl || data?.url || normalizedUrl) || undefined,
            previewUrl: toWebResourceUrl(data?.previewUrl || normalizedUrl) || undefined,
        });

    } catch (error) {
        console.error("Upload Proxy Error:", error);
        return NextResponse.json({ error: "Upload Service Unavailable" }, { status: 502 });
    }
}
