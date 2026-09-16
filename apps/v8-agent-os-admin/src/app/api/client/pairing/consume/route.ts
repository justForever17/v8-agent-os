import { NextRequest } from "next/server";
import { fetchEngineClientIdentity } from "@/lib/server/engine-identity";
import { identityErrorResponse } from "@/lib/server/identity-route";
export async function POST(req: NextRequest) {
    try { return await fetchEngineClientIdentity("/pairing/consume", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(await req.json()) }); }
    catch (error) { return identityErrorResponse(error); }
}
