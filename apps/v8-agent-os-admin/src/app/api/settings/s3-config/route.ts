import { NextResponse } from "next/server";

import { proxyEngineJson, requireAdminIdentity } from "@/lib/server/engine-proxy";

type SystemBaseRegistryData = {
    bridge?: Record<string, unknown>;
    webFetch?: Record<string, unknown>;
    desktopTools?: Record<string, unknown>;
    desktopLive?: Record<string, unknown>;
    s3?: Record<string, unknown>;
};

async function readSystemBaseDomain() {
    const { response, data } = await proxyEngineJson("/config-registry/system-base");
    return {
        response,
        data: ((data as { data?: SystemBaseRegistryData })?.data || {}) as SystemBaseRegistryData,
    };
}

export async function GET() {
    const unauthorized = await requireAdminIdentity();
    if (unauthorized) return unauthorized;

    const { response, data } = await readSystemBaseDomain();
    if (!response.ok) {
        return NextResponse.json(data, { status: response.status });
    }
    return NextResponse.json({
        value: data.s3 || {
            endpoint: "",
            region: "",
            bucket: "",
            accessKeyId: "",
            secretAccessKey: "",
        },
    });
}

export async function POST(request: Request) {
    const unauthorized = await requireAdminIdentity();
    if (unauthorized) return unauthorized;

    const { value } = await request.json();
    if (!value) {
        return NextResponse.json({ error: "Value is required" }, { status: 400 });
    }

    const current = await readSystemBaseDomain();
    if (!current.response.ok) {
        return NextResponse.json(current.data, { status: current.response.status });
    }

    const { response, data } = await proxyEngineJson("/config-registry/system-base", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            data: {
                bridge: current.data.bridge || {},
                webFetch: current.data.webFetch || {},
                desktopTools: current.data.desktopTools || {},
                desktopLive: current.data.desktopLive || {},
                s3: value,
            },
        }),
    });
    if (!response.ok) {
        return NextResponse.json(data, { status: response.status });
    }

    return NextResponse.json({ success: true, value });
}
