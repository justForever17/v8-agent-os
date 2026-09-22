import { engineFetch } from "@admin/lib/server/engine-fetch";
import { NextResponse } from "next/server";

import { resolveEngineOrigin } from "@admin/lib/server/runtime-config";


export async function POST() {
  try {
    const response = await engineFetch(`${resolveEngineOrigin()}/v1/storage-retention/registry/refresh`, {
      method: "POST",
      cache: "no-store",
    });
    const data = await response.json().catch(() => null);
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    return NextResponse.json(
      { detail: error instanceof Error ? error.message : String(error) },
      { status: 502 },
    );
  }
}
