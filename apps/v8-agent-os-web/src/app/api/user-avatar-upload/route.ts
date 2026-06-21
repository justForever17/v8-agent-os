import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveAdminApiBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
    try {
        const session = await auth();
        const userIdentifier = String(session?.user?.email || session?.user?.login || "").trim();
        if (!userIdentifier) {
            return NextResponse.json({ error: "未登录" }, { status: 401 });
        }
        const internalSecret = await resolveInternalSecret();
        if (!internalSecret) {
            return NextResponse.json({ error: "Admin bridge secret is not configured" }, { status: 500 });
        }

        const formData = await req.formData();
        const file = formData.get("file");
        if (!(file instanceof File)) {
            return NextResponse.json({ error: "没有找到上传文件" }, { status: 400 });
        }

        const upstreamForm = new FormData();
        upstreamForm.append("file", file);
        const upstream = await fetch(`${await resolveAdminApiBaseUrl()}/avatar-upload`, {
            method: "POST",
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": userIdentifier,
            },
            body: upstreamForm,
            cache: "no-store",
        });
        const payload = await upstream.json().catch(() => ({}));
        if (!upstream.ok) {
            return NextResponse.json(payload, { status: upstream.status });
        }

        return NextResponse.json(payload, { status: upstream.status });
    } catch (error) {
        console.error("[user-avatar-upload] Upload failed:", error);
        return NextResponse.json({ error: "头像上传失败" }, { status: 500 });
    }
}
