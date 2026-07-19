"use client";

import { useEffect } from "react";

import { useClientProfile } from "@/hooks/use-client-profile";
import {
    normalizeAppearance,
    PERSONALIZATION_STORAGE_KEY,
    resolveLightBackgroundSrc,
} from "@/lib/personalization";

function clearWallpaper(root: HTMLElement) {
    if (root.dataset.v8Wallpaper) delete root.dataset.v8Wallpaper;
    if (root.style.getPropertyValue("--v8-wallpaper-image")) {
        root.style.removeProperty("--v8-wallpaper-image");
    }
}

function commitWallpaper(root: HTMLElement, src: string) {
    const cssValue = `url(${JSON.stringify(src)})`;
    if (root.dataset.v8Wallpaper !== "active") {
        root.dataset.v8Wallpaper = "active";
    }
    if (root.style.getPropertyValue("--v8-wallpaper-image") !== cssValue) {
        root.style.setProperty("--v8-wallpaper-image", cssValue);
    }
}

export function PersonalizationProvider({ children }: { children: React.ReactNode }) {
    const { profile, canonicalLoaded } = useClientProfile();

    useEffect(() => {
        if (!canonicalLoaded) return;
        const root = document.documentElement;
        const appearance = normalizeAppearance(profile?.appearance);
        if (!appearance.lightBackgroundEnabled || !appearance.lightBackgroundImage) {
            clearWallpaper(root);
            window.localStorage.removeItem(PERSONALIZATION_STORAGE_KEY);
            return;
        }
        const src = resolveLightBackgroundSrc(appearance.lightBackgroundImage);
        if (!src) {
            clearWallpaper(root);
            window.localStorage.removeItem(PERSONALIZATION_STORAGE_KEY);
            return;
        }
        window.localStorage.setItem(PERSONALIZATION_STORAGE_KEY, JSON.stringify(appearance));
        const nextCssValue = `url(${JSON.stringify(src)})`;
        if (
            root.dataset.v8Wallpaper === "active"
            && root.style.getPropertyValue("--v8-wallpaper-image") === nextCssValue
        ) {
            return;
        }

        let cancelled = false;
        const image = new Image();
        image.onload = () => {
            if (!cancelled) commitWallpaper(root, src);
        };
        image.src = src;
        if (image.complete && image.naturalWidth > 0) {
            commitWallpaper(root, src);
        }
        return () => {
            cancelled = true;
            image.onload = null;
        };
    }, [canonicalLoaded, profile?.appearance]);

    return (
        <>
            <div className="v8-personalization-wallpaper" aria-hidden="true" />
            <div className="v8-personalization-wallpaper-overlay" aria-hidden="true" />
            {children}
        </>
    );
}
