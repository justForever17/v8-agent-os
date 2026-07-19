export const PERSONALIZATION_STORAGE_KEY = "v8-web-personalization";

export type UserAppearancePreferences = {
    lightBackgroundImage?: string;
    lightBackgroundEnabled?: boolean;
};

export function normalizeAppearance(value: unknown): UserAppearancePreferences {
    const record = value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
    const image = String(record.lightBackgroundImage || "").trim();
    return {
        lightBackgroundImage: image,
        lightBackgroundEnabled: Boolean(record.lightBackgroundEnabled && image),
    };
}

export function resolveLightBackgroundSrc(value?: string | null) {
    const raw = String(value || "").trim();
    if (raw.startsWith("/user-assets/background/")) {
        return `/api/user-media?src=${encodeURIComponent(raw)}`;
    }
    return "";
}

export function buildPersonalizationBootstrapScript() {
    return `(() => { try { const raw = localStorage.getItem(${JSON.stringify(PERSONALIZATION_STORAGE_KEY)}); if (!raw) return; const value = JSON.parse(raw); const image = typeof value?.lightBackgroundImage === "string" ? value.lightBackgroundImage.trim() : ""; if (!value?.lightBackgroundEnabled || !image.startsWith("/user-assets/background/")) return; const src = "/api/user-media?src=" + encodeURIComponent(image); const root = document.documentElement; root.dataset.v8Wallpaper = "active"; root.style.setProperty("--v8-wallpaper-image", "url(" + JSON.stringify(src) + ")"); } catch (_) {} })();`;
}
