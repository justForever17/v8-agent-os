import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveAdminApiBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
    try {
        const session = await auth();
        const userIdentifier = String(session?.user?.email || session?.user?.login || "").trim();
        if (!userIdentifier) {
            return NextResponse.json({ error: "本机会话不可用，请确认本机 Admin 已启动。" }, { status: 401 });
        }
        const internalSecret = await resolveInternalSecret();
        if (!internalSecret) {
            return NextResponse.json({ error: "Admin bridge secret is not configured" }, { status: 500 });
        }
        const contentType = String(req.headers.get("content-type") || "").split(";", 1)[0].trim().toLowerCase();
        if (!req.body || !contentType) {
            return NextResponse.json({ error: "没有找到上传文件" }, { status: 400 });
        }
        const upstream = await fetch(`${await resolveAdminApiBaseUrl()}/client/user-background-upload`, {
            method: "POST",
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": userIdentifier,
                "x-v8-upload-mode": "raw",
                "content-type": contentType,
                ...(req.headers.get("content-length") ? { "content-length": String(req.headers.get("content-length")) } : {}),
            },
            body: req.body,
            cache: "no-store",
            duplex: "half",
        } as RequestInit & { duplex: "half" });
        const payload = await upstream.json().catch(() => ({}));
        return NextResponse.json(payload, { status: upstream.status });
    } catch (error) {
        console.error("[user-background-upload] Upload failed:", error);
        return NextResponse.json({ error: "背景图上传失败" }, { status: 500 });
    }
}
