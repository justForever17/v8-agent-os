import { NextRequest, NextResponse } from "next/server";

import { resolveEngineOrigin } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

export const runtime = "nodejs";

export async function GET(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const { path } = await context.params;
        const response = await fetch(`${resolveEngineOrigin()}/workspace/${path.join("/")}`, {
            headers: {
                "x-v8-agent-os-user-email": userEmail,
            },
            cache: "no-store",
        });

        if (!response.ok) {
            return new NextResponse(await response.arrayBuffer(), { status: response.status });
        }

        const headers = new Headers();
        const contentType = response.headers.get("Content-Type");
        if (contentType) headers.set("Content-Type", contentType);
        const contentLength = response.headers.get("Content-Length");
        if (contentLength) headers.set("Content-Length", contentLength);
        const contentDisposition = response.headers.get("Content-Disposition");
        if (contentDisposition) headers.set("Content-Disposition", contentDisposition);

        return new NextResponse(response.body, {
            status: response.status,
            headers,
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Workspace 文件代理失败" },
            { status: 500 },
        );
    }
}
