import fs from "fs";
import path from "path";

import { getBaseDir } from "@/lib/storage";

export type UserMediaKind = "avatar" | "background";

const USER_MEDIA_FILENAME_PATTERNS: Record<UserMediaKind, RegExp> = {
  avatar: /^[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.webp$/,
  background: /^[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.(?:webp|mp4)$/,
};

export function resolveUserMediaDirectory(kind: UserMediaKind) {
  return path.join(getBaseDir(), "assets", "user-media", kind);
}

export function resolveUserMediaFile(kind: UserMediaKind, filename: string) {
  if (!USER_MEDIA_FILENAME_PATTERNS[kind].test(filename)) return null;
  return path.join(resolveUserMediaDirectory(kind), filename);
}

export function buildUserMediaPublicPath(kind: UserMediaKind, filename: string) {
  if (!USER_MEDIA_FILENAME_PATTERNS[kind].test(filename)) {
    throw new Error("Invalid user media filename");
  }
  return `/user-assets/${kind}/${filename}`;
}

export function ensureUserMediaDirectory(kind: UserMediaKind) {
  const directory = resolveUserMediaDirectory(kind);
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}

export function removeManagedUserMedia(value: unknown, expectedKind: UserMediaKind) {
  const normalized = String(value || "").trim();
  const prefix = `/user-assets/${expectedKind}/`;
  if (!normalized.startsWith(prefix)) return;
  const filename = normalized.slice(prefix.length);
  const target = resolveUserMediaFile(expectedKind, filename);
  if (!target || !fs.existsSync(target)) return;
  try {
    fs.unlinkSync(target);
  } catch (error) {
    console.warn(`[user-media] Failed to remove stale ${expectedKind} asset:`, error);
  }
}
