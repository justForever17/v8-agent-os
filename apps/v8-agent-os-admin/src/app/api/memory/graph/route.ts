import { NextRequest, NextResponse } from "next/server";
import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    try {
        const ENGINE_URL = resolveEngineOrigin();
        const { searchParams } = new URL(req.url);
        if (searchParams.get("overview") === "1") {
            const query = new URLSearchParams();
            for (const key of ["offset", "limit", "clusterId", "entity", "relationOffset", "workspaceQuery"]) {
                const value = searchParams.get(key);
                if (value !== null) query.set(key, value);
            }
            const response = await fetch(`${ENGINE_URL}/v1/memory/graph/overview?${query}`, { signal: req.signal, cache: "no-store" });
            return NextResponse.json(await response.json(), { status: response.status });
        }
        const entity = searchParams.get("entity");
        const keyword = searchParams.get("keyword");
        const workspaceKey = searchParams.get("workspaceKey");
        const workspaces = searchParams.get("workspaces");
        const limit = searchParams.get("limit") || "100";
        const workspaceQuery = workspaceKey ? `&workspaceKey=${encodeURIComponent(workspaceKey)}` : "";

        const response = workspaces === "1"
            ? await fetch(`${ENGINE_URL}/v1/memory/graph/workspaces`)
            : entity
            ? await fetch(`${ENGINE_URL}/v1/memory/graph/entity/${encodeURIComponent(entity)}?workspaceKey=${encodeURIComponent(workspaceKey || "")}`)
            : keyword
                ? await fetch(`${ENGINE_URL}/v1/memory/graph/search?keyword=${encodeURIComponent(keyword)}&limit=${limit}${workspaceQuery}`)
                : await fetch(`${ENGINE_URL}/v1/memory/graph/all?limit=${limit}${workspaceQuery}`);
        return NextResponse.json(await response.json(), { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    try {
        const ENGINE_URL = resolveEngineOrigin();
        const body = await req.json();
        const action = body?.action;

        let target = "";
        if (action === "add_entity") {
            target = `${ENGINE_URL}/v1/memory/graph/entity`;
        } else if (action === "add_relation") {
            target = `${ENGINE_URL}/v1/memory/graph/relation`;
        } else {
            return NextResponse.json({ error: "Unsupported graph action" }, { status: 400 });
        }

        const response = await fetch(target, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}

export async function DELETE(req: NextRequest) {
    const unauthorized = await requireAdminIdentity(req);
    if (unauthorized) return unauthorized;
    try {
        const ENGINE_URL = resolveEngineOrigin();
        const body = await req.json();
        const action = body?.action;

        let target = "";
        if (action === "delete_entity") {
            target = `${ENGINE_URL}/v1/memory/graph/entity`;
        } else if (action === "delete_relation") {
            target = `${ENGINE_URL}/v1/memory/graph/relation`;
        } else {
            return NextResponse.json({ error: "Unsupported graph action" }, { status: 400 });
        }

        const response = await fetch(target, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        return NextResponse.json(data, { status: response.status });
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}
