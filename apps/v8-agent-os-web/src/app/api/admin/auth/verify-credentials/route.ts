import { NextRequest, NextResponse } from "next/server";
import { timingSafeEqual } from "node:crypto";
import { resolveInternalSecret } from "@admin/lib/server/runtime-config";
import { fetchEngineIdentity } from "@admin/lib/server/engine-identity";
import { identityErrorResponse } from "@admin/lib/server/identity-route";
export async function POST(req: NextRequest) {
    try {
        const expected = resolveInternalSecret(), provided = req.headers.get("x-v8-agent-os-secret") || "";
        if (!expected || !provided || Buffer.byteLength(expected) !== Buffer.byteLength(provided) || !timingSafeEqual(Buffer.from(expected), Buffer.from(provided))) return NextResponse.json({ error: "Unauthorized Service Call" }, { status: 401 });
        const body = await req.json();
        return await fetchEngineIdentity("/verify-credentials", { method: "POST", body: JSON.stringify({ login: body.login || body.email, password: body.password }) });
    } catch (error) { return identityErrorResponse(error); }
}
