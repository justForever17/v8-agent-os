import { NextRequest, NextResponse } from "next/server";
import { verifyServiceAuth } from "@/lib/service-auth";
import { fetchEngineIdentity } from "@/lib/server/engine-identity";
import { identityErrorResponse } from "@/lib/server/identity-route";
export async function POST(req: NextRequest) {
    try {
        if (!await verifyServiceAuth(req)) return NextResponse.json({ error: "local_engine_session_required" }, { status: 401 });
        return await fetchEngineIdentity("/local-session", { method: "POST", body: JSON.stringify(await req.json()) });
    } catch (error) { return identityErrorResponse(error); }
}
