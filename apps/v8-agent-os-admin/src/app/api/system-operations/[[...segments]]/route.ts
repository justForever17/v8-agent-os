import { NextRequest, NextResponse } from "next/server";
import { resolveAdminIdentity, proxyEngineJson } from "@/lib/server/engine-proxy";
import { resolveInternalSecret } from "@/lib/server/runtime-config";
import { auth } from "@/lib/auth";

type Context = { params: Promise<{ segments?: string[] }> };

async function proxy(req: NextRequest, context: Context) {
    const owner = await resolveAdminIdentity(req);
    if (!owner) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    const path = (await context.params).segments || [];
    const settings = req.method === "GET" && path.join("/") === "settings";
    const credential = ["PUT", "DELETE"].includes(req.method) && path.length === 2 && path[0] === "credentials" && ["unlock", "run_privileged"].includes(path[1]);
    const setup = req.method === "POST" && path.length === 3 && path[0] === "components" && ["unlock", "privilege"].includes(path[1]) && ["install", "uninstall"].includes(path[2]);
    if (!settings && !credential && !setup) return NextResponse.json({ error: "Not found" }, { status: 404 });
    if (setup && (await auth())?.user?.role !== "ADMIN") return NextResponse.json({ error: "Administrator required" }, { status: 403 });
    try {
        const body = req.method === "PUT" ? await req.text() : undefined;
        if (body && body.length > 32768) return NextResponse.json({ error: "Invalid input" }, { status: 422 });
        const { response, data } = await proxyEngineJson(`/system-operations/${path.join("/")}`, {
            method: req.method, body,
            headers: { "Content-Type": "application/json", "x-v8-agent-os-user-email": owner, "x-v8-agent-os-secret": resolveInternalSecret(), ...(setup ? { "x-v8-admin-role": "ADMIN" } : {}) },
        });
        return NextResponse.json(data, { status: response.status });
    } catch {
        // Neither request bodies nor raw exception objects belong in logs here.
        return NextResponse.json({ error: "System operation configuration unavailable" }, { status: 503 });
    }
}

export const GET = proxy;
export const PUT = proxy;
export const DELETE = proxy;
export const POST = proxy;
