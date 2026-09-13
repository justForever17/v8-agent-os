"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useClientProfile } from "@/hooks/use-client-profile";
import { useSurfaceVisible } from "@/hooks/use-surface-visible";
import { backgroundFromAppearance, normalizeBackgroundPlaylist, resolveLightBackgroundMediaSrc } from "@/lib/personalization";

type Playback = {
    available: boolean; muted: boolean; toggleMuted: () => void;
    enabled: boolean; paused: boolean; togglePaused: () => void;
    multiple: boolean; next: () => void; previous: () => void; error: string;
};
const BackgroundVideoAudioContext = createContext<Playback | null>(null);
export function useBackgroundVideoAudio() {
    const value = useContext(BackgroundVideoAudioContext);
    if (!value) throw new Error("useBackgroundVideoAudio must be used within PersonalizationProvider");
    return value;
}
function clearWallpaper() {
    const root = document.documentElement;
    delete root.dataset.v8Wallpaper;
    delete root.dataset.v8WallpaperKind;
    root.style.removeProperty("--v8-wallpaper-image");
}

export function PersonalizationProvider({ children }: { children: React.ReactNode }) {
    const { profile, canonicalLoaded } = useClientProfile();
    const visible = useSurfaceVisible();
    const rawAppearance = JSON.stringify(profile?.appearance || {});
    const { playlist, configurationError } = useMemo(() => {
        try { return { playlist: backgroundFromAppearance(JSON.parse(rawAppearance)), configurationError: "" }; }
        catch { return { playlist: normalizeBackgroundPlaylist({}), configurationError: "web.background.configurationError" }; }
    }, [rawAppearance]);
    const items = playlist.items;
    const [selection, setSelection] = useState({ id: "", serial: 0 });
    const selectionRef = useRef(selection);
    const item = items.find((candidate) => candidate.id === selection.id) || items[0];
    const enabled = canonicalLoaded && playlist.enabled && Boolean(item) && Boolean(profile?.id);
    const videoRef = useRef<HTMLVideoElement>(null);
    const [ready, setReady] = useState(false);
    const [videoMuted, setVideoMuted] = useState(true);
    const [paused, setPaused] = useState(false);
    const [error, setError] = useState("");
    const [poster, setPoster] = useState("");
    const failed = useRef(new Set<string>());
    const clock = useRef({ serial: -1, remaining: 0, started: 0 });
    const mediaSrc = enabled && item ? resolveLightBackgroundMediaSrc(item.media) : "";
    useEffect(() => {
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) setPaused(true);
        return clearWallpaper;
    }, []);
    useEffect(() => {
        failed.current.clear();
        if (!items.some((candidate) => candidate.id === selectionRef.current.id)) {
            const next = { id: items[0]?.id || "", serial: selectionRef.current.serial + 1 };
            selectionRef.current = next; setSelection(next);
        }
    }, [items]);
    useEffect(() => { clearWallpaper(); setPoster(""); setVideoMuted(true); }, [profile?.id]);

    const advance = useCallback((direction: number, expectedSerial = selectionRef.current.serial, failedItem = false) => {
        if (expectedSerial !== selectionRef.current.serial || !item) return;
        if (failedItem) failed.current.add(item.id);
        const candidates = items.filter((candidate) => !failed.current.has(candidate.id));
        if (!candidates.length) { setPaused(true); setReady(false); setError("web.background.playbackFailed"); return; }
        const currentIndex = items.findIndex((candidate) => candidate.id === item.id);
        let nextItem = item;
        if (playlist.order === "shuffle" && candidates.length > 1) {
            const choices = candidates.filter((candidate) => candidate.id !== item.id);
            nextItem = choices[Math.floor(Math.random() * choices.length)];
        } else {
            for (let step = 1; step <= items.length; step++) {
                const candidate = items[(currentIndex + direction * step + items.length * 2) % items.length];
                if (!failed.current.has(candidate.id)) { nextItem = candidate; break; }
            }
        }
        const video = videoRef.current;
        if (video && video.readyState >= 2 && video.videoWidth) {
            try {
                const canvas = document.createElement("canvas");
                canvas.width = Math.min(video.videoWidth, 1920); canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
                canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
                const frame = canvas.toDataURL("image/jpeg", 0.95);
                setPoster(frame);
                document.documentElement.style.setProperty("--v8-wallpaper-image", `url(${JSON.stringify(frame)})`);
                document.documentElement.dataset.v8WallpaperKind = "image";
            } catch { /* The last image wallpaper remains a fallback. */ }
        }
        const next = { id: nextItem.id, serial: expectedSerial + 1 };
        selectionRef.current = next; setSelection(next);
    }, [item, items, playlist.order]);

    useEffect(() => {
        setReady(false); setError("");
        if (!enabled || !item) { clearWallpaper(); return; }
        const root = document.documentElement;
        root.style.setProperty("--v8-wallpaper-fit", item.fit);
        root.style.setProperty("--v8-wallpaper-position", item.position);
        clock.current = { serial: selection.serial, remaining: item.imageDurationMs || playlist.imageDurationMs, started: 0 };
        if (item.kind !== "image") return;
        let cancelled = false;
        const image = new Image();
        image.onload = () => {
            if (cancelled) return;
            root.style.setProperty("--v8-wallpaper-image", `url(${JSON.stringify(mediaSrc)})`);
            root.dataset.v8Wallpaper = "active"; root.dataset.v8WallpaperKind = "image";
            setReady(true);
        };
        image.onerror = () => { if (!cancelled) advance(1, selection.serial, true); };
        image.src = mediaSrc;
        return () => { cancelled = true; image.onload = null; image.onerror = null; image.src = ""; };
    // Source lifecycle is keyed by identity and generation, independent of theme and chat renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, item?.id, item?.media, item?.fit, item?.position, item?.imageDurationMs, mediaSrc, playlist.imageDurationMs, selection.serial]);

    useEffect(() => {
        const video = videoRef.current;
        const running = enabled && ready && visible && !paused;
        if (video) {
            if (!running) video.pause();
            else void video.play().catch((reason) => {
                if (reason?.name === "AbortError" || videoRef.current !== video || selectionRef.current.serial !== selection.serial) return;
                setPaused(true); setError("web.background.playRequired");
            });
        }
        if (!running || item?.kind !== "image" || items.length < 2) return;
        const current = clock.current;
        current.started = performance.now();
        const timer = setTimeout(() => advance(1, selection.serial), Math.max(1, current.remaining));
        return () => { clearTimeout(timer); current.remaining = Math.max(1, current.remaining - (performance.now() - current.started)); current.started = 0; };
    }, [enabled, ready, visible, paused, item?.kind, items.length, selection.serial, advance]);

    const toggleMuted = useCallback(() => {
        const video = videoRef.current;
        if (!video) return;
        video.muted = !videoMuted; video.volume = 0.65; setVideoMuted(!videoMuted);
    }, [videoMuted]);
    const togglePaused = useCallback(() => {
        if (error && !ready) {
            failed.current.clear(); setError("");
            const next = { ...selectionRef.current, serial: selectionRef.current.serial + 1 };
            selectionRef.current = next; setSelection(next);
        }
        setPaused((current) => !current);
    }, [error, ready]);
    const value = { available: enabled && item?.kind === "video", muted: videoMuted, toggleMuted, enabled, paused, togglePaused, multiple: items.length > 1, next: () => advance(1), previous: () => advance(-1), error: error || configurationError };
    return <BackgroundVideoAudioContext.Provider value={value}>
        <div className="v8-personalization-wallpaper" aria-hidden="true" />
        {enabled && item?.kind === "video" ? <video
            key={`${item.id}:${selection.serial}`}
            ref={videoRef} className="v8-personalization-wallpaper-video"
            src={mediaSrc} poster={poster || undefined} muted={videoMuted} loop={items.length === 1}
            playsInline preload="metadata" disablePictureInPicture aria-hidden="true" tabIndex={-1}
            onCanPlay={() => {
                if (selectionRef.current.serial !== selection.serial) return;
                const root = document.documentElement;
                root.dataset.v8Wallpaper = "active"; root.dataset.v8WallpaperKind = "video";
                setReady(true);
            }}
            onEnded={() => advance(1, selection.serial)}
            onError={() => advance(1, selection.serial, true)}
        /> : null}
        {children}
    </BackgroundVideoAudioContext.Provider>;
}
