import { NextResponse } from 'next/server';
import { resolveEngineBaseUrl } from "@/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET() {
  try {
    const res = await fetch(`${ENGINE_URL}/mcp/status`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      // Ensure we fetch fresh data
      cache: 'no-store',
    });

    if (!res.ok) {
      return NextResponse.json(
        { error: 'Failed to fetch from backend' },
        { status: res.status }
      );
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error('Error in /api/mcp/status:', error);
    return NextResponse.json(
      { error: 'Internal Server Error' },
      { status: 500 }
    );
  }
}
