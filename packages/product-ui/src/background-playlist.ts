/** User appearance metadata only; Admin owns persistence and managed media. */
export type BackgroundItem = {
  id: string; media: string; kind: "image" | "video";
  fit: "cover" | "contain"; position: string; imageDurationMs?: number;
};
export type BackgroundPlaylist = {
  revision: number; enabled: boolean; items: BackgroundItem[];
  imageDurationMs: number; order: "ordered" | "shuffle";
};
export const DEFAULT_IMAGE_DURATION_MS = 30_000;
export const MAX_BACKGROUND_ITEMS = 50;
export function isBackgroundMedia(value: unknown): value is string {
  return typeof value === "string" && /^\/user-assets\/background\/[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.(webp|mp4)$/i.test(value);
}
function duration(value: unknown, fallback = DEFAULT_IMAGE_DURATION_MS) {
  if (value === undefined) return fallback;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 1000 || value > 3600_000) throw new Error("图片间隔应为 1–3600 秒");
  return Math.round(value);
}
export function normalizeBackgroundPlaylist(value: unknown): BackgroundPlaylist {
  const raw = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const source = raw.items ?? [];
  if (!Array.isArray(source) || source.length > MAX_BACKGROUND_ITEMS) throw new Error("背景列表最多包含 50 项");
  const ids = new Set<string>();
  const items: BackgroundItem[] = source.map((item) => {
    if (!item || typeof item !== "object" || !/^[A-Za-z0-9_-]{1,100}$/.test(item.id) || ids.has(item.id) || !isBackgroundMedia(item.media)) throw new Error("背景素材引用无效");
    ids.add(item.id);
    const kind = item.media.toLowerCase().endsWith(".mp4") ? "video" : "image";
    if (item.kind !== kind) throw new Error("背景素材类型不匹配");
    const position = String(item.position || "50% 50%");
    if (!/^(?:100|[0-9]{1,2})% (?:100|[0-9]{1,2})%$/.test(position)) throw new Error("背景位置无效");
    return { id: item.id, media: item.media, kind, fit: item.fit === "contain" ? "contain" : "cover", position, ...(item.imageDurationMs === undefined ? {} : { imageDurationMs: duration(item.imageDurationMs) }) };
  });
  return { revision: Number.isSafeInteger(raw.revision) && Number(raw.revision) >= 0 ? Number(raw.revision) : 0, enabled: raw.enabled === true, items, imageDurationMs: duration(raw.imageDurationMs), order: raw.order === "shuffle" ? "shuffle" : "ordered" };
}
export function backgroundFromAppearance(appearance: Record<string, unknown> = {}): BackgroundPlaylist {
  if (appearance.webBackground !== undefined) return normalizeBackgroundPlaylist(appearance.webBackground);
  const media = appearance.lightBackgroundMedia || appearance.lightBackgroundImage;
  return normalizeBackgroundPlaylist({ enabled: appearance.lightBackgroundEnabled === true, items: isBackgroundMedia(media) ? [{ id: "legacy-background", media, kind: media.toLowerCase().endsWith(".mp4") ? "video" : "image" }] : [] });
}
export function referencedBackgroundMedia(appearance: Record<string, unknown> = {}) {
  const refs = new Set<string>();
  for (const value of [appearance.lightBackgroundMedia, appearance.lightBackgroundImage]) if (isBackgroundMedia(value)) refs.add(value);
  // Read raw references conservatively even when an unknown future schema exists.
  const playlist = appearance.webBackground as { items?: Array<{ media?: unknown }> } | undefined;
  if (Array.isArray(playlist?.items)) for (const item of playlist.items) if (isBackgroundMedia(item.media)) refs.add(item.media);
  return refs;
}
