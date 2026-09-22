import { NextRequest, NextResponse } from "next/server";
import { requireClientContext } from "@admin/lib/server/client-proxy";
import { fetchEngineClientIdentity } from "@admin/lib/server/engine-identity";
import { resolveInternalSecret } from "@admin/lib/server/runtime-config";
import { identityErrorResponse } from "@admin/lib/server/identity-route";

export async function proxyClientMedia(req: NextRequest, path: string) {
    try {
        const context = await requireClientContext(req);
        if (context instanceof NextResponse) return context;
        const headers = new Headers();
        for (const name of ["content-type", "content-length", "x-v8-upload-mode", "x-v8-background-intent"]) {
            const value = req.headers.get(name); if (value) headers.set(name, value);
        }
        const bearer = req.headers.get("authorization");
        if (bearer) headers.set("authorization", bearer);
        else headers.set("x-v8-agent-os-secret", resolveInternalSecret());
        const init: RequestInit & { duplex: "half" } = { method: req.method, headers, body: req.body, duplex: "half" };
        return await fetchEngineClientIdentity(path, init);
    } catch (error) { return identityErrorResponse(error); }
}
