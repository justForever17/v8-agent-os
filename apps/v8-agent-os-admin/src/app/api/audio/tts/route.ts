import { NextRequest, NextResponse } from "next/server";

import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const body = await req.json();
        const response = await fetch(`${resolveEngineBaseUrl()}/audio/tts/stream`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-user-email": userEmail,
            },
            body: JSON.stringify(body),
            cache: "no-store",
        });

        if (!response.ok || !response.body) {
            const detail = await response.text().catch(() => "");
            return new NextResponse(detail || "TTS request failed", { status: response.status || 500 });
        }

        return new NextResponse(response.body, {
            status: response.status,
            headers: {
                "Content-Type": response.headers.get("Content-Type") || "audio/mpeg",
                "Cache-Control": "no-store",
            },
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "TTS 代理失败" },
            { status: 500 },
        );
    }
}
