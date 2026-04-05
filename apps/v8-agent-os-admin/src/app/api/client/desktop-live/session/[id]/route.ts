import { NextRequest, NextResponse } from "next/server";

import { fetchClientAdmin, forbidIfClientAdmin, requireClientContext } from "@/lib/server/client-proxy";

export const runtime = "nodejs";

async function release(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    const client = await requireClientContext(req);
    if (client instanceof NextResponse) {
        return client;
    }
    if (forbidIfClientAdmin(client.user)) {
        return NextResponse.json({ error: "Admin 账号不提供桌面直播观看入口" }, { status: 403 });
    }
    try {
        const { id } = await context.params;
        const response = await fetchClientAdmin(req, `/desktop-live/session/${encodeURIComponent(id)}`, {
            method: "DELETE",
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, { status: response.status });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "释放桌面直播会话失败" },
            { status: error instanceof Error && error.message === "Unauthorized" ? 401 : 502 },
        );
    }
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    return release(req, context);
}

export async function POST(req: NextRequest, context: { params: Promise<{ id: string }> }) {
    return release(req, context);
}
