"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Circle, Eraser, RotateCcw, Trash2, X } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";

export type CreativeCanvasMaskPoint = { x: number; y: number };
export type CreativeCanvasMaskStroke = {
    id: string;
    mode: "paint" | "erase";
    size: number;
    points: CreativeCanvasMaskPoint[];
};
export type CreativeCanvasMaskState = {
    revision: number;
    strokes: CreativeCanvasMaskStroke[];
    frozenSourceIds?: string[];
    sourceWidth?: number;
    sourceHeight?: number;
};

function clamp(value: number, minimum = 0, maximum = 1) {
    return Math.min(maximum, Math.max(minimum, value));
}

function strokePath(stroke: CreativeCanvasMaskStroke) {
    if (!stroke.points.length) return "";
    return stroke.points.map((point, index) => `${index ? "L" : "M"} ${point.x * 1000} ${point.y * 1000}`).join(" ");
}

export async function rasterizeCreativeCanvasMask(
    mask: CreativeCanvasMaskState,
    dimensions: { width: number; height: number },
): Promise<Blob> {
    const width = Math.max(1, Math.min(8192, Math.round(dimensions.width)));
    const height = Math.max(1, Math.min(8192, Math.round(dimensions.height)));
    const canvas = window.document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("浏览器无法创建蒙版画布。");
    // GPT Image edits use transparent mask pixels as the editable region.
    // Start fully opaque (preserve everything), then paint by removing alpha;
    // erasing a brush stroke restores opacity. PNG serialization keeps the
    // required alpha channel and the source image dimensions.
    context.globalCompositeOperation = "source-over";
    context.fillStyle = "rgba(0,0,0,1)";
    context.fillRect(0, 0, width, height);
    context.lineCap = "round";
    context.lineJoin = "round";
    for (const stroke of mask.strokes) {
        if (!stroke.points.length) continue;
        context.globalCompositeOperation = stroke.mode === "paint" ? "destination-out" : "source-over";
        context.strokeStyle = "rgba(0,0,0,1)";
        context.fillStyle = "rgba(0,0,0,1)";
        context.lineWidth = Math.max(1, stroke.size * Math.min(width, height));
        const first = stroke.points[0];
        if (stroke.points.length === 1) {
            context.beginPath();
            context.arc(first.x * width, first.y * height, context.lineWidth / 2, 0, Math.PI * 2);
            context.fill();
            continue;
        }
        context.beginPath();
        context.moveTo(first.x * width, first.y * height);
        for (const point of stroke.points.slice(1)) context.lineTo(point.x * width, point.y * height);
        context.stroke();
    }
    context.globalCompositeOperation = "source-over";
    return new Promise<Blob>((resolve, reject) => {
        canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("蒙版快照生成失败。")), "image/png");
    });
}

export function CreativeCanvasMaskOverlay({ mask }: { mask?: CreativeCanvasMaskState }) {
    if (!mask?.strokes.length) return null;
    return (
        <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
            {mask.strokes.map((stroke) => (
                <path
                    key={stroke.id}
                    d={strokePath(stroke)}
                    fill="none"
                    stroke={stroke.mode === "paint" ? "rgba(244,63,94,.58)" : "rgba(255,255,255,.72)"}
                    strokeWidth={stroke.size * 1000}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeDasharray={stroke.mode === "erase" ? "8 8" : undefined}
                />
            ))}
        </svg>
    );
}

