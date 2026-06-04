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
    const upstream = new URL(`${ENGINE_ORIGIN}/v1/skills/list`);
    for (const key of ["sessionId", "workspacePath", "workspaceId", "projectId"]) {
      const value = req.nextUrl.searchParams.get(key);
      if (value) {
        upstream.searchParams.set(key, value);
      }
    }
    const res = await fetch(upstream.toString());
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
