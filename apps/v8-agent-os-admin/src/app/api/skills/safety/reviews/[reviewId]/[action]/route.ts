import { NextRequest, NextResponse } from "next/server";
import { resolveEngineOrigin } from "@/lib/server/runtime-config";
import { resolveAuthorizedUserEmail, unauthorizedJson } from "@/lib/server/request-auth";

const ENGINE_ORIGIN = resolveEngineOrigin();
const ALLOWED_ACTIONS = new Set(["approve", "disable", "revoke", "rescan"]);

export async function POST(
  req: NextRequest,
  context: { params: Promise<{ reviewId: string; action: string }> },
) {
  const userEmail = await resolveAuthorizedUserEmail(req);
  if (!userEmail) {
    return unauthorizedJson();
  }
  const { reviewId, action } = await context.params;
  const normalizedAction = String(action || "").trim().toLowerCase();
  if (!ALLOWED_ACTIONS.has(normalizedAction)) {
    return NextResponse.json({ error: "Unsupported action" }, { status: 400 });
  }
  try {
    const res = await fetch(`${ENGINE_ORIGIN}/v1/skills/safety/reviews/${encodeURIComponent(reviewId)}/${normalizedAction}`, {
      method: "POST",
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (error) {
    console.error("Error updating skill safety review:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
