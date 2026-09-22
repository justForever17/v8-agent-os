import { NextRequest, NextResponse } from "next/server";

import { fetchClientAdmin } from "@admin/lib/server/client-proxy";
import { fetchEngineClientIdentity } from "@admin/lib/server/engine-identity";

export async function GET(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
    try {
        const { path } = await context.params;
        const targetPath = `/workspace/files/${path.join("/")}`;
        const response = req.nextUrl.searchParams.has("v8sig")
            ? await fetchEngineClientIdentity(targetPath + (req.nextUrl.search ? `?${req.nextUrl.searchParams.toString()}` : ""), { method: "GET" })
            : await fetchClientAdmin(req, targetPath, { method: "GET" });
        if (!response.ok || !response.body) {
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
        if (error instanceof Error && error.message === "Unauthorized") {
            return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
        }
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "Workspace 文件代理失败" },
            { status: 502 },
        );
    }
}
