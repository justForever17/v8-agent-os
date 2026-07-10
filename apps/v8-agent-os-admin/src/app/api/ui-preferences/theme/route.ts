import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

type UiTheme = "light" | "dark" | "system";

function normalizeTheme(value: unknown): UiTheme {
    const normalized = String(value || "").trim().toLowerCase();
    return normalized === "light" || normalized === "dark" || normalized === "system"
        ? normalized
        : "system";
}

function themeResponse(payload: unknown) {
    const record = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
    const data = record.data && typeof record.data === "object" ? record.data as Record<string, unknown> : record;
    return { theme: normalizeTheme(data.theme) };
}

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    try {
        const { response, data } = await proxyEngineJson("/config-registry/ui");
        return NextResponse.json(response.ok ? themeResponse(data) : data, { status: response.status });
    } catch (error) {
        console.error("[Admin UI Theme] Failed to load canonical theme:", error);
        return NextResponse.json({ error: "Theme service unavailable" }, { status: 503 });
    }
}

export async function PUT(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;

    const body = await req.json().catch(() => ({}));
    const rawTheme = String(body?.theme || "").trim().toLowerCase();
    if (!new Set(["light", "dark", "system"]).has(rawTheme)) {
        return NextResponse.json({ error: "Invalid theme" }, { status: 400 });
    }

    try {
        const { response, data } = await proxyEngineJson("/config-registry/ui", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ data: { theme: rawTheme } }),
        });
        return NextResponse.json(response.ok ? themeResponse(data) : data, { status: response.status });
    } catch (error) {
        console.error("[Admin UI Theme] Failed to save canonical theme:", error);
        return NextResponse.json({ error: "Theme service unavailable" }, { status: 503 });
    }
}
