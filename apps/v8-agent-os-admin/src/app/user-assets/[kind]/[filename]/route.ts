import fs from "fs";
import { Readable } from "stream";

import { NextResponse } from "next/server";

import { resolveUserMediaFile, type UserMediaKind } from "@/lib/user-media";

export const runtime = "nodejs";

const ALLOWED_KINDS = new Set<UserMediaKind>(["avatar", "background"]);

export async function GET(
  request: Request,
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

  const isVideo = filename.toLowerCase().endsWith(".mp4");
  const stat = fs.statSync(target);
  const headers = new Headers({
    "Content-Type": isVideo ? "video/mp4" : "image/webp",
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    "Accept-Ranges": "bytes",
  });

  if (!isVideo) {
    headers.set("Content-Length", String(stat.size));
    return new NextResponse(fs.readFileSync(target), { status: 200, headers });
  }

  const range = request.headers.get("range");
  let start = 0;
  let end = stat.size - 1;
  let status = 200;
  if (range) {
    const match = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
    if (!match || (!match[1] && !match[2])) {
      headers.set("Content-Range", `bytes */${stat.size}`);
      return new NextResponse(null, { status: 416, headers });
    }
    if (!match[1] && match[2]) {
      const suffixLength = Number(match[2]);
      if (!Number.isSafeInteger(suffixLength) || suffixLength <= 0) {
        headers.set("Content-Range", `bytes */${stat.size}`);
        return new NextResponse(null, { status: 416, headers });
      }
      start = Math.max(0, stat.size - suffixLength);
      end = stat.size - 1;
    } else {
      start = Number(match[1]);
      end = match[2] ? Number(match[2]) : stat.size - 1;
    }
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end < start || start >= stat.size) {
      headers.set("Content-Range", `bytes */${stat.size}`);
      return new NextResponse(null, { status: 416, headers });
    }
    end = Math.min(end, stat.size - 1);
    status = 206;
    headers.set("Content-Range", `bytes ${start}-${end}/${stat.size}`);
  }
  headers.set("Content-Length", String(end - start + 1));
  const stream = Readable.toWeb(fs.createReadStream(target, { start, end })) as ReadableStream;
  return new NextResponse(stream, { status, headers });
}
