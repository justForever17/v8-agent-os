import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getClientProxyConfig } from "@/lib/server/runtime-config";

export async function GET() {
    const session = await auth();
    if (!session?.user?.email) {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const { clientApiBaseUrl, internalSecret } = await getClientProxyConfig();
    if (!internalSecret) {
        return NextResponse.json({ error: "Configuration Error" }, { status: 500 });
    }
    try {
        const response = await fetch(`${clientApiBaseUrl}/runtime/bridge`, {
            headers: {
                "x-v8-agent-os-secret": internalSecret,
                "x-v8-agent-os-user-email": session.user.email,
            },
            cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        return NextResponse.json(payload, {
            status: response.status,
            headers: {
                "Cache-Control": "no-store",
            },
        });
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "读取桥接配置失败" },
            { status: 500 },
        );
    }
}
