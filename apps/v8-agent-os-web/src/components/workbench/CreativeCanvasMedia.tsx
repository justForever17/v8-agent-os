"use client";

/* eslint-disable @next/next/no-img-element -- session resources include local and signed URLs without fixed dimensions */

import dynamic from "next/dynamic";
import { Box, FileText, ImageIcon, Loader2, Music2, Video } from "lucide-react";

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
    if (kind === "video" && !compact) {
        return (
            <video
                src={url}
                controls={inspect}
                muted={!inspect}
                playsInline
                preload="metadata"
                onLoadedMetadata={(event) => {
                    if (inspect) return;
                    onDimensions?.({
                        width: event.currentTarget.videoWidth,
                        height: event.currentTarget.videoHeight,
                    });
                }}
                className="h-full w-full object-contain"
            />
        );
    }
    if (kind === "audio" && !compact) {
        return inspect
            ? <audio src={url} controls preload="metadata" className="w-[88%]" />
            : <FileFallback resource={resource} compact={compact} />;
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
