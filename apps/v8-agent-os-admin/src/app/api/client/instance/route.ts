import { NextRequest, NextResponse } from "next/server";

import { hasOwner } from "@/lib/users";
import { readOrCreateInstanceIdentity } from "@/lib/server/instance-identity";
import { buildClientLinkManifest, resolveRequestOrigin } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    const identity = readOrCreateInstanceIdentity();
    const manifest = buildClientLinkManifest(resolveRequestOrigin(req));
    return NextResponse.json({
        ok: true,
        kind: "v8_instance_manifest",
        version: "1",
        instanceId: identity.instanceId,
        createdAt: identity.createdAt,
        initialized: hasOwner(),
        ownerMode: "single_owner",
        clientGateway: "admin_bff",
        admin: manifest.admin,
        warnings: manifest.warnings,
        capabilities: {
            pairing: true,
            passwordLoginFallback: true,
            publicRegistration: false,
        },
    });
}
