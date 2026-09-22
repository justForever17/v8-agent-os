import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextResponse } from "next/server";
import { auth } from "@admin/lib/auth";
import { resolveEngineBaseUrl } from "@admin/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET() {
  const session = await auth();
  if (!session?.user?.adminAuthenticated || session.user.role !== "ADMIN") return new NextResponse("Unauthorized", { status: 401 });

  try {
    const res = await engineFetch(`${ENGINE_URL}/mcp/tools`);
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return new NextResponse("Internal Error", { status: 500 });
  }
}
