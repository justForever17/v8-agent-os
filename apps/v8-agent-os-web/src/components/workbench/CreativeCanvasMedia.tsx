"use client";

/* eslint-disable @next/next/no-img-element -- session resources include local and signed URLs without fixed dimensions */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Activity, Box, FileText, ImageIcon, Loader2, Music2, Pause, Play, Video } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";

export type CreativeCanvasMediaResource = {
    id?: string;
    sessionId?: string;
    origin?: string;
    name: string;
    mimeType: string;
    mediaType?: string;
    url?: string;
    previewUrl?: string;
};

const READY_RESOURCE_LIMIT = 128;
const readyResourceUrls = new Map<string, true>();
const documentVisibilitySubscribers = new Set<() => void>();
let documentVisibilityListening = false;

function notifyDocumentVisibilitySubscribers() {
    for (const subscriber of documentVisibilitySubscribers) subscriber();
}

function subscribeDocumentVisibility(subscriber: () => void) {
    documentVisibilitySubscribers.add(subscriber);
    if (!documentVisibilityListening && typeof document !== "undefined") {
        document.addEventListener("visibilitychange", notifyDocumentVisibilitySubscribers);
        documentVisibilityListening = true;
    }
    return () => {
        documentVisibilitySubscribers.delete(subscriber);
        if (documentVisibilityListening && !documentVisibilitySubscribers.size && typeof document !== "undefined") {
            document.removeEventListener("visibilitychange", notifyDocumentVisibilitySubscribers);
            documentVisibilityListening = false;
        }
    };
}

function documentVisibleSnapshot() {
    return typeof document === "undefined" || !document.hidden;
}

function hasReadyResource(cacheKey: string) {
    if (!readyResourceUrls.has(cacheKey)) return false;
    readyResourceUrls.delete(cacheKey);
    readyResourceUrls.set(cacheKey, true);
    return true;
}

function rememberReadyResource(cacheKey: string) {
    readyResourceUrls.delete(cacheKey);
    readyResourceUrls.set(cacheKey, true);
    while (readyResourceUrls.size > READY_RESOURCE_LIMIT) {
        const oldest = readyResourceUrls.keys().next().value;
        if (typeof oldest !== "string") break;
        readyResourceUrls.delete(oldest);
    }
}

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
    const rawExplicit = String(resource.mediaType || "").toLowerCase();
    const explicit = rawExplicit === "model3d" || rawExplicit === "3d" ? "model_3d" : rawExplicit;
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
                : kind === "motion"
                    ? Activity
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

type MotionPoint = [number | null, number | null, number | null, number | null];
type MotionFrame = {
    frameIndex: number;
    ptsTicks: number;
    pose: MotionPoint[];
    leftHand: MotionPoint[];
    rightHand: MotionPoint[];
};
type MotionManifest = {
    source?: {
        frameCount?: number;
        timeBase?: { numerator?: number; denominator?: number };
    };
    qa?: { status?: string };
};

