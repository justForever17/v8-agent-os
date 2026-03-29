import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_ORIGIN = resolveEngineOrigin();

export async function GET() {
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
