import { NextRequest, NextResponse } from "next/server";

import { resolveAdminApiBaseUrl, resolveAdminPublicBaseUrl } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    const publicAdminBase = resolveAdminPublicBaseUrl();
    const requestAdminBase = `${req.nextUrl.protocol}//${req.nextUrl.host}`;
    const adminBaseUrl = (publicAdminBase || requestAdminBase).replace(/\/$/, "");
    return NextResponse.json({
        adminBaseUrl,
        adminApiBaseUrl: resolveAdminApiBaseUrl(),
        bridgeMode: "admin_only",
        version: process.env.npm_package_version || process.env.NEXT_PUBLIC_APP_VERSION || "unknown",
        reachable: true,
    });
}
