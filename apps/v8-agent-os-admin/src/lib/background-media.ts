import fs from "node:fs";
import path from "node:path";
import { withUserBackgroundReferences } from "@/lib/users";
import { ensureUserMediaDirectory, removeManagedUserMedia } from "@/lib/user-media";

export function recordBackgroundReceipt(userId: string, media: string, kind: "image" | "video") {
  const directory = ensureUserMediaDirectory("background");
  const receipt = { userId, media, kind, createdAt: Date.now(), expiresAt: Date.now() + 86400_000 };
  fs.writeFileSync(path.join(directory, `.receipt-${path.basename(media)}.json`), JSON.stringify(receipt), { flag: "wx" });
  return { media, kind, expiresAt: receipt.expiresAt };
}
export function removeUnreferencedBackground(media: string) {
  withUserBackgroundReferences((references) => {
    if (!references.has(media)) {
      removeManagedUserMedia(media, "background");
      if (media.endsWith(".webp")) removeManagedUserMedia(media.replace(/\.webp$/, ".thumb.webp"), "background");
    }
  });
}
export function pruneExpiredBackgroundReceipts() {
  const directory = ensureUserMediaDirectory("background");
  withUserBackgroundReferences((references) => {
    for (const name of fs.readdirSync(directory)) {
      if (!/^\.receipt-[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.(webp|mp4)\.json$/.test(name)) continue;
      const receiptPath = path.join(directory, name);
      try {
        const receipt = JSON.parse(fs.readFileSync(receiptPath, "utf8"));
        if (receipt.committed || typeof receipt.expiresAt !== "number" || receipt.expiresAt > Date.now() || references.has(receipt.media)) continue;
        removeManagedUserMedia(receipt.media, "background");
        if (String(receipt.media).endsWith(".webp")) removeManagedUserMedia(receipt.media.replace(/\.webp$/, ".thumb.webp"), "background");
        fs.unlinkSync(receiptPath);
      } catch { /* An unreadable receipt never authorizes deleting a media file. */ }
    }
  });
}
