export const PERSONALIZATION_STORAGE_KEY = "v8-web-personalization";

export type LightBackgroundMediaType = "image" | "video";

export type UserAppearancePreferences = {
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
    return `(() => { try { const raw = localStorage.getItem(${JSON.stringify(PERSONALIZATION_STORAGE_KEY)}); if (!raw) return; const value = JSON.parse(raw); const media = typeof value?.lightBackgroundMedia === "string" ? value.lightBackgroundMedia.trim() : (typeof value?.lightBackgroundImage === "string" ? value.lightBackgroundImage.trim() : ""); const kind = value?.lightBackgroundMediaType === "video" || media.toLowerCase().endsWith(".mp4") ? "video" : "image"; if (!value?.lightBackgroundEnabled || !media.startsWith("/user-assets/background/")) return; const root = document.documentElement; root.dataset.v8WallpaperKind = kind; if (kind !== "image") return; const src = "/api/user-media?src=" + encodeURIComponent(media); root.dataset.v8Wallpaper = "active"; root.style.setProperty("--v8-wallpaper-image", "url(" + JSON.stringify(src) + ")"); } catch (_) {} })();`;
}