export function CreativeCanvasMaskEditor({
    src,
    title,
    value,
    disabled,
    onChange,
    onClose,
    onUse,
}: {
    src: string;
    title: string;
    value?: CreativeCanvasMaskState;
    disabled?: boolean;
    onChange: (value: CreativeCanvasMaskState) => void;
    onClose: () => void;
    onUse: (value: CreativeCanvasMaskState, dimensions: { width: number; height: number }) => void;
}) {
    const t = useT();
    const surfaceRef = useRef<HTMLDivElement | null>(null);
    const viewportRef = useRef<HTMLDivElement | null>(null);
    const imageRef = useRef<HTMLImageElement | null>(null);
    const drawingRef = useRef<CreativeCanvasMaskStroke | null>(null);
    const [mode, setMode] = useState<"paint" | "erase">("paint");
    const [size, setSize] = useState(0.045);
    const [draft, setDraft] = useState<CreativeCanvasMaskState>(value || { revision: 0, strokes: [] });
    const [surfaceSize, setSurfaceSize] = useState<{ width: number; height: number } | null>(null);

    const fitSurface = useCallback(() => {
        const viewport = viewportRef.current;
        const image = imageRef.current;
        if (!viewport || !image?.naturalWidth || !image.naturalHeight) return;
        const bounds = viewport.getBoundingClientRect();
        const aspect = image.naturalWidth / image.naturalHeight;
        let width = Math.max(1, bounds.width - 32);
        let height = width / aspect;
        if (height > bounds.height - 32) {
            height = Math.max(1, bounds.height - 32);
            width = height * aspect;
        }
        setSurfaceSize({ width, height });
    }, []);

    useEffect(() => {
        const viewport = viewportRef.current;
        if (!viewport || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(fitSurface);
        observer.observe(viewport);
        const frame = window.requestAnimationFrame(fitSurface);
        return () => {
            window.cancelAnimationFrame(frame);
            observer.disconnect();
        };
    }, [fitSurface]);

    const commit = (next: CreativeCanvasMaskState) => {
        setDraft(next);
        onChange(next);
    };

    const pointFromEvent = (event: React.PointerEvent) => {
        const rect = surfaceRef.current?.getBoundingClientRect();
        if (!rect) return { x: 0, y: 0 };
        return {
            x: clamp((event.clientX - rect.left) / Math.max(1, rect.width)),
            y: clamp((event.clientY - rect.top) / Math.max(1, rect.height)),
        };
    };

    const hasPaint = useMemo(
        () => draft.strokes.some((stroke) => stroke.mode === "paint" && stroke.points.length > 0),
        [draft.strokes],
    );

    return (
        <div data-canvas-wheel-isolation className="absolute inset-5 z-50 flex min-h-0 flex-col overflow-hidden rounded-[22px] border border-white/70 bg-background/94 shadow-[0_28px_90px_rgba(15,23,42,.22)] backdrop-blur-xl dark:border-white/10">
            <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border/60 px-3">
                <span className="min-w-0 flex-1 truncate text-xs font-semibold">{t("web.workbench.canvas.mask.title", { title })}</span>
                <span className="text-[10px] text-muted-foreground">{t("web.workbench.canvas.mask.revision", { revision: draft.revision })}</span>
                <button type="button" onClick={onClose} className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.workbench.canvas.mask.close")}><X className="h-4 w-4" /></button>
            </div>
            <div ref={viewportRef} className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-[linear-gradient(45deg,hsl(var(--muted))_25%,transparent_25%),linear-gradient(-45deg,hsl(var(--muted))_25%,transparent_25%),linear-gradient(45deg,transparent_75%,hsl(var(--muted))_75%),linear-gradient(-45deg,transparent_75%,hsl(var(--muted))_75%)] bg-[length:20px_20px] bg-[position:0_0,0_10px,10px_-10px,-10px_0] p-4">
                <div
                    ref={surfaceRef}
                    onPointerDown={(event) => {
                        if (disabled || event.button !== 0) return;
                        event.currentTarget.setPointerCapture(event.pointerId);
                        const stroke: CreativeCanvasMaskStroke = {
                            id: `mask-stroke-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                            mode,
                            size,
                            points: [pointFromEvent(event)],
                        };
                        drawingRef.current = stroke;
                        setDraft((current) => ({ ...current, strokes: [...current.strokes.slice(-63), stroke] }));
                    }}
                    onPointerMove={(event) => {
                        const currentStroke = drawingRef.current;
                        if (!currentStroke || disabled) return;
                        if (currentStroke.points.length >= 512) return;
                        const point = pointFromEvent(event);
                        const previous = currentStroke.points.at(-1);
                        if (previous && Math.hypot(point.x - previous.x, point.y - previous.y) < 0.003) return;
                        currentStroke.points = [...currentStroke.points, point];
                        setDraft((current) => ({
                            ...current,
                            strokes: current.strokes.map((stroke) => stroke.id === currentStroke.id ? { ...currentStroke } : stroke),
                        }));
                    }}
                    onPointerUp={(event) => {
                        if (!drawingRef.current) return;
                        event.currentTarget.releasePointerCapture(event.pointerId);
                        drawingRef.current = null;
                        setDraft((current) => {
                            const next = { ...current, revision: current.revision + 1 };
                            onChange(next);
                            return next;
                        });
                    }}
                    style={surfaceSize ? { width: surfaceSize.width, height: surfaceSize.height } : { width: "100%", height: "100%" }}
                    className={cn("relative touch-none select-none overflow-hidden rounded-xl bg-black/80 shadow-2xl", disabled && "cursor-not-allowed")}
                >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img ref={imageRef} src={src} alt={title} draggable={false} onLoad={fitSurface} className="pointer-events-none h-full w-full object-contain" />
                    <CreativeCanvasMaskOverlay mask={draft} />
                </div>
            </div>
            <div className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-2xl border border-white/70 bg-background/88 p-1.5 shadow-xl backdrop-blur-xl dark:border-white/10">
                <button type="button" disabled={disabled} onClick={() => setMode("paint")} className={cn("flex h-8 items-center gap-1.5 rounded-xl px-2.5 text-[11px]", mode === "paint" ? "bg-foreground text-background" : "hover:bg-muted")}><Circle className="h-3.5 w-3.5 fill-current" />{t("web.workbench.canvas.mask.paint")}</button>
                <button type="button" disabled={disabled} onClick={() => setMode("erase")} className={cn("flex h-8 items-center gap-1.5 rounded-xl px-2.5 text-[11px]", mode === "erase" ? "bg-foreground text-background" : "hover:bg-muted")}><Eraser className="h-3.5 w-3.5" />{t("web.workbench.canvas.mask.erase")}</button>
                <label className="flex items-center gap-2 px-2 text-[10px] text-muted-foreground">{t("web.workbench.canvas.mask.brush")}<input type="range" min="1" max="16" value={Math.round(size * 100)} disabled={disabled} onChange={(event) => setSize(Number(event.target.value) / 100)} className="w-20 accent-foreground" /></label>
                <span className="mx-0.5 h-5 w-px bg-border" />
                <button type="button" disabled={disabled || !draft.strokes.length} onClick={() => commit({ ...draft, revision: draft.revision + 1, strokes: draft.strokes.slice(0, -1) })} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.mask.undoStroke")}><RotateCcw className="h-3.5 w-3.5" /></button>
                <button type="button" disabled={disabled || !draft.strokes.length} onClick={() => commit({ ...draft, revision: draft.revision + 1, strokes: [] })} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-destructive disabled:opacity-30" aria-label={t("web.workbench.canvas.mask.clear")}><Trash2 className="h-3.5 w-3.5" /></button>
                <button type="button" disabled={disabled || !hasPaint} onClick={() => {
                    const dimensions = {
                        width: imageRef.current?.naturalWidth || draft.sourceWidth || 1024,
                        height: imageRef.current?.naturalHeight || draft.sourceHeight || 1024,
                    };
                    const next = { ...draft, sourceWidth: dimensions.width, sourceHeight: dimensions.height };
                    commit(next);
                    onUse(next, dimensions);
                }} className="ml-1 flex h-8 items-center gap-1.5 rounded-xl bg-primary px-3 text-[11px] font-semibold text-primary-foreground disabled:opacity-35"><Check className="h-3.5 w-3.5" />{t("web.workbench.canvas.mask.use")}</button>
            </div>
        </div>
    );
}
