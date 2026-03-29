import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_ORIGIN = resolveEngineOrigin();

export async function POST(req: Request) {
  try {
    const formData = await req.formData();
    const res = await fetch(`${ENGINE_ORIGIN}/v1/skills/install/zip`, {
      method: "POST",
      body: formData, // FormData matches multipart/form-data
    });
    
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    console.error("Error uploading skill zip:", error);
    return NextResponse.json(
      { error: "Internal Server Error" },
      { status: 500 }
    );
  }
}
