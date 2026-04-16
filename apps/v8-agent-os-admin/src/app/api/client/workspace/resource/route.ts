import { NextRequest, NextResponse } from "next/server";

import { fetchClientAdmin } from "@/lib/server/client-proxy";
import { fetchSignedClientAdminPath, verifySignedClientSurfaceRequest } from "@/lib/server/client-surface-resource";

export const runtime = "nodejs";

function buildTargetPath(req: NextRequest) {
    const url = new URL(req.url);
    const query = url.searchParams.toString();
    return `/workspace/resource${query ? `?${query}` : ""}`;
}

export async function GET(req: NextRequest) {
    try {
        const targetPath = buildTargetPath(req);
        const response = verifySignedClientSurfaceRequest(req)
            ? await fetchSignedClientAdminPath(targetPath, {
                method: "GET",
                headers: req.headers.get("range") ? { Range: String(req.headers.get("range")) } : undefined,
            })
            : await fetchClientAdmin(req, targetPath, {
                method: "GET",
                headers: req.headers.get("range") ? { Range: String(req.headers.get("range")) } : undefined,
            });

        if (!response.ok || !response.body) {
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
        if (error instanceof Error && error.message === "Unauthorized") {
            return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
        }
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Workspace 资源代理失败" },
            { status: 502 },
        );
    }
}
