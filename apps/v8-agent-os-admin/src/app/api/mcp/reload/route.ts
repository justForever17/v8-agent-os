import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function POST() {
  const session = await auth();
  if (!session) return new NextResponse("Unauthorized", { status: 401 });

  try {
    const res = await fetch(`${ENGINE_URL}/mcp/reload`, {
      method: "POST",
    });
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return new NextResponse("Internal Error", { status: 500 });
  }
}
