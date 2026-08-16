import { NextRequest, NextResponse } from "next/server";

import { hasOwner } from "@/lib/users";
import { isAdminStorageUnavailableError } from "@/lib/storage";
import { readOrCreateInstanceIdentity } from "@/lib/server/instance-identity";
import { buildClientLinkManifest, resolveRequestOrigin } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    try {
        const initialized = hasOwner();
        const identity = readOrCreateInstanceIdentity();
        const manifest = buildClientLinkManifest(resolveRequestOrigin(req));
        return NextResponse.json({
            ok: true,
            kind: "v8_instance_manifest",
            version: "1",
            instanceId: identity.instanceId,
            createdAt: identity.createdAt,
            initialized,
            ownerMode: "single_owner",
            clientGateway: "admin_bff",
            admin: manifest.admin,
            warnings: manifest.warnings,
            capabilities: {
                pairing: true,
                localTrustedSession: true,
                localTrustedSurfaces: ["web", "cyber"],
                passwordLoginFallback: true,
                publicRegistration: false,
            },
        });
    } catch (error) {
        console.error("Instance Owner state unavailable:", error);
        if (isAdminStorageUnavailableError(error)) {
            return NextResponse.json({
                ok: false,
                error: error.code,
            }, { status: 503 });
        }
        throw error;
    }
}
