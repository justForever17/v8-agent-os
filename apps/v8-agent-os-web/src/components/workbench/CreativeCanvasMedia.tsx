"use client";

/* eslint-disable @next/next/no-img-element -- session resources include local and signed URLs without fixed dimensions */

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import { Box, FileText, ImageIcon, Loader2, Music2, Pause, Play, Video } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";

export type CreativeCanvasMediaResource = {
    id?: string;
    origin?: string;
    name: string;
    mimeType: string;
    mediaType?: string;
    url?: string;
    previewUrl?: string;
};

const readyResourceUrls = new Set<string>();

function ModelLoadingState() {
    const t = useT();
    return (
        <div className="flex h-full w-full items-center justify-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            {t("web.workbench.canvas.media.loading3d")}
        </div>
    );
}

const ModelViewer = dynamic(
    () => import("@/components/chat/ModelViewer").then((module) => module.ModelViewer),
    {
        ssr: false,
        loading: () => <ModelLoadingState />,
    },
);

function kindOf(resource: CreativeCanvasMediaResource) {
    const explicit = String(resource.mediaType || "").toLowerCase();
    const mime = String(resource.mimeType || "").toLowerCase();
    const name = String(resource.name || "").toLowerCase();
    if (explicit === "psd" || mime.includes("photoshop") || /\.psd$/.test(name)) return "psd";
    if (explicit && explicit !== "unknown") return explicit;
    if (mime.startsWith("image/")) return "image";
    if (mime.startsWith("video/")) return "video";
    if (mime.startsWith("audio/")) return "audio";
    if (mime.includes("gltf") || /\.(?:glb|gltf|obj|fbx|stl|usd|usdz)$/.test(name)) return "model_3d";
    if (mime.startsWith("text/") || mime.includes("json") || mime.includes("pdf")) return "document";
    return "file";
}

function LoadingState({ compact }: { compact: boolean }) {
    return (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-muted/20 text-muted-foreground">
            <Loader2 className={compact ? "h-3.5 w-3.5 animate-spin motion-reduce:animate-none" : "h-5 w-5 animate-spin motion-reduce:animate-none"} />
        </div>
    );
}

function FileFallback({ resource, compact }: { resource: CreativeCanvasMediaResource; compact: boolean }) {
    const kind = kindOf(resource);
    const Icon = kind === "image"
        ? ImageIcon
        : kind === "video"
            ? Video
            : kind === "audio"
                ? Music2
                : kind === "model_3d"
                    ? Box
                    : FileText;
    return (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-3 text-center text-muted-foreground">
            <Icon className={compact ? "h-4 w-4" : "h-8 w-8 opacity-70"} />
            {!compact ? <span className="line-clamp-2 text-[11px]">{resource.name}</span> : null}
        </div>
    );
}

