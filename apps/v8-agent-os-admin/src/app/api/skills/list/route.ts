import { NextRequest, NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

const ENGINE_ORIGIN = resolveEngineOrigin();

export async function GET(req: NextRequest) {
  const userEmail = await resolveAuthorizedUserEmail(req);
  if (!userEmail) {
    return unauthorizedJson();
  }
  try {
    const res = await fetch(`${ENGINE_ORIGIN}/v1/skills/list`);
    const data = await res.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Error fetching skills list:", error);
    return NextResponse.json(
      { error: "Internal Server Error" },
      { status: 500 }
    );
  }
}
