import { NextRequest } from "next/server";

import { fetchEngineIdentity } from "@admin/lib/server/engine-identity";
import { identityErrorResponse } from "@admin/lib/server/identity-route";

export async function POST(req: NextRequest) {
    try {
        return await fetchEngineIdentity("/bootstrap", {
            method: "POST",
            body: JSON.stringify(await req.json()),
        });
    } catch (error) {
        return identityErrorResponse(error);
    }
}