export function CreativeCanvasMedia({
    resource,
    compact = false,
    inspect = false,
    active = true,
    visible = true,
    onDimensions,
}: {
    resource: CreativeCanvasMediaResource;
    compact?: boolean;
    inspect?: boolean;
    active?: boolean;
    visible?: boolean;
    onDimensions?: (dimensions: { width: number; height: number }) => void;
}) {
    const t = useT();
    const kind = kindOf(resource);
    const url = String((kind === "psd" ? resource.previewUrl : "") || resource.url || "").trim();
    const cacheKey = `${kind}:${url}`;
    const rootRef = useRef<HTMLDivElement | null>(null);
    const playableRef = useRef<HTMLMediaElement | null>(null);
    const [playing, setPlaying] = useState(false);
    const [inView, setInView] = useState(!compact);
    const effectiveVisible = visible && inView;
    const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
        () => readyResourceUrls.has(cacheKey) ? "ready" : "loading",
    );
    useEffect(() => {
        setPlaying(false);
        setLoadState(readyResourceUrls.has(cacheKey) ? "ready" : "loading");
    }, [cacheKey]);
    useEffect(() => {
        if (!compact) {
            setInView(true);
            return;
        }
        const element = rootRef.current;
        if (!element || typeof IntersectionObserver === "undefined") {
            setInView(true);
            return;
        }
        const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { rootMargin: "120px" });
        observer.observe(element);
        return () => observer.disconnect();
    }, [compact]);
    useEffect(() => {
        const media = playableRef.current;
        if (!media) return;
        const sync = () => {
            if (!active || !effectiveVisible || document.hidden) media.pause();
            else if (media.readyState === HTMLMediaElement.HAVE_NOTHING) media.load();
        };
        sync();
        document.addEventListener("visibilitychange", sync);
        return () => document.removeEventListener("visibilitychange", sync);
    }, [active, effectiveVisible]);
    const markReady = () => {
        readyResourceUrls.add(cacheKey);
        setLoadState("ready");
    };
    const togglePlayback = () => {
        const media = playableRef.current;
        if (!media) return;
        if (media.paused) void media.play();
        else media.pause();
    };
    if (!url) return <FileFallback resource={resource} compact={compact} />;
    if (kind === "image" || kind === "psd") {
        return (
            <div ref={rootRef} className="relative h-full w-full">
                <div className="absolute inset-0"><FileFallback resource={resource} compact={compact} /></div>
                {loadState === "error" ? <FileFallback resource={resource} compact={compact} /> : (
                    <img
                        src={url}
                        alt={resource.name}
                        draggable={false}
                        loading={compact || !effectiveVisible ? "lazy" : "eager"}
                        decoding="async"
                        onLoad={(event) => {
                            markReady();
                            if (compact || inspect) return;
                            onDimensions?.({
                                width: event.currentTarget.naturalWidth,
                                height: event.currentTarget.naturalHeight,
                            });
                        }}
                        onError={() => setLoadState("error")}
                        className={`h-full w-full object-contain transition-opacity ${loadState === "loading" ? "opacity-0" : "opacity-100"}`}
                    />
                )}
                {effectiveVisible && loadState === "loading" ? <LoadingState compact={compact} /> : null}
            </div>
        );
    }
    if (kind === "video") {
        if (inspect) {
            return (
                <div ref={rootRef} className="relative h-full w-full">
                    <div className="absolute inset-0"><FileFallback resource={resource} compact={compact} /></div>
                    {loadState === "error" ? <FileFallback resource={resource} compact={compact} /> : (
                        <video
                            src={url}
                            controls
                            playsInline
                            preload="metadata"
                            onLoadedData={markReady}
                            onLoadedMetadata={(event) => {
                                if (Number.isFinite(event.currentTarget.duration) && event.currentTarget.duration > 0) {
                                    event.currentTarget.currentTime = Math.min(0.001, event.currentTarget.duration / 2);
                                }
                            }}
                            onSeeked={markReady}
                            onError={() => setLoadState("error")}
                            className={`h-full w-full object-contain ${loadState === "loading" ? "opacity-0" : "opacity-100"}`}
                        />
                    )}
                    {loadState === "loading" ? <LoadingState compact={compact} /> : null}
                </div>
            );
        }
        return (
            <div ref={rootRef} className="relative h-full w-full">
                <div className="absolute inset-0"><FileFallback resource={resource} compact={compact} /></div>
                {loadState === "error" ? <FileFallback resource={resource} compact={compact} /> : (
                    <video
                        ref={(element) => { playableRef.current = element; }}
                        src={url}
                        muted
                        playsInline
                        preload={effectiveVisible ? "metadata" : "none"}
                        onPlay={() => setPlaying(true)}
                        onPause={() => setPlaying(false)}
                        onEnded={() => setPlaying(false)}
                        onLoadedData={markReady}
                        onLoadedMetadata={(event) => {
                            if (!compact) onDimensions?.({ width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight });
                            if (event.currentTarget.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) markReady();
                            else if (Number.isFinite(event.currentTarget.duration) && event.currentTarget.duration > 0) {
                                event.currentTarget.currentTime = Math.min(0.001, event.currentTarget.duration / 2);
                            }
                        }}
                        onSeeked={markReady}
                        onError={() => setLoadState("error")}
                        className={`pointer-events-none h-full w-full object-contain transition-opacity ${loadState === "loading" ? "opacity-0" : "opacity-100"}`}
                    />
                )}
                {effectiveVisible && loadState === "loading" ? <LoadingState compact={compact} /> : null}
                {!compact && loadState === "ready" ? (
                    <button type="button" onClick={togglePlayback} className="absolute left-1/2 top-1/2 grid h-10 w-10 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/65 text-white shadow-lg hover:bg-black/80" aria-label={t(playing ? "web.workbench.canvas.media.pauseVideo" : "web.workbench.canvas.media.playVideo")}>
                        {playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
                    </button>
                ) : null}
            </div>
        );
    }
    if (kind === "audio" && !compact) {
        if (inspect) return <audio src={url} controls preload="metadata" onLoadedMetadata={markReady} onError={() => setLoadState("error")} className="w-[88%]" />;
        return (
            <div className="relative h-full w-full">
                <FileFallback resource={resource} compact={compact} />
                <audio ref={(element) => { playableRef.current = element; }} src={url} preload={effectiveVisible ? "metadata" : "none"} onLoadedMetadata={markReady} onError={() => setLoadState("error")} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />
                {effectiveVisible && loadState === "loading" ? <LoadingState compact={compact} /> : null}
                {loadState === "ready" ? (
                    <button type="button" onClick={togglePlayback} className="absolute left-1/2 top-1/2 grid h-10 w-10 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/65 text-white shadow-lg hover:bg-black/80" aria-label={t(playing ? "web.workbench.canvas.media.pauseAudio" : "web.workbench.canvas.media.playAudio")}>
                        {playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
                    </button>
                ) : null}
            </div>
        );
    }
    if (kind === "model_3d") {
        const supported = /\.(?:glb|gltf)(?:$|[?#])/i.test(url) || /\.(?:glb|gltf)$/i.test(resource.name);
        if (inspect && supported) {
            return <ModelViewer src={url} className="h-full w-full rounded-none border-0" compact={false} active interactive />;
        }
        return <FileFallback resource={resource} compact={compact} />;
    }
    return <FileFallback resource={resource} compact={compact} />;
}

export function creativeCanvasMediaType(resource: CreativeCanvasMediaResource) {
    return kindOf(resource);
}
