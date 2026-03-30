import { NextRequest, NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

type SupervisorRegistryData = {
    bindings?: {
        supervisorModel?: string | null;
    };
};

export async function GET() {
    const unauthorized = await requireAdminIdentity();
    if (unauthorized) return unauthorized;

    const { response, data } = await proxyEngineJson("/config-registry/supervisor");
    if (!response.ok) {
        return NextResponse.json(data, { status: response.status });
    }
    const supervisorData = ((data as { data?: SupervisorRegistryData })?.data || {}) as SupervisorRegistryData;
    return NextResponse.json({
        modelId: supervisorData.bindings?.supervisorModel || null,
        source: "config.json#models.roles.supervisor",
    });
}

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity();
    if (unauthorized) return unauthorized;

    const { modelId } = await req.json();
    const { response, data } = await proxyEngineJson("/config-registry/supervisor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            data: {
                bindings: {
                    supervisorModel: modelId ?? null,
                },
            },
        }),
    });
    if (!response.ok) {
        return NextResponse.json(data, { status: response.status });
    }

    const supervisorData = ((data as { data?: SupervisorRegistryData })?.data || {}) as SupervisorRegistryData;
    return NextResponse.json({
        key: "SUPERVISOR_MODEL_ID",
        modelId: supervisorData.bindings?.supervisorModel || null,
        value: supervisorData.bindings?.supervisorModel || null,
        source: "config.json#models.roles.supervisor",
    });
}
