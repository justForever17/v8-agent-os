import { NextRequest, NextResponse } from "next/server";

import {
    ADMIN_CONNECTION_COOKIE,
    deriveAdminApiBaseUrl,
    getActiveAdminConnection,
    normalizeAdminBaseUrl,
    serializeAdminConnection,
    type AdminConnection,
} from "@/lib/server/admin-connection";
import { shouldUseSecureCookies } from "@/lib/server/cookie-policy";

export async function GET() {
    const current = await getActiveAdminConnection();
    return NextResponse.json({ connection: current });
}

export async function POST(req: NextRequest) {
    try {
        const body = await req.json().catch(() => ({}));
        const requestedAdminBase = normalizeAdminBaseUrl(body?.adminBaseUrl);
        const persist = body?.persist !== false;
        if (!requestedAdminBase) {
            return NextResponse.json({ error: "管理台地址不能为空" }, { status: 400 });
        }

        const bootstrapUrl = `${requestedAdminBase}/api/bootstrap/bridge`;
        const response = await fetch(bootstrapUrl, { cache: "no-store" });
        if (!response.ok) {
            const text = await response.text().catch(() => "");
            return NextResponse.json(
                { error: text || `连接管理台失败 (${response.status})` },
                { status: 502 },
            );
        }

        const payload = await response.json();
        const connection: AdminConnection = {
            adminBaseUrl: normalizeAdminBaseUrl(String(payload?.adminBaseUrl || requestedAdminBase)),
            adminApiBaseUrl: deriveAdminApiBaseUrl(String(payload?.adminBaseUrl || requestedAdminBase)),
            bridgeMode: String(payload?.bridgeMode || "admin_only"),
            reachable: Boolean(payload?.reachable ?? true),
            version: String(payload?.version || ""),
        };
        if (!connection.adminBaseUrl) {
            return NextResponse.json({ error: "管理台返回的 bridge 信息不完整" }, { status: 502 });
        }

        const result = NextResponse.json({ connection });
        if (persist) {
            result.cookies.set({
                name: ADMIN_CONNECTION_COOKIE,
                value: serializeAdminConnection(connection),
                httpOnly: true,
                sameSite: "lax",
                secure: shouldUseSecureCookies(),
                path: "/",
            });
        }
        return result;
    } catch (error) {
        return NextResponse.json(
            { error: error instanceof Error ? error.message : "保存连接失败" },
            { status: 500 },
        );
    }
}

export async function DELETE() {
    const result = NextResponse.json({ success: true });
    result.cookies.set({
        name: ADMIN_CONNECTION_COOKIE,
        value: "",
        maxAge: 0,
        path: "/",
    });
    return result;
}
