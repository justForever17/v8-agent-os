import fs from "fs";

import { NextResponse } from "next/server";

import { resolveUserMediaFile, type UserMediaKind } from "@/lib/user-media";

export const runtime = "nodejs";

const ALLOWED_KINDS = new Set<UserMediaKind>(["avatar", "background"]);

export async function GET(
  _request: Request,
  context: { params: Promise<{ kind: string; filename: string }> },
) {
  const { kind: rawKind, filename } = await context.params;
  if (!ALLOWED_KINDS.has(rawKind as UserMediaKind)) {
    return NextResponse.json({ error: "Unsupported user media kind" }, { status: 404 });
  }
  const kind = rawKind as UserMediaKind;
  const target = resolveUserMediaFile(kind, filename);
  if (!target || !fs.existsSync(target)) {
    return NextResponse.json({ error: "User media not found" }, { status: 404 });
  }

  return new NextResponse(fs.readFileSync(target), {
    status: 200,
    headers: {
      "Content-Type": "image/webp",
      "Cache-Control": "public, max-age=31536000, immutable",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
