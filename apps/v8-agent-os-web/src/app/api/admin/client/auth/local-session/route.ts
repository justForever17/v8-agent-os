import { NextRequest, NextResponse } from "next/server";
import { verifyServiceAuth } from "@admin/lib/service-auth";
import { fetchEngineIdentity } from "@admin/lib/server/engine-identity";
import { identityErrorResponse } from "@admin/lib/server/identity-route";
export async function POST(req: NextRequest) {
    try {
        if (!await verifyServiceAuth(req)) return NextResponse.json({ error: "local_engine_session_required" }, { status: 401 });
        return await fetchEngineIdentity("/local-session", { method: "POST", body: JSON.stringify(await req.json()) });
    } catch (error) { return identityErrorResponse(error); }
}