const POSE_CONNECTIONS = [
    [0, 1], [1, 2], [2, 3], [3, 7], [0, 4], [4, 5], [5, 6], [6, 8],
    [9, 10], [11, 12], [11, 13], [13, 15], [15, 17], [15, 19], [15, 21],
    [12, 14], [14, 16], [16, 18], [16, 20], [16, 22], [11, 23], [12, 24],
    [23, 24], [23, 25], [25, 27], [27, 29], [29, 31], [24, 26], [26, 28],
    [28, 30], [30, 32],
] as const;
const HAND_CONNECTIONS = [
    [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8],
    [5, 9], [9, 10], [10, 11], [11, 12], [9, 13], [13, 14], [14, 15],
    [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
] as const;

function validPoint(point: MotionPoint | undefined): point is [number, number, number, number] {
    return Boolean(point && point.every((value) => typeof value === "number" && Number.isFinite(value)));
}

function MotionSkeleton({
    resource,
    compact,
    visible,
}: {
    resource: CreativeCanvasMediaResource;
    compact: boolean;
    visible: boolean;
}) {
    const t = useT();
    const [manifest, setManifest] = useState<MotionManifest | null>(null);
    const [frameIndex, setFrameIndex] = useState(0);
    const [frame, setFrame] = useState<MotionFrame | null>(null);
    const [error, setError] = useState(false);
    const baseUrl = useMemo(() => {
        const sessionId = String(resource.sessionId || "").trim();
        const origin = String(resource.origin || "").trim();
        const id = String(resource.id || "").trim();
        return sessionId && ["source", "artifact", "workspace_asset"].includes(origin) && id
            ? `/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/motion/${origin}/${encodeURIComponent(id)}`
            : "";
    }, [resource.id, resource.origin, resource.sessionId]);
    const frameCount = Math.max(0, Number(manifest?.source?.frameCount) || 0);
    useEffect(() => {
        if (!visible || !baseUrl) return;
        const controller = new AbortController();
        void fetch(`${baseUrl}/manifest`, { cache: "no-store", signal: controller.signal })
            .then(async (response) => {
                if (!response.ok) throw new Error(String(response.status));
                return response.json() as Promise<MotionManifest>;
            })
            .then((value) => {
                setError(false);
                setManifest(value);
                setFrameIndex((current) => Math.min(current, Math.max(0, Number(value.source?.frameCount || 1) - 1)));
            })
            .catch((reason) => { if (reason?.name !== "AbortError") setError(true); });
        return () => controller.abort();
    }, [baseUrl, visible]);
    useEffect(() => {
        if (!visible || !baseUrl || frameCount <= 0) return;
        const controller = new AbortController();
        void fetch(`${baseUrl}/frames/${frameIndex}`, { cache: "no-store", signal: controller.signal })
            .then(async (response) => {
                if (!response.ok) throw new Error(String(response.status));
                return response.json() as Promise<MotionFrame>;
            })
            .then(setFrame)
            .catch((reason) => { if (reason?.name !== "AbortError") setError(true); });
        return () => controller.abort();
    }, [baseUrl, frameCount, frameIndex, visible]);
    if (error || !baseUrl) return <FileFallback resource={resource} compact={compact} />;
    if (!manifest || !frame) {
        return <div className="flex h-full w-full items-center justify-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />{compact ? null : t("web.workbench.canvas.media.motionLoading")}</div>;
    }
    const renderConnections = (points: MotionPoint[], connections: readonly (readonly [number, number])[], color: string) => connections.flatMap(([from, to], index) => {
        const left = points[from];
        const right = points[to];
        return validPoint(left) && validPoint(right) ? [
            <line key={`${color}-${index}`} x1={left[0] * 100} y1={left[1] * 100} x2={right[0] * 100} y2={right[1] * 100} stroke={color} strokeWidth="1.35" strokeLinecap="round" />,
        ] : [];
    });
    const qaStatus = String(manifest.qa?.status || "warning");
    const qaKey = qaStatus === "passed"
        ? "web.workbench.canvas.media.motionPassed"
        : qaStatus === "failed"
            ? "web.workbench.canvas.media.motionFailed"
            : "web.workbench.canvas.media.motionWarning";
    const timeBase = manifest.source?.timeBase || {};
    const numerator = Number(timeBase.numerator) || 0;
    const denominator = Number(timeBase.denominator) || 1;
    const precision = Math.min(9, Math.max(3, String(denominator).length));
    const seconds = (frame.ptsTicks * numerator / denominator).toFixed(precision);
    return (
        <div className="flex h-full w-full flex-col bg-neutral-950 text-white">
            <div className="relative min-h-0 flex-1 overflow-hidden">
                <svg viewBox="0 0 100 100" className="h-full w-full" role="img" aria-label={resource.name}>
                    <rect width="100" height="100" fill="#09090b" />
                    <g opacity="0.3"><path d="M50 0V100M0 50H100" stroke="#3f3f46" strokeWidth="0.35" /></g>
                    {renderConnections(frame.pose || [], POSE_CONNECTIONS, "#a78bfa")}
                    {renderConnections(frame.leftHand || [], HAND_CONNECTIONS, "#22d3ee")}
                    {renderConnections(frame.rightHand || [], HAND_CONNECTIONS, "#f472b6")}
                </svg>
                <span className={`absolute right-2 top-2 rounded px-1.5 py-0.5 text-[9px] ${qaStatus === "passed" ? "bg-emerald-500/80" : qaStatus === "failed" ? "bg-rose-500/85" : "bg-amber-500/85"}`}>{t(qaKey)}</span>
            </div>
            {!compact ? (
                <div className="border-t border-white/10 px-3 py-2">
                    <input aria-label={t("web.workbench.canvas.media.motionFrame", { current: frameIndex + 1, total: frameCount })} type="range" min={0} max={Math.max(0, frameCount - 1)} step={1} value={frameIndex} onChange={(event) => setFrameIndex(Number(event.currentTarget.value))} className="h-4 w-full accent-violet-400" />
                    <div className="flex items-center justify-between text-[10px] text-white/65"><span>{t("web.workbench.canvas.media.motionFrame", { current: frameIndex + 1, total: frameCount })}</span><span>{seconds}s</span></div>
                </div>
            ) : null}
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
    const originalUrl = String(resource.url || "").trim();
    const proxyUrl = String(resource.previewUrl || "").trim();
    const usesOriginalResource = kind === "model_3d" || kind === "motion";
    const url = String(usesOriginalResource || inspect ? originalUrl || proxyUrl : proxyUrl || originalUrl).trim();
    const cacheKey = `${kind}:${url}`;
    const rootRef = useRef<HTMLDivElement | null>(null);
    const playableRef = useRef<HTMLMediaElement | null>(null);
    const [playbackState, setPlaybackState] = useState({ cacheKey, playing: false });
    const [modelPreviewKey, setModelPreviewKey] = useState("");
    const [inView, setInView] = useState(false);
    const documentVisible = useSyncExternalStore(
        subscribeDocumentVisibility,
        documentVisibleSnapshot,
        () => true,
    );
    const effectiveVisible = visible && (inspect || inView) && documentVisible;
    const [resourceState, setResourceState] = useState<{ cacheKey: string; state: "loading" | "ready" | "error" }>(() => ({
        cacheKey,
        state: hasReadyResource(cacheKey) ? "ready" : "loading",
    }));
    const playing = playbackState.cacheKey === cacheKey && playbackState.playing;
    const loadState = resourceState.cacheKey === cacheKey
        ? resourceState.state
        : hasReadyResource(cacheKey) ? "ready" : "loading";
    const setPlaying = useCallback((value: boolean) => setPlaybackState({ cacheKey, playing: value }), [cacheKey]);
    const setLoadState = useCallback((state: "loading" | "ready" | "error") => setResourceState({ cacheKey, state }), [cacheKey]);
    useEffect(() => {
        const element = rootRef.current;
        if (!element || typeof IntersectionObserver === "undefined") {
            setInView(true);
            return;
        }
        const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { rootMargin: compact ? "160px" : "320px" });
        observer.observe(element);
        return () => observer.disconnect();
    }, [compact]);
    useEffect(() => {
        const media = playableRef.current;
        if (!media) return;
        if (!active || !effectiveVisible || !documentVisible) media.pause();
        else if (media.readyState === HTMLMediaElement.HAVE_NOTHING) media.load();
    }, [active, documentVisible, effectiveVisible]);
    const markReady = () => {
        rememberReadyResource(cacheKey);
        setLoadState("ready");
    };
    const togglePlayback = () => {
        const media = playableRef.current;
        if (!media) return;
        if (media.paused) void media.play();
        else media.pause();
    };
    if (kind === "motion") return <div ref={rootRef} className="h-full w-full"><MotionSkeleton resource={resource} compact={compact} visible={effectiveVisible} /></div>;
    if (!url) return <FileFallback resource={resource} compact={compact} />;
    if (kind === "image" || kind === "psd") {
        return (
            <div ref={rootRef} className="relative h-full w-full">
                <div className="absolute inset-0"><FileFallback resource={resource} compact={compact} /></div>
                {!effectiveVisible || loadState === "error" ? <FileFallback resource={resource} compact={compact} /> : (
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
                    {!effectiveVisible || loadState === "error" ? <FileFallback resource={resource} compact={compact} /> : (
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
                {!effectiveVisible || loadState === "error" ? <FileFallback resource={resource} compact={compact} /> : (
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
        if (inspect) return <div ref={rootRef} className="flex h-full w-full items-center justify-center">{effectiveVisible ? <audio src={url} controls preload="metadata" onLoadedMetadata={markReady} onError={() => setLoadState("error")} className="w-[88%]" /> : <FileFallback resource={resource} compact={compact} />}</div>;
        return (
            <div ref={rootRef} className="relative h-full w-full">
                <FileFallback resource={resource} compact={compact} />
                {effectiveVisible ? <audio ref={(element) => { playableRef.current = element; }} src={url} preload="metadata" onLoadedMetadata={markReady} onError={() => setLoadState("error")} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} /> : null}
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
        if (supported) {
            const previewRequested = inspect || modelPreviewKey === cacheKey;
            const shouldRenderModel = effectiveVisible && !compact && (inspect || (active && previewRequested));
            return (
                <div ref={rootRef} className="relative h-full w-full">
                    {shouldRenderModel ? <ModelViewer src={url} className="h-full w-full rounded-none border-0" compact={!inspect} active interactive={inspect} /> : <FileFallback resource={resource} compact={compact} />}
                    {!compact && active && effectiveVisible && !previewRequested ? (
                        <button
                            type="button"
                            onPointerDown={(event) => event.stopPropagation()}
                            onClick={(event) => { event.stopPropagation(); setModelPreviewKey(cacheKey); }}
                            className="absolute left-1/2 top-1/2 flex h-9 -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 rounded-full bg-black/70 px-3 text-[10px] font-semibold text-white shadow-lg hover:bg-black/85"
                        >
                            <Box className="h-3.5 w-3.5" />{t("web.workbench.canvas.media.load3d")}
                        </button>
                    ) : null}
                </div>
            );
        }
        return <div ref={rootRef} className="h-full w-full"><FileFallback resource={resource} compact={compact} /></div>;
    }
    return <FileFallback resource={resource} compact={compact} />;
}

export function creativeCanvasMediaType(resource: CreativeCanvasMediaResource) {
    return kindOf(resource);
}
