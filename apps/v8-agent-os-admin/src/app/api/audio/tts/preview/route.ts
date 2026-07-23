import { NextRequest, NextResponse } from "next/server";

import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) return unauthorizedJson();

    try {
        const body = await req.json();
        const response = await fetch(`${resolveEngineBaseUrl()}/audio/tts/preview`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "x-v8-agent-os-user-email": userEmail,
            },
            body: JSON.stringify(body),
            cache: "no-store",
        });
        const contentType = response.headers.get("Content-Type") || "";
        if (contentType.includes("application/json")) {
            const payload = await response.json().catch(() => ({
                ok: false,
                error: `Engine TTS preview returned HTTP ${response.status}.`,
            }));
            return NextResponse.json(payload, { status: response.status });
        }
        if (!response.ok) {
            const detail = await response.text().catch(() => "");
            return NextResponse.json(
                { ok: false, error: detail || `Engine TTS preview returned HTTP ${response.status}.` },
                { status: response.status || 502 },
            );
        }
        return new NextResponse(await response.arrayBuffer(), {
            status: response.status,
            headers: {
                "Content-Type": contentType || "audio/mpeg",
                "Cache-Control": "no-store",
            },
        });
    } catch (error) {
        return NextResponse.json(
            { ok: false, error: error instanceof Error ? error.message : "TTS preview proxy failed." },
            { status: 502 },
        );
    }
}
