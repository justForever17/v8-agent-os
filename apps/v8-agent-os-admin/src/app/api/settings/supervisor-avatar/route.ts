import { NextRequest, NextResponse } from "next/server";

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
        defaultReplyModel?: string | null;
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
    return NextResponse.json({ avatar: data.profile?.avatar || "" });
}

export async function POST(req: NextRequest) {
    const unauthorized = await requireAdminIdentity();
    if (unauthorized) return unauthorized;

    const { avatar } = await req.json();
    if (avatar === undefined) {
        return NextResponse.json({ error: "Avatar URL is required" }, { status: 400 });
    }

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
                bindings: current.data.bindings || {},
                profile: {
                    ...(current.data.profile || {}),
                    avatar: String(avatar || ""),
                },
            },
        }),
    });
    if (!response.ok) {
        return NextResponse.json(data, { status: response.status });
    }

    return NextResponse.json({ avatar: String(avatar || "") });
}
