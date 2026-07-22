import { NextRequest, NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl, resolveInternalSecret } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

function asObject(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function engineHeaders(json: boolean): HeadersInit {
    const internalSecret = resolveInternalSecret();
    if (!internalSecret) {
        throw new Error("V8OS internal service credential is unavailable.");
    }
    return {
        "x-v8-agent-os-secret": internalSecret,
        ...(json ? { "Content-Type": "application/json" } : {}),
    };
}

async function proxyEngineResponse(response: Response) {
    const payload = asObject(await response.json().catch(() => ({})));
    if (Object.keys(payload).length === 0) {
        return NextResponse.json(
            { ok: false, error: `Engine voice manager returned HTTP ${response.status}.` },
            { status: response.ok ? 502 : response.status },
        );
    }
    return NextResponse.json(payload, { status: response.status });
}

export async function POST(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ ok: false, error: "Unauthorized" }, { status: 401 });
    }

    try {
        const contentType = req.headers.get("content-type") || "";
        const isMultipart = contentType.includes("multipart/form-data");
        const body = isMultipart ? await req.formData() : JSON.stringify(asObject(await req.json().catch(() => ({}))));
        const response = await fetch(`${ENGINE_URL}/audio/model-ref-voices`, {
            method: "POST",
            headers: engineHeaders(!isMultipart),
            body,
            cache: "no-store",
        });
        return proxyEngineResponse(response);
    } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Unknown error";
        return NextResponse.json({ ok: false, error: message }, { status: 502 });
    }
}
