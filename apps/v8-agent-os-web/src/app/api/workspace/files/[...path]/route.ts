import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";

import { getAdminProxyConfig } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
    const resolvedParams = await params;
    const session = await auth();
    
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { adminApiBaseUrl, internalSecret } = await getAdminProxyConfig();

    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }

    try {
        const pathSegments = resolvedParams.path;
        // e.g. /api/workspace/files/uploads/file.png
        const targetUrl = `${adminApiBaseUrl}/workspace/files/${pathSegments.join('/')}`;

        const res = await fetch(targetUrl, {
            method: "GET",
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email
            }
        });

        if (!res.ok) {
            return new NextResponse(await res.arrayBuffer(), { status: res.status });
        }

        const responseHeaders = new Headers();
        const ct = res.headers.get("Content-Type");
        if (ct) responseHeaders.set("Content-Type", ct);
        const cl = res.headers.get("Content-Length");
        if (cl) responseHeaders.set("Content-Length", cl);
        const cd = res.headers.get("Content-Disposition");
        if (cd) responseHeaders.set("Content-Disposition", cd);

        // Forward the response stream directly to the browser
        return new NextResponse(res.body, {
            status: res.status,
            headers: responseHeaders
        });

    } catch (error) {
        console.error("Workspace files Proxy Error:", error);
        return NextResponse.json({ error: "Service Unavailable" }, { status: 502 });
    }
}
