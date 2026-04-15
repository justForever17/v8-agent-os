import { NextRequest, NextResponse } from "next/server";

import {
    isReachableClientSurfaceOrigin,
    resolveAdminApiBaseUrl,
    resolveAdminPublicBaseUrl,
    resolveReachableAdminPublicBaseUrl,
    resolveReachableClientSurfaceOriginFromRequest,
} from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    const publicAdminBase = resolveAdminPublicBaseUrl();
    const adminBaseUrl = (
        resolveReachableClientSurfaceOriginFromRequest(req.url)
        || resolveReachableAdminPublicBaseUrl()
    ).replace(/\/$/, "");
    return NextResponse.json({
        adminBaseUrl,
        configuredAdminBaseUrl: publicAdminBase,
        adminApiBaseUrl: resolveAdminApiBaseUrl(),
        bridgeMode: "admin_only",
        version: process.env.npm_package_version || process.env.NEXT_PUBLIC_APP_VERSION || "unknown",
        reachable: Boolean(adminBaseUrl) && isReachableClientSurfaceOrigin(adminBaseUrl),
    });
}
