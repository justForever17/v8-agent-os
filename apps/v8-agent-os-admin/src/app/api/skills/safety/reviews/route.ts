import { NextRequest, NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

const ENGINE_ORIGIN = resolveEngineOrigin();

export async function GET(req: NextRequest) {
  const userEmail = await resolveAuthorizedUserEmail(req);
  if (!userEmail) {
    return unauthorizedJson();
  }
  const search = req.nextUrl.search || "";
  try {
    const res = await fetch(`${ENGINE_ORIGIN}/v1/skills/safety/reviews${search}`, { cache: "no-store" });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    console.error("Error fetching skill safety reviews:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
