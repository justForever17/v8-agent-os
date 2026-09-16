import { NextRequest } from "next/server";
import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { fetchEngineIdentity } from "@/lib/server/engine-identity";
import { identityErrorResponse } from "@/lib/server/identity-route";
export async function GET(req: NextRequest) {
    try { const denied = await requireAdminIdentity(req); if (denied) return denied; return await fetchEngineIdentity("/profile"); }
    catch (error) { return identityErrorResponse(error); }
}
export async function PATCH(req: NextRequest) {
    try { const denied = await requireAdminIdentity(req); if (denied) return denied; return await fetchEngineIdentity("/profile", { method: "PATCH", body: JSON.stringify(await req.json()) }); }
    catch (error) { return identityErrorResponse(error); }
}
