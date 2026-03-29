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
        const formData = await req.formData();
        const response = await fetch(`${resolveEngineBaseUrl()}/audio/stt/transcribe`, {
            method: "POST",
            headers: {
                "x-v8-agent-os-user-email": userEmail,
            },
            body: formData,
            cache: "no-store",
        });

        const contentType = response.headers.get("Content-Type") || "application/json";
        if (contentType.includes("application/json")) {
            const payload = await response.json().catch(() => ({}));
            return NextResponse.json(payload, { status: response.status });
        }

        const text = await response.text().catch(() => "");
        return new NextResponse(text, {
            status: response.status,
            headers: { "Content-Type": contentType },
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "STT 代理失败" },
            { status: 500 },
        );
    }
}
