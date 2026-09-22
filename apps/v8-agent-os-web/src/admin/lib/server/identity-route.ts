import { NextResponse } from "next/server";
import { EngineIdentityError } from "@admin/lib/server/engine-identity";

export function identityErrorResponse(error: unknown) {
    const code = error instanceof EngineIdentityError ? error.code : "engine_identity_unavailable";
    const status = error instanceof EngineIdentityError ? error.status : 503;
    return NextResponse.json({ error: code, code }, { status });
}
