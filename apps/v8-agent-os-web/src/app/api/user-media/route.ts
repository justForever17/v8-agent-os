import { NextRequest } from "next/server";

import { proxyUserMedia } from "@/lib/server/user-media-proxy";

export const runtime = "nodejs";

export async function GET(req: NextRequest) {
    return proxyUserMedia(req, { allowedKinds: ["background"] });
}
