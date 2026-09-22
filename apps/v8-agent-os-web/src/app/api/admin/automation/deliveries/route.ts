import { NextRequest, NextResponse } from "next/server";
import { auth } from "@admin/lib/auth";
import { proxyEngineJson } from "@admin/lib/server/engine-proxy";
import { resolveInternalSecret } from "@admin/lib/server/runtime-config";

export async function GET(req: NextRequest) {
    const session = await auth();
    if (!session?.user?.adminAuthenticated || session.user.role !== "ADMIN") return NextResponse.json({ detail: { code: "automation_admin_auth_required", message: "请登录管理员后重试。" } }, { status: 401 });
    if (session.user.adminAuthenticated !== true || session.user.role !== "ADMIN") return NextResponse.json({ detail: { code: "automation_admin_required", message: "此操作仅限管理员。" } }, { status: 403 });
    const owner = session.user.email || session.user.login;
    if (!owner) return NextResponse.json({ detail: { code: "automation_admin_auth_required", message: "管理员身份不可用。" } }, { status: 401 });
    const query = new URLSearchParams();
    for (const key of ["limit", "after", "ownership"]) {
        const value = req.nextUrl.searchParams.get(key);
        if (value !== null) query.set(key, value);
    }
    try {
        const { response, data } = await proxyEngineJson(`/automation/deliveries?${query}`, {
            headers: { "x-v8-agent-os-secret": resolveInternalSecret(), "x-v8-agent-os-user-email": owner, "x-v8-admin-role": "ADMIN" },
        });
        return NextResponse.json(data, { status: response.status });
    } catch {
        return NextResponse.json({ detail: { code: "automation_delivery_unavailable", message: "暂时无法读取待核对任务。" } }, { status: 502 });
    }
}
