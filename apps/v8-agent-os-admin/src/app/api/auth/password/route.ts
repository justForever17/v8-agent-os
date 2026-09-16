import { NextRequest } from "next/server";
import { requireAdminIdentity } from "@/lib/server/engine-proxy";
import { fetchEngineIdentity } from "@/lib/server/engine-identity";
import { identityErrorResponse } from "@/lib/server/identity-route";
export async function POST(req: NextRequest) {
    try { const denied = await requireAdminIdentity(req); if (denied) return denied; return await fetchEngineIdentity("/password", { method: "POST", body: JSON.stringify(await req.json()) }); }
    catch (error) { return identityErrorResponse(error); }
}
