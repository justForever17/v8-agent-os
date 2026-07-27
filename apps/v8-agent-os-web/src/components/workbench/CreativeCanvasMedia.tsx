"use client";

/* eslint-disable @next/next/no-img-element -- session resources include local and signed URLs without fixed dimensions */

import dynamic from "next/dynamic";
import { useRef, useState } from "react";
import { Box, FileText, ImageIcon, Loader2, Music2, Pause, Play, Video } from "lucide-react";

export type CreativeCanvasMediaResource = {
    name: string;
    mimeType: string;
    mediaType?: string;
    url?: string;
};

const ModelViewer = dynamic(
    () => import("@/components/chat/ModelViewer").then((module) => module.ModelViewer),
    {
        ssr: false,
        loading: () => (
            <div className="flex h-full w-full items-center justify-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在加载 3D 预览…
            </div>
        ),
    },
);

function kindOf(resource: CreativeCanvasMediaResource) {
    const explicit = String(resource.mediaType || "").toLowerCase();
    if (explicit && explicit !== "unknown") return explicit;
    const mime = String(resource.mimeType || "").toLowerCase();
    const name = String(resource.name || "").toLowerCase();
    if (mime.startsWith("image/")) return "image";
    if (mime.startsWith("video/")) return "video";
    if (mime.startsWith("audio/")) return "audio";
    if (mime.includes("gltf") || /\.(?:glb|gltf|obj|fbx|stl|usd|usdz)$/.test(name)) return "model_3d";
    if (mime.startsWith("text/") || mime.includes("json") || mime.includes("pdf")) return "document";
    return "file";
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
    onDimensions,
}: {
    resource: CreativeCanvasMediaResource;
    compact?: boolean;
    inspect?: boolean;
    onDimensions?: (dimensions: { width: number; height: number }) => void;
}) {
    const kind = kindOf(resource);
    const url = String(resource.url || "").trim();
    const playableRef = useRef<HTMLMediaElement | null>(null);
    const [playing, setPlaying] = useState(false);
    const togglePlayback = () => {
        const media = playableRef.current;
        if (!media) return;
        if (media.paused) void media.play();
        else media.pause();
    };
    if (!url) return <FileFallback resource={resource} compact={compact} />;
    if (kind === "image") {
        return (
            <img
                src={url}
                alt={resource.name}
                draggable={false}
                loading="lazy"
                onLoad={(event) => {
                    if (compact || inspect) return;
                    onDimensions?.({
                        width: event.currentTarget.naturalWidth,
                        height: event.currentTarget.naturalHeight,
                    });
                }}
                className="h-full w-full object-contain"
            />
        );
    }
    if (kind === "video") {
        if (inspect) {
            return <video src={url} controls playsInline preload="metadata" className="h-full w-full object-contain" />;
        }
        return (
            <div className="relative h-full w-full">
                <video
                    ref={(element) => { playableRef.current = element; }}
                    src={url}
                    muted
                    playsInline
                    preload="auto"
                    onPlay={() => setPlaying(true)}
                    onPause={() => setPlaying(false)}
                    onEnded={() => setPlaying(false)}
                    onLoadedMetadata={(event) => {
                        if (compact) return;
                        onDimensions?.({ width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight });
                    }}
                    className="pointer-events-none h-full w-full object-contain"
                />
                {!compact ? (
                    <button type="button" onClick={togglePlayback} className="absolute left-1/2 top-1/2 grid h-10 w-10 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/65 text-white shadow-lg hover:bg-black/80" aria-label={playing ? "暂停视频" : "播放视频"}>
                        {playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
                    </button>
                ) : null}
            </div>
        );
    }
    if (kind === "audio" && !compact) {
        if (inspect) return <audio src={url} controls preload="metadata" className="w-[88%]" />;
        return (
            <div className="relative h-full w-full">
                <FileFallback resource={resource} compact={compact} />
                <audio ref={(element) => { playableRef.current = element; }} src={url} preload="metadata" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />
                <button type="button" onClick={togglePlayback} className="absolute left-1/2 top-1/2 grid h-10 w-10 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/65 text-white shadow-lg hover:bg-black/80" aria-label={playing ? "暂停音频" : "播放音频"}>
                    {playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
                </button>
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
