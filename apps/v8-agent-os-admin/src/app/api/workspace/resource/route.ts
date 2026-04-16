import { NextRequest, NextResponse } from "next/server";

import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

export const runtime = "nodejs";

function buildTargetUrl(req: NextRequest) {
    const url = new URL(req.url);
    const query = url.searchParams.toString();
    return `${resolveEngineBaseUrl()}/workspace/resource${query ? `?${query}` : ""}`;
}

export async function GET(req: NextRequest) {
    const userEmail = await resolveAuthorizedUserEmail(req);
    if (!userEmail) {
        return unauthorizedJson();
    }

    try {
        const response = await fetch(buildTargetUrl(req), {
            headers: {
                "x-v8-agent-os-user-email": userEmail,
                ...(req.headers.get("range") ? { Range: String(req.headers.get("range")) } : {}),
            },
            cache: "no-store",
        });

        if (!response.ok) {
            return new NextResponse(await response.arrayBuffer(), { status: response.status });
        }

        const headers = new Headers();
        for (const name of [
            "Content-Type",
            "Content-Length",
            "Content-Disposition",
            "Accept-Ranges",
            "Content-Range",
            "Cache-Control",
            "X-V8-Workspace-Relative-Path",
            "X-V8-Path-Plane",
            "X-V8-Workspace-Id",
            "X-V8-Project-Id",
        ]) {
            const value = response.headers.get(name);
            if (value) {
                headers.set(name, value);
            }
        }

        return new NextResponse(response.body, {
            status: response.status,
            headers,
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Workspace 资源代理失败" },
            { status: 500 },
        );
    }
}
