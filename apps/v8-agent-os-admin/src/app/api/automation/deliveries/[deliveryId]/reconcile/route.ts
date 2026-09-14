import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { proxyEngineJson } from "@/lib/server/engine-proxy";
import { resolveInternalSecret } from "@/lib/server/runtime-config";

export async function POST(req: NextRequest, context: { params: Promise<{ deliveryId: string }> }) {
    const session = await auth();
    if (!session?.user) return NextResponse.json({ detail: { code: "automation_admin_auth_required", message: "请登录管理员后重试。" } }, { status: 401 });
    if (session.user.role !== "ADMIN") return NextResponse.json({ detail: { code: "automation_admin_required", message: "此操作仅限管理员。" } }, { status: 403 });
    const owner = session.user.email || session.user.login;
    if (!owner) return NextResponse.json({ detail: { code: "automation_admin_auth_required", message: "管理员身份不可用。" } }, { status: 401 });
    try {
        const body = await req.text();
        if (new TextEncoder().encode(body).byteLength > 16384) return NextResponse.json({ detail: { code: "automation_reconciliation_invalid", message: "核对证据过长。" } }, { status: 422 });
        const { deliveryId } = await context.params;
        const { response, data } = await proxyEngineJson(`/automation/deliveries/${encodeURIComponent(deliveryId)}/reconcile`, {
            method: "POST", body,
            headers: { "Content-Type": "application/json", "x-v8-agent-os-secret": resolveInternalSecret(), "x-v8-agent-os-user-email": owner, "x-v8-admin-role": "ADMIN" },
        });
        return NextResponse.json(data, { status: response.status });
    } catch {
        return NextResponse.json({ detail: { code: "automation_delivery_unavailable", message: "暂时无法保存核对结果。" } }, { status: 502 });
    }
}
