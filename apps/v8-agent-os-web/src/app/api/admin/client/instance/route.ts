import { fetchEngineIdentity } from "@admin/lib/server/engine-identity";
import { identityErrorResponse } from "@admin/lib/server/identity-route";
export async function GET() {
    try { return await fetchEngineIdentity("/instance"); }
    catch (error) { return identityErrorResponse(error); }
}
