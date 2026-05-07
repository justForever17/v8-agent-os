import { NextRequest, NextResponse } from "next/server";

import { requireClientContext } from "@/lib/server/client-proxy";
import {
    buildAdminLinkManifest,
    resolveAdminApiBaseUrl,
    resolveDesktopLiveBridgeBaseUrl,
    resolveEngineBaseUrl,
} from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    const context = await requireClientContext(req);
    if (context instanceof NextResponse) {
        return context;
    }

    const requestOrigin = new URL(req.url).origin;
    const linkManifest = buildAdminLinkManifest(requestOrigin);
    return NextResponse.json({
        connection: {
            adminBaseUrl: requestOrigin,
            adminApiBaseUrl: `${requestOrigin}/api`,
            configuredAdminApiBaseUrl: resolveAdminApiBaseUrl(),
            bridgeMode: "admin_only",
            reachable: true,
            engineBaseUrl: resolveEngineBaseUrl(),
            desktopLiveBridgeBaseUrl: resolveDesktopLiveBridgeBaseUrl(),
            transportKind: linkManifest.transportKind,
            transportProfileId: linkManifest.activeProfileId,
            linkManifest,
            vpnDiagnostics: linkManifest.diagnostics,
        },
        linkManifest,
        user: {
            id: context.user.id,
            login: context.user.login,
            email: context.user.email || context.user.login,
            name: context.user.name || "",
            role: context.user.role,
            image: context.user.image || "",
            mustChangePassword: Boolean(context.user.mustChangePassword),
        },
    });
}
