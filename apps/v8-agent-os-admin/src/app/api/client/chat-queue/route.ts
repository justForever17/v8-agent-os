import { NextRequest, NextResponse } from "next/server";
import { fetchClientEngine } from "@/lib/server/client-proxy";
import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
export const dynamic = "force-dynamic";
export async function GET(req: NextRequest) {
    if (!await resolveClientUserEmail(req)) return unauthorizedClientJson();
    const query = new URLSearchParams({ session_id: req.nextUrl.searchParams.get("session_id") || "" });
    const cursor = req.nextUrl.searchParams.get("after_ordinal");
    if (cursor !== null) query.set("after_ordinal", cursor);
    const response = await fetchClientEngine(req, `/chat/queued-messages?${query}`, { cache: "no-store" });
    return NextResponse.json(await response.json(), { status: response.status });
}
