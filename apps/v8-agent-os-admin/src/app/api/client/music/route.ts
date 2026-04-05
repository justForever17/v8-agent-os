import { NextRequest } from "next/server";

import { proxyClientAdminJson } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest) {
    return proxyClientAdminJson(req, "/music", { method: "GET" });
}
