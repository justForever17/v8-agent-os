"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useProductTheme } from "@v8/product-ui";

import { useClientProfile } from "@/hooks/use-client-profile";
import {
    normalizeAppearance,
    PERSONALIZATION_STORAGE_KEY,
    resolveLightBackgroundMediaSrc,
} from "@/lib/personalization";

function clearWallpaper(root: HTMLElement) {
    if (root.dataset.v8Wallpaper) delete root.dataset.v8Wallpaper;
    if (root.dataset.v8WallpaperKind) delete root.dataset.v8WallpaperKind;
    if (root.style.getPropertyValue("--v8-wallpaper-image")) {
        root.style.removeProperty("--v8-wallpaper-image");
    }
}

function commitImageWallpaper(root: HTMLElement, src: string) {
    const cssValue = `url(${JSON.stringify(src)})`;
    if (root.dataset.v8Wallpaper !== "active") {
        root.dataset.v8Wallpaper = "active";
    }
    root.dataset.v8WallpaperKind = "image";
    if (root.style.getPropertyValue("--v8-wallpaper-image") !== cssValue) {
        root.style.setProperty("--v8-wallpaper-image", cssValue);
    }
}

type BackgroundVideoAudioContextValue = {
    available: boolean;
    muted: boolean;
    toggleMuted: () => void;
};

const BackgroundVideoAudioContext = createContext<BackgroundVideoAudioContextValue | null>(null);

export function useBackgroundVideoAudio() {
    const context = useContext(BackgroundVideoAudioContext);
    if (!context) throw new Error("useBackgroundVideoAudio must be used within PersonalizationProvider");
    return context;
}

export function PersonalizationProvider({ children }: { children: React.ReactNode }) {
    const { profile, canonicalLoaded } = useClientProfile();
    const { resolvedTheme } = useProductTheme();
    const videoRef = useRef<HTMLVideoElement | null>(null);
    const [videoSrc, setVideoSrc] = useState("");
    const [videoReady, setVideoReady] = useState(false);
    const [videoMuted, setVideoMuted] = useState(true);

    useEffect(() => {
        if (!canonicalLoaded) return;
        const root = document.documentElement;
        const appearance = normalizeAppearance(profile?.appearance);
        const media = appearance.lightBackgroundMedia || appearance.lightBackgroundImage || "";
        if (!appearance.lightBackgroundEnabled || !media) {
            setVideoSrc("");
            setVideoReady(false);
            setVideoMuted(true);
            clearWallpaper(root);
            window.localStorage.removeItem(PERSONALIZATION_STORAGE_KEY);
            return;
        }
        const src = resolveLightBackgroundMediaSrc(media);
        if (!src) {
            setVideoSrc("");
            setVideoReady(false);
            setVideoMuted(true);
            clearWallpaper(root);
            window.localStorage.removeItem(PERSONALIZATION_STORAGE_KEY);
            return;
        }
        window.localStorage.setItem(PERSONALIZATION_STORAGE_KEY, JSON.stringify(appearance));
        if (appearance.lightBackgroundMediaType === "video") {
            setVideoSrc((current) => {
                if (current === src) return current;
                setVideoReady(false);
                setVideoMuted(true);
                return src;
            });
            return;
        }
        setVideoSrc("");
        setVideoReady(false);
        setVideoMuted(true);
        const nextCssValue = `url(${JSON.stringify(src)})`;
        if (
            root.dataset.v8Wallpaper === "active"
            && root.dataset.v8WallpaperKind === "image"
            && root.style.getPropertyValue("--v8-wallpaper-image") === nextCssValue
        ) {
            return;
        }

        let cancelled = false;
        const image = new Image();
        image.onload = () => {
            if (!cancelled) commitImageWallpaper(root, src);
        };
        image.src = src;
        if (image.complete && image.naturalWidth > 0) {
            commitImageWallpaper(root, src);
        }
        return () => {
            cancelled = true;
            image.onload = null;
        };
    }, [canonicalLoaded, profile?.appearance]);

    useEffect(() => {
        if (resolvedTheme !== "dark") return;
        setVideoMuted(true);
        if (videoRef.current) videoRef.current.muted = true;
    }, [resolvedTheme]);

    const toggleMuted = useCallback(() => {
        const video = videoRef.current;
        if (!video || !videoReady || resolvedTheme !== "light") return;
        setVideoMuted((current) => {
            const next = !current;
            video.muted = next;
            if (!next) {
                video.volume = 0.65;
                void video.play().catch(() => {
                    video.muted = true;
                    setVideoMuted(true);
                });
            }
            return next;
        });
    }, [resolvedTheme, videoReady]);

    const backgroundVideoAudio = useMemo<BackgroundVideoAudioContextValue>(() => ({
        available: Boolean(videoSrc && videoReady && resolvedTheme === "light"),
        muted: videoMuted,
        toggleMuted,
    }), [resolvedTheme, toggleMuted, videoMuted, videoReady, videoSrc]);

    return (
        <BackgroundVideoAudioContext.Provider value={backgroundVideoAudio}>
            <div className="v8-personalization-wallpaper" aria-hidden="true" />
            <video
                ref={videoRef}
                className="v8-personalization-wallpaper-video"
                src={videoSrc || undefined}
                autoPlay
                muted={videoMuted}
                loop
                playsInline
                preload="auto"
                disablePictureInPicture
                aria-hidden="true"
                tabIndex={-1}
                onCanPlay={(event) => {
                    if (!videoSrc) return;
                    const root = document.documentElement;
                    root.style.removeProperty("--v8-wallpaper-image");
                    root.dataset.v8WallpaperKind = "video";
                    root.dataset.v8Wallpaper = "active";
                    event.currentTarget.muted = videoMuted;
                    setVideoReady(true);
                    void event.currentTarget.play().catch(() => undefined);
                }}
                onError={() => {
                    setVideoReady(false);
                    if (document.documentElement.dataset.v8WallpaperKind === "video") {
                        clearWallpaper(document.documentElement);
                    }
                }}
            />
            <div className="v8-personalization-wallpaper-overlay" aria-hidden="true" />
            {children}
        </BackgroundVideoAudioContext.Provider>
    );
}
