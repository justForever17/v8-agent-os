import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextResponse } from "next/server";
import { auth } from "@admin/lib/auth";
import { resolveEngineBaseUrl } from "@admin/lib/server/runtime-config";

const ENGINE_URL = `${resolveEngineBaseUrl()}/models/catalog`;

export async function GET() {
    const session = await auth();
    if (!session?.user?.email || session.user.adminAuthenticated !== true || session.user.role !== "ADMIN") {
        return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const response = await engineFetch(ENGINE_URL, { next: { revalidate: 60 } });
    const data = await response.json().catch(() => ({}));
    return NextResponse.json(data, { status: response.status });
}
