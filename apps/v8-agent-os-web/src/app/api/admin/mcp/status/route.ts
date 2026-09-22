import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextResponse } from 'next/server';
import { resolveEngineBaseUrl } from "@admin/lib/server/runtime-config";

const ENGINE_URL = resolveEngineBaseUrl();

export async function GET() {
  try {
    const res = await engineFetch(`${ENGINE_URL}/mcp/status`, {
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
    console.error('Error in /api/admin/mcp/status:', error);
    return NextResponse.json(
      { error: 'Internal Server Error' },
      { status: 500 }
    );
  }
}
