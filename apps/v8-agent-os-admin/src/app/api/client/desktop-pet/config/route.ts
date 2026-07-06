import { NextRequest } from "next/server";

import { proxyClientEngineJson } from "@/lib/server/client-proxy";

export async function GET(req: NextRequest) {
    return proxyClientEngineJson(req, "/config-registry/desktop-pet", {
        cache: "no-store",
    });
}
