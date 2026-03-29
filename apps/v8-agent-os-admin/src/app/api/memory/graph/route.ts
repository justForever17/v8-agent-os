import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineOrigin();

export async function GET(req: Request) {
    try {
        const { searchParams } = new URL(req.url);
        const entity = searchParams.get("entity");
        const keyword = searchParams.get("keyword");
        const limit = searchParams.get("limit") || "100";

        const response = entity
            ? await fetch(`${ENGINE_URL}/v1/memory/graph/entity/${encodeURIComponent(entity)}`)
            : keyword
                ? await fetch(`${ENGINE_URL}/v1/memory/graph/search?keyword=${encodeURIComponent(keyword)}&limit=${limit}`)
                : await fetch(`${ENGINE_URL}/v1/memory/graph/all?limit=${limit}`);
        if (!response.ok) throw new Error(`Failed: ${response.status}`);
        return NextResponse.json(await response.json());
    } catch (error) {
        return NextResponse.json({ error: String(error) }, { status: 500 });
    }
}

export async function POST(req: Request) {
    try {
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

export async function DELETE(req: Request) {
    try {
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
