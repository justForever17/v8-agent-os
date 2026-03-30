import { NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

type SupervisorRegistryData = {
    systemPrompt?: string;
    allowedTools?: string[] | null;
    profile?: {
        name?: string;
        roleLabel?: string;
        avatar?: string;
    };
    bindings?: {
        supervisorModel?: string | null;
    };
};

async function readSupervisorDomain() {
    const { response, data } = await proxyEngineJson("/config-registry/supervisor");
    return {
        response,
        data: ((data as { data?: SupervisorRegistryData })?.data || {}) as SupervisorRegistryData,
    };
}

export async function GET() {
    const unauthorized = await requireAdminIdentity();
    if (unauthorized) return unauthorized;

    const { response, data } = await readSupervisorDomain();
    if (!response.ok) {
        return NextResponse.json(data, { status: response.status });
    }
    return NextResponse.json({ value: data.profile?.roleLabel || "主理人" });
}

export async function POST(req: Request) {
    const unauthorized = await requireAdminIdentity();
    if (unauthorized) return unauthorized;

    const { value } = await req.json();
    const current = await readSupervisorDomain();
    if (!current.response.ok) {
        return NextResponse.json(current.data, { status: current.response.status });
    }

    const { response, data } = await proxyEngineJson("/config-registry/supervisor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            data: {
                systemPrompt: current.data.systemPrompt || "",
                allowedTools: current.data.allowedTools ?? null,
                bindings: {
                    supervisorModel: current.data.bindings?.supervisorModel || null,
                },
                profile: {
                    ...(current.data.profile || {}),
                    roleLabel: String(value || "").trim(),
                },
            },
        }),
    });
    if (!response.ok) {
        return NextResponse.json(data, { status: response.status });
    }

    return NextResponse.json({ key: "supervisor-role", value: String(value || "").trim() });
}
