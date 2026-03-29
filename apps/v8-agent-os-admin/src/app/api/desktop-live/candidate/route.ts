import { NextRequest, NextResponse } from "next/server";

import { ensureDesktopLiveBridge } from "@/lib/server/desktop-live-bridge";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";
import { resolveInternalSecret } from "@/lib/server/runtime-config";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const payload = (await req.json().catch(() => ({}))) as {
            sessionId?: string;
            candidate?: Record<string, unknown> | null;
        };
        if (!payload.sessionId) {
            return NextResponse.json({ error: "Missing sessionId" }, { status: 400 });
        }

        const bridgeBaseUrl = await ensureDesktopLiveBridge();
        const response = await fetch(`${bridgeBaseUrl}/desktop-live/candidate`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-secret": resolveInternalSecret(),
            },
            body: JSON.stringify({
                session_id: payload.sessionId,
                candidate: payload.candidate ?? null,
            }),
            cache: "no-store",
        });
        const body = await response.json().catch(() => ({}));
        return NextResponse.json(body, { status: response.status });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "提交桌面直播 candidate 失败" },
            { status: 500 },
        );
    }
}
