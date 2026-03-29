import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_ORIGIN = resolveEngineOrigin();

export async function GET() {
  const session = await auth();
  if (!session) return new NextResponse("Unauthorized", { status: 401 });

  try {
    const res = await fetch(`${ENGINE_ORIGIN}/v1/config/workspace`);
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return new NextResponse("Internal Error", { status: 500 });
  }
}

export async function POST(req: Request) {
  const session = await auth();
  if (!session) return new NextResponse("Unauthorized", { status: 401 });

  try {
    const body = await req.json();
    const res = await fetch(`${ENGINE_ORIGIN}/v1/config/workspace`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return new NextResponse("Internal Error", { status: 500 });
  }
}
