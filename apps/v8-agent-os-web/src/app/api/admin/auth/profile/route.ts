import { NextRequest } from "next/server";
import { requireAdminIdentity } from "@admin/lib/server/engine-proxy";
import { fetchEngineIdentity } from "@admin/lib/server/engine-identity";
import { identityErrorResponse } from "@admin/lib/server/identity-route";
export async function GET(req: NextRequest) {
    try { const denied = await requireAdminIdentity(req); if (denied) return denied; return await fetchEngineIdentity("/profile"); }
    catch (error) { return identityErrorResponse(error); }
}
export async function PATCH(req: NextRequest) {
    try { const denied = await requireAdminIdentity(req); if (denied) return denied; return await fetchEngineIdentity("/profile", { method: "PATCH", body: JSON.stringify(await req.json()) }); }
    catch (error) { return identityErrorResponse(error); }
}
