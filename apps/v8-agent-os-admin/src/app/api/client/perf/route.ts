import { NextRequest, NextResponse } from "next/server";

import { resolveClientUserEmail, unauthorizedClientJson } from "@/lib/server/client-request-auth";
import { getAdminApiPerfSnapshot, recordPhonePerfMetric } from "@/lib/server/client-perf-metrics";

export async function GET(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }
    return NextResponse.json(getAdminApiPerfSnapshot(), {
        headers: {
            "Cache-Control": "no-store",
        },
    });
}

export async function POST(req: NextRequest) {
    const userEmail = await resolveClientUserEmail(req);
    if (!userEmail) {
        return unauthorizedClientJson();
    }

    const payload = await req.json().catch(() => ({}));
    const samples = Array.isArray(payload?.samples)
        ? payload.samples
        : Array.isArray(payload)
            ? payload
            : [payload];
    let accepted = 0;
    for (const sample of samples) {
        if (sample && typeof sample === "object" && !Array.isArray(sample)) {
            accepted += recordPhonePerfMetric(sample as Record<string, unknown>) ? 1 : 0;
        }
    }

    return NextResponse.json({ ok: true, accepted }, {
        headers: {
            "Cache-Control": "no-store",
        },
    });
}
