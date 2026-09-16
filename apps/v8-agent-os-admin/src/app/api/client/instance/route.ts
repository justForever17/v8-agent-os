import { fetchEngineIdentity } from "@/lib/server/engine-identity";
import { identityErrorResponse } from "@/lib/server/identity-route";
export async function GET() {
    try { return await fetchEngineIdentity("/instance"); }
    catch (error) { return identityErrorResponse(error); }
}
