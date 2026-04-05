import { NextRequest } from "next/server";

import { proxyClientAdminJson } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest) {
    const target = req.nextUrl.search ? `/memory/artifacts${req.nextUrl.search}` : "/memory/artifacts";
    return proxyClientAdminJson(req, target, { method: "GET" });
}
