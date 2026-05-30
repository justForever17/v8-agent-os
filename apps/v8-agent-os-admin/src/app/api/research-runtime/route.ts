import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

function buildEnginePath(req: NextRequest) {
    const url = new URL(req.url);
    const view = url.searchParams.get("view") || "ledger";
    const params = new URLSearchParams();
    for (const key of ["scope", "limit", "query", "minConfidence", "includeArchived"]) {
        const value = url.searchParams.get(key);
        if (value) params.set(key, value);
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    if (view === "evidence") return `/research-runtime/evidence${suffix}`;
    if (view === "experience") return `/research-runtime/experience${suffix}`;
    if (view === "source-providers") return "/research-runtime/source-providers";
    return `/research-runtime/ledger${suffix}`;
}

export async function GET(req: NextRequest) {
    try {
        const unauthorized = await requireAdminIdentity(req);
        if (unauthorized) return unauthorized;
        const { response, data } = await proxyEngineJson(buildEnginePath(req));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("Failed to read research runtime ledger:", error);
        return NextResponse.json({ ok: false, error: "research_runtime_read_failed" }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    try {
        const unauthorized = await requireAdminIdentity(req);
        if (unauthorized) return unauthorized;
        const body = await req.json().catch(() => ({}));
        const action = typeof body?.action === "string" ? body.action : "promote";
        const path = action === "archive"
            ? "/research-runtime/experience/archive"
            : action === "restore"
                ? "/research-runtime/experience/restore"
                : "/research-runtime/experience/promote";
        const { response, data } = await proxyEngineJson(path, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
        });
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("Failed to promote research experience:", error);
        return NextResponse.json({ ok: false, error: "research_runtime_promote_failed" }, { status: 500 });
    }
}

export async function DELETE(req: NextRequest) {
    try {
        const unauthorized = await requireAdminIdentity(req);
        if (unauthorized) return unauthorized;
        const url = new URL(req.url);
        const experiencePackId = url.searchParams.get("experiencePackId") || "";
        const confirm = url.searchParams.get("confirm") === "true";
        const { response, data } = await proxyEngineJson(
            `/research-runtime/experience/${encodeURIComponent(experiencePackId)}?confirm=${confirm ? "true" : "false"}`,
            { method: "DELETE" },
        );
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        console.error("Failed to delete research experience:", error);
        return NextResponse.json({ ok: false, error: "research_runtime_delete_failed" }, { status: 500 });
    }
}
