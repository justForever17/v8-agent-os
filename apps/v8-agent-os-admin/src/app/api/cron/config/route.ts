import { NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";

const ENGINE_ORIGIN = resolveEngineOrigin();

export async function GET() {
  try {
    const res = await fetch(`${ENGINE_ORIGIN}/v1/cron/config`, {
      method: "GET",
      // Important to skip caching for real-time config
      headers: { "Cache-Control": "no-cache", "Pragma": "no-cache" },
      cache: "no-store",
    });
    
    if (!res.ok) {
      return NextResponse.json(
        { error: "Failed to fetch cron config" },
        { status: res.status }
      );
    }
    
    const data = await res.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Error fetching cron config:", error);
    return NextResponse.json(
      { error: "Internal Server Error" },
      { status: 500 }
    );
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const res = await fetch(`${ENGINE_ORIGIN}/v1/cron/config`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      return NextResponse.json(
        { error: "Failed to save cron config" },
        { status: res.status }
      );
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error("Error saving cron config:", error);
    return NextResponse.json(
      { error: "Internal Server Error" },
      { status: 500 }
    );
  }
}
