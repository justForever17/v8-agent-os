import { NextRequest, NextResponse } from "next/server";
import { getClientProxyConfig, resolveLocalAdminRootUrl } from "@/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    if (req.nextUrl.searchParams.get("open") === "admin") return NextResponse.redirect(`${resolveLocalAdminRootUrl()}/admin`);
    const { clientApiBaseUrl, internalSecret } = await getClientProxyConfig();
    if (!internalSecret) return NextResponse.json({ error: "Local Engine is not configured" }, { status: 503 });
    try {
        const response = await fetch(`${clientApiBaseUrl}/connection`, {
            headers: { "x-v8-agent-os-secret": internalSecret }, cache: "no-store", redirect: "error",
        });
        return NextResponse.json(await response.json(), { status: response.status });
    } catch {
        return NextResponse.json({ error: "Local Engine is unavailable" }, { status: 503 });
    }
}
