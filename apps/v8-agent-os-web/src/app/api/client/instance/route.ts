import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveClientApiBaseUrl } from "@/lib/server/runtime-config";

export async function GET() {
    if (!(await auth())?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    try {
        const response = await fetch(`${await resolveClientApiBaseUrl()}/instance`, { cache: "no-store" });
        const payload = await response.json();
        if (!response.ok || !payload.instanceId) throw new Error("Instance unavailable");
        return NextResponse.json({ instanceId: payload.instanceId });
    } catch { return NextResponse.json({ error: "Instance unavailable" }, { status: 503 }); }
}
