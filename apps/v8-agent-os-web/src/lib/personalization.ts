export const PERSONALIZATION_STORAGE_KEY = "v8-web-personalization";
export { backgroundFromAppearance, normalizeBackgroundPlaylist, DEFAULT_IMAGE_DURATION_MS } from "@v8/product-ui/background-playlist";
import type { BackgroundPlaylist } from "@v8/product-ui/background-playlist";

export type LightBackgroundMediaType = "image" | "video";

export type UserAppearancePreferences = {
    webBackground?: BackgroundPlaylist;
    lightBackgroundMedia?: string;
    lightBackgroundMediaType?: LightBackgroundMediaType;
    /** @deprecated Read compatibility for image-only profiles. */
    lightBackgroundImage?: string;
    lightBackgroundEnabled?: boolean;
};

export function normalizeAppearance(value: unknown): UserAppearancePreferences {
    const record = value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
    const media = String(record.lightBackgroundMedia || record.lightBackgroundImage || "").trim();
    const inferredType: LightBackgroundMediaType = media.toLowerCase().endsWith(".mp4") ? "video" : "image";
    const requestedType = String(record.lightBackgroundMediaType || "").trim().toLowerCase();
    const mediaType: LightBackgroundMediaType = requestedType === "video" && inferredType === "video" ? "video" : inferredType;
    return {
        ...(record.webBackground === undefined ? {} : { webBackground: record.webBackground as BackgroundPlaylist }),
        lightBackgroundMedia: media,
        lightBackgroundMediaType: mediaType,
        lightBackgroundImage: mediaType === "image" ? media : "",
        lightBackgroundEnabled: Boolean(record.lightBackgroundEnabled && media),
    };
}

export function resolveLightBackgroundMediaSrc(value?: string | null) {
    const raw = String(value || "").trim();
    if (/^\/user-assets\/background\/[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.(?:webp|mp4)$/i.test(raw)) {
        return `/api/user-media?src=${encodeURIComponent(raw)}`;
    }
    return "";
}

export function buildPersonalizationBootstrapScript() {
    // Identity is not established before hydration. Retire the old global cache
    // instead of showing the previous principal's media during bootstrap.
    return `try { localStorage.removeItem(${JSON.stringify(PERSONALIZATION_STORAGE_KEY)}); } catch (_) {}`;
}
