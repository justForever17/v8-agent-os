"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Pause, Play, Scissors } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { mediaTypeOf, recordOf } from "./serialization";
import {
    canvasTimelineSeconds,
    formatCanvasTimelineSeconds,
    isValidCanvasTimeRange,
    reconcileCanvasTimeRange,
} from "./timeline";
import type { CanvasResource, CanvasTimeRange } from "./types";

export function CanvasTimeRangeEditor({
    sessionId,
    resource,
    range,
    mode,
    onChange,
}: {
    sessionId: string;
    resource: CanvasResource | null;
    range: CanvasTimeRange;
    mode: "frame" | "range";
    onChange: (range: CanvasTimeRange) => void;
}) {
    const t = useT();
    const mediaRef = useRef<HTMLMediaElement | null>(null);
    const onChangeRef = useRef(onChange);
    const rangeRef = useRef(range);
    const scrubTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const pendingScrubSecondsRef = useRef<number | null>(null);
    const playheadSecondsRef = useRef(0);
    const seekTokenRef = useRef(0);
    const videoFrameCallbackRef = useRef<{ element: HTMLVideoElement; callbackId: number } | null>(null);
    const [draftRange, setDraftRange] = useState(range);
    const [mediaDuration, setMediaDuration] = useState(0);
    const [playheadSeconds, setPlayheadSeconds] = useState(0);
    const [isPlaying, setIsPlaying] = useState(false);
    const [indexingExact, setIndexingExact] = useState(false);
    const draftRangeRef = useRef(range);
    useEffect(() => {
        onChangeRef.current = onChange;
    }, [onChange]);
    useEffect(() => {
        rangeRef.current = range;
    }, [range]);
    useEffect(() => {
        // The persisted Graph range may be hydrated after this editor mounts.
        // A card only persists canonical execution fields (fingerprint + selected
        // indices), not a potentially huge list of every frame boundary.  Do not
        // replace a locally hydrated range with that compact snapshot while this
        // card remains mounted.
        const current = draftRangeRef.current;
        const currentIsHydrated = current.count > 0 && Boolean(current.unit) && Boolean(current.probeFingerprint);
        const incomingIsHydrated = range.count > 0 && Boolean(range.unit) && Boolean(range.probeFingerprint);
        if (!incomingIsHydrated && currentIsHydrated) return;
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setDraftRange(range);
        draftRangeRef.current = range;
    }, [
        range.count,
        range.endIndexExclusive,
        range.error,
        range.exact,
        range.loading,
        range.probeFingerprint,
        range.startIndex,
        range.unit,
    ]);
    const mediaType = resource ? mediaTypeOf(resource) : "unknown";
    const duration = Number(draftRange.durationSeconds) || 0;
    const previewDuration = Math.max(duration, mediaDuration);
    const formatPreviewSeconds = (seconds: number) => `${seconds
        .toFixed(Math.max(3, draftRange.displayPrecision))
        .replace(/0+$/, "")
        .replace(/\.$/, "")}s`;
    const selectionStart = draftRange.count ? Math.min(100, (draftRange.startIndex / draftRange.count) * 100) : 0;
    const selectionEnd = draftRange.count
        ? Math.min(100, ((mode === "frame" ? draftRange.startIndex + 1 : draftRange.endIndexExclusive) / draftRange.count) * 100)
        : 0;

    useEffect(() => {
        // Reset local playback telemetry when the selected governed resource changes.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setMediaDuration(0);
        setPlayheadSeconds(0);
        setIsPlaying(false);
        playheadSecondsRef.current = 0;
        setIndexingExact(false);
    }, [resource?.id, resource?.origin]);

    useEffect(() => () => {
        if (scrubTimerRef.current) clearTimeout(scrubTimerRef.current);
        const pendingFrame = videoFrameCallbackRef.current;
        if (pendingFrame && typeof pendingFrame.element.cancelVideoFrameCallback === "function") {
            pendingFrame.element.cancelVideoFrameCallback(pendingFrame.callbackId);
        }
    }, []);

    useEffect(() => {
        const currentRange = rangeRef.current;
        if (!resource || !currentRange.loading) return;
        const controller = new AbortController();
        const reference = resource.origin === "source"
            ? { sourceId: resource.id }
            : resource.origin === "artifact"
                ? { artifactId: resource.id }
                : { workspaceAssetId: resource.id };
        const applyProbe = (payload: Record<string, unknown>) => {
            const timeline = recordOf(payload?.timeline);
            const timeBase = recordOf(timeline.timeBase);
            const averageFrameRate = recordOf(timeline.averageFrameRate);
            const unit = timeline.unit === "frame" ? "frame" : timeline.unit === "sample" ? "sample" : undefined;
            const count = Number(timeline.count);
            const approximate = timeline.approximate === true;
            const boundaryTicks = Array.isArray(timeline.boundaryTicks)
                ? timeline.boundaryTicks.map(Number).filter(Number.isFinite)
                : undefined;
            if (!unit || !Number.isInteger(count) || count <= 0 || (unit === "frame" && !approximate && boundaryTicks?.length !== count + 1)) {
                throw new Error(t("web.workbench.canvas.timeline.metadataError"));
            }
            if (mode === "frame" && unit !== "frame") {
                throw new Error(t("web.workbench.canvas.timeline.metadataError"));
            }
            const previous = draftRangeRef.current;
            const nextRange: CanvasTimeRange = {
                unit,
                count,
                startIndex: 0,
                endIndexExclusive: count,
                durationSeconds: String(timeline.durationSeconds || "0"),
                timeBaseNumerator: Number(timeBase.numerator) || 1,
                timeBaseDenominator: Number(timeBase.denominator) || 1,
                averageFrameRateNumerator: Number(averageFrameRate.numerator) || undefined,
                averageFrameRateDenominator: Number(averageFrameRate.denominator) || undefined,
                boundaryTicks,
                probeFingerprint: String(payload?.fingerprint || ""),
                displayPrecision: Math.max(3, Math.min(9, Number(timeline.displayPrecision) || 6)),
                exact: !approximate,
                loading: false,
            };
            const reconciledRange = reconcileCanvasTimeRange(previous, nextRange, mode);
            setDraftRange(reconciledRange);
            draftRangeRef.current = reconciledRange;
            onChangeRef.current(reconciledRange);
        };
        const loadProbe = async () => {
            try {
                const requestProbe = async (detail?: "preview" | "sparse") => {
                    const response = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/media/probe`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(detail ? { ...reference, detail } : reference),
                        signal: controller.signal,
                    });
                    const payload = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
                    return recordOf(payload);
                };
                const previewPayload = await requestProbe("preview");
                applyProbe(previewPayload);
                if (recordOf(previewPayload.timeline).approximate === true) {
                    setIndexingExact(true);
                    try {
                        applyProbe(await requestProbe("sparse"));
                    } catch (reason) {
                        if (controller.signal.aborted) throw reason;
                    }
                    applyProbe(await requestProbe());
                }
            } catch (reason) {
                if (!controller.signal.aborted) {
                    const failed = {
                        ...draftRangeRef.current,
                        loading: false,
                        error: reason instanceof Error ? reason.message : String(reason),
                    };
                    setDraftRange(failed);
                    draftRangeRef.current = failed;
                    onChangeRef.current(failed);
                }
            } finally {
                if (!controller.signal.aborted) setIndexingExact(false);
            }
        };
        void loadProbe();
        return () => controller.abort();
    }, [mode, resource?.id, resource?.origin, sessionId, t]);

    const seekPreviewSeconds = (rawSeconds: number, exact: boolean) => {
        const media = mediaRef.current;
        if (!media) return;
        const maximum = Number.isFinite(media.duration) && media.duration > 0 ? media.duration : previewDuration;
        const seconds = Math.max(0, Math.min(rawSeconds, maximum || rawSeconds));
        const token = seekTokenRef.current + 1;
        seekTokenRef.current = token;
        media.pause();
        playheadSecondsRef.current = seconds;
        setPlayheadSeconds(seconds);
        const fastSeek = (media as HTMLMediaElement & { fastSeek?: (time: number) => void }).fastSeek;
        if (!exact && typeof fastSeek === "function") fastSeek.call(media, seconds);
        else media.currentTime = seconds;
        if (media instanceof HTMLVideoElement && typeof media.requestVideoFrameCallback === "function") {
            const pendingFrame = videoFrameCallbackRef.current;
            if (pendingFrame && typeof pendingFrame.element.cancelVideoFrameCallback === "function") {
                pendingFrame.element.cancelVideoFrameCallback(pendingFrame.callbackId);
            }
            const callbackId = media.requestVideoFrameCallback((_now, metadata) => {
                if (seekTokenRef.current !== token) return;
                const renderedSeconds = Number(metadata.mediaTime);
                if (!Number.isFinite(renderedSeconds)) return;
                playheadSecondsRef.current = renderedSeconds;
                setPlayheadSeconds(renderedSeconds);
                videoFrameCallbackRef.current = null;
            });
            videoFrameCallbackRef.current = { element: media, callbackId };
        }
    };
    const queueScrubPreview = (seconds: number) => {
        pendingScrubSecondsRef.current = seconds;
        playheadSecondsRef.current = seconds;
        setPlayheadSeconds(seconds);
        if (scrubTimerRef.current) return;
        scrubTimerRef.current = setTimeout(() => {
            scrubTimerRef.current = null;
            const pending = pendingScrubSecondsRef.current;
            pendingScrubSecondsRef.current = null;
            if (pending !== null) seekPreviewSeconds(pending, false);
        }, 48);
    };
    const commitScrubPreview = (seconds = playheadSecondsRef.current) => {
        if (scrubTimerRef.current) {
            clearTimeout(scrubTimerRef.current);
            scrubTimerRef.current = null;
        }
        pendingScrubSecondsRef.current = null;
        seekPreviewSeconds(seconds, true);
    };
    const seekPreview = (kind: "start" | "end", current: CanvasTimeRange) => {
        if (!current.unit) return;
        const index = current.unit === "frame" && kind === "end"
            ? Math.max(current.startIndex, current.endIndexExclusive - 1)
            : kind === "start" ? current.startIndex : current.endIndexExclusive;
        commitScrubPreview(canvasTimelineSeconds(current, index));
    };
    const setBoundary = (kind: "start" | "end", rawValue: number, commit = true, previewWhileDragging = false) => {
        const current = draftRangeRef.current;
        const value = Math.round(rawValue);
        let next: CanvasTimeRange;
        if (kind === "start") {
            const maxStart = Math.max(0, current.endIndexExclusive - 1);
            next = { ...current, startIndex: Math.max(0, Math.min(value, maxStart)), error: undefined };
        } else {
            next = {
                ...current,
                endIndexExclusive: Math.min(current.count, Math.max(current.startIndex + 1, value)),
                error: undefined,
            };
        }
        setDraftRange(next);
        draftRangeRef.current = next;
        if (commit) {
            onChangeRef.current(next);
            seekPreview(kind, next);
        } else if (previewWhileDragging) {
            const previewIndex = next.unit === "frame" && kind === "end"
                ? Math.max(next.startIndex, next.endIndexExclusive - 1)
                : kind === "start" ? next.startIndex : next.endIndexExclusive;
            queueScrubPreview(canvasTimelineSeconds(next, previewIndex));
        }
    };
    const commitDraftRange = (kind: "start" | "end") => {
        const current = draftRangeRef.current;
        onChangeRef.current(current);
        seekPreview(kind, current);
    };
    const setFromCurrent = (kind: "start" | "end") => {
        const currentTime = mediaRef.current?.currentTime;
        const current = draftRangeRef.current;
        if (!Number.isFinite(currentTime) || !current.unit) return;
        const target = Number(currentTime);
        if (current.unit === "sample") {
            setBoundary(kind, Math.round(target * current.timeBaseDenominator / Math.max(1, current.timeBaseNumerator)));
        } else {
            const boundaries = current.boundaryTicks || [];
            let low = 0;
            let high = Math.max(0, boundaries.length - 1);
            const targetTicks = target * current.timeBaseDenominator / Math.max(1, current.timeBaseNumerator);
            while (low < high) {
                const middle = Math.floor((low + high) / 2);
                if ((boundaries[middle] || 0) < targetTicks) low = middle + 1;
                else high = middle;
            }
            const previous = Math.max(0, low - 1);
            const nearest = Math.abs((boundaries[low] || 0) - targetTicks) < Math.abs((boundaries[previous] || 0) - targetTicks) ? low : previous;
            setBoundary(kind, nearest);
        }
    };
    const togglePlayback = () => {
        const media = mediaRef.current;
        if (!media) return;
        if (media.paused) {
            void media.play().catch(() => setIsPlaying(false));
        } else {
            media.pause();
        }
    };
    const preview = resource?.url && mediaType === "video" ? (
        <video
            ref={(element) => { mediaRef.current = element; }}
            src={resource.url}
            playsInline
            preload="metadata"
            onClick={togglePlayback}
            onLoadedMetadata={(event) => {
                const nextDuration = Number(event.currentTarget.duration) || 0;
                setMediaDuration(nextDuration);
            }}
            onPlay={() => setIsPlaying(true)}
            onPause={() => setIsPlaying(false)}
            onEnded={() => setIsPlaying(false)}
            onTimeUpdate={(event) => {
                if (event.currentTarget.seeking) return;
                const seconds = Number(event.currentTarget.currentTime) || 0;
                playheadSecondsRef.current = seconds;
                setPlayheadSeconds(seconds);
            }}
            className="max-h-40 w-full cursor-pointer rounded-xl bg-black object-contain"
        />
    ) : resource?.url && mediaType === "audio" ? (
        <audio
            ref={(element) => { mediaRef.current = element; }}
            src={resource.url}
            preload="metadata"
            onLoadedMetadata={(event) => {
                const nextDuration = Number(event.currentTarget.duration) || 0;
                setMediaDuration(nextDuration);
            }}
            onPlay={() => setIsPlaying(true)}
            onPause={() => setIsPlaying(false)}
            onEnded={() => setIsPlaying(false)}
            onTimeUpdate={(event) => {
                if (event.currentTarget.seeking) return;
                const seconds = Number(event.currentTarget.currentTime) || 0;
                playheadSecondsRef.current = seconds;
                setPlayheadSeconds(seconds);
            }}
            className="w-full"
        />
    ) : null;

    return (
        <div className="space-y-3">
            {preview}
            {preview && previewDuration > 0 ? (
                <div className="flex items-center gap-2 rounded-xl border border-border/70 bg-background/80 px-3 py-2 shadow-sm">
                    <button type="button" onClick={togglePlayback} className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={isPlaying ? t("web.workbench.canvas.timeline.pause") : t("web.workbench.canvas.timeline.play")}>
                        {isPlaying ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3" />}
                    </button>
                    <span className="w-[84px] shrink-0 font-mono text-[9px] tabular-nums text-foreground">{formatPreviewSeconds(playheadSeconds)}</span>
                    <span className="min-w-0 flex-1 text-center text-[9px] text-muted-foreground">{t(mode === "frame" ? "web.workbench.canvas.timeline.frameSelection" : "web.workbench.canvas.timeline.selection")}</span>
                    <span className="w-[84px] shrink-0 text-right font-mono text-[9px] tabular-nums text-muted-foreground">{formatPreviewSeconds(previewDuration)}</span>
                </div>
            ) : null}
            <div className="rounded-xl border border-border/70 bg-muted/20 p-3">
                <div className="mb-3 flex items-center gap-2 text-[10px] text-muted-foreground">
                    {draftRange.loading || indexingExact ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Scissors className="h-3.5 w-3.5" />}
                    <span>{draftRange.loading || indexingExact
                        ? t("web.workbench.canvas.timeline.loading")
                        : t(mode === "frame" ? "web.workbench.canvas.timeline.frameSelection" : "web.workbench.canvas.timeline.selection")}</span>
                    {duration ? <span className="ml-auto font-mono">{draftRange.durationSeconds}s</span> : null}
                </div>
                {duration ? (
                    <div className="relative mb-3 h-2 rounded-full bg-muted">
                        <span
                            className="absolute inset-y-0 rounded-full bg-violet-500"
                            style={{ left: `${selectionStart}%`, width: `${Math.max(0, selectionEnd - selectionStart)}%` }}
                        />
                    </div>
                ) : null}
                {mode === "frame" ? (
                    <>
                        <label className="space-y-1.5 text-[10px] font-medium">
                            <span>{t("web.workbench.canvas.timeline.frame")}</span>
                            <input type="number" min={0} max={Math.max(0, draftRange.count - 1)} step={1} value={draftRange.startIndex} disabled={draftRange.loading || !draftRange.count} onChange={(event) => setBoundary("start", Number(event.target.value), false)} onBlur={() => commitDraftRange("start")} onKeyDown={(event) => { if (event.key === "Enter") commitDraftRange("start"); }} className="h-9 w-full rounded-lg border border-border/70 bg-background px-2 font-mono text-xs outline-none focus:border-violet-400 disabled:opacity-50" />
                            <span className="block font-mono text-[9px] text-muted-foreground">{draftRange.loading ? t("web.workbench.canvas.timeline.loading") : `${formatCanvasTimelineSeconds(draftRange, draftRange.startIndex)} · F${draftRange.startIndex}`}</span>
                            {draftRange.count ? <input type="range" min={0} max={Math.max(0, draftRange.count - 1)} step={1} value={draftRange.startIndex} onInput={(event) => setBoundary("start", Number(event.currentTarget.value), false, true)} onPointerUp={() => commitDraftRange("start")} onKeyUp={() => commitDraftRange("start")} onBlur={() => commitDraftRange("start")} className="w-full accent-violet-600" /> : null}
                        </label>
                        <div className="mt-2 flex items-center gap-2">
                            <button type="button" onClick={() => setFromCurrent("start")} disabled={!preview || draftRange.loading || !draftRange.count} className="rounded-lg border border-border/70 px-2 py-1 text-[9px] hover:bg-muted disabled:opacity-40">{t("web.workbench.canvas.timeline.setFrame")}</button>
                        </div>
                    </>
                ) : (
                    <>
                        <div className="grid grid-cols-2 gap-3">
                            <label className="space-y-1.5 text-[10px] font-medium">
                                <span>{t("web.workbench.canvas.timeline.start")}</span>
                                <input type="number" min={0} max={Math.max(0, draftRange.endIndexExclusive - 1)} step={1} value={draftRange.startIndex} disabled={draftRange.loading || !draftRange.count} onChange={(event) => setBoundary("start", Number(event.target.value), false)} onBlur={() => commitDraftRange("start")} onKeyDown={(event) => { if (event.key === "Enter") commitDraftRange("start"); }} className="h-9 w-full rounded-lg border border-border/70 bg-background px-2 font-mono text-xs outline-none focus:border-violet-400 disabled:opacity-50" />
                                <span className="block font-mono text-[9px] text-muted-foreground">{draftRange.loading ? t("web.workbench.canvas.timeline.loading") : `${formatCanvasTimelineSeconds(draftRange, draftRange.startIndex)} · ${draftRange.unit === "frame" ? `F${draftRange.startIndex}` : `S${draftRange.startIndex}`}`}</span>
                                {draftRange.count ? <input type="range" min={0} max={Math.max(0, draftRange.endIndexExclusive - 1)} step={1} value={draftRange.startIndex} onInput={(event) => setBoundary("start", Number(event.currentTarget.value), false, true)} onPointerUp={() => commitDraftRange("start")} onKeyUp={() => commitDraftRange("start")} onBlur={() => commitDraftRange("start")} className="w-full accent-violet-600" /> : null}
                            </label>
                            <label className="space-y-1.5 text-[10px] font-medium">
                                <span>{t("web.workbench.canvas.timeline.end")}</span>
                                <input type="number" min={draftRange.startIndex + 1} max={draftRange.count || undefined} step={1} value={draftRange.endIndexExclusive} disabled={draftRange.loading || !draftRange.count} onChange={(event) => setBoundary("end", Number(event.target.value), false)} onBlur={() => commitDraftRange("end")} onKeyDown={(event) => { if (event.key === "Enter") commitDraftRange("end"); }} className="h-9 w-full rounded-lg border border-border/70 bg-background px-2 font-mono text-xs outline-none focus:border-violet-400 disabled:opacity-50" />
                                <span className="block font-mono text-[9px] text-muted-foreground">{draftRange.loading ? t("web.workbench.canvas.timeline.loading") : `${formatCanvasTimelineSeconds(draftRange, draftRange.endIndexExclusive)} · ${draftRange.unit === "frame" ? `F${draftRange.endIndexExclusive}` : `S${draftRange.endIndexExclusive}`}`}</span>
                                {draftRange.count ? <input type="range" min={draftRange.startIndex + 1} max={draftRange.count} step={1} value={draftRange.endIndexExclusive} onInput={(event) => setBoundary("end", Number(event.currentTarget.value), false, true)} onPointerUp={() => commitDraftRange("end")} onKeyUp={() => commitDraftRange("end")} onBlur={() => commitDraftRange("end")} className="w-full accent-violet-600" /> : null}
                            </label>
                        </div>
                        <div className="mt-2 flex items-center gap-2">
                            <button type="button" onClick={() => setFromCurrent("start")} disabled={!preview || draftRange.loading || !draftRange.count} className="rounded-lg border border-border/70 px-2 py-1 text-[9px] hover:bg-muted disabled:opacity-40">{t("web.workbench.canvas.timeline.setStart")}</button>
                            <button type="button" onClick={() => setFromCurrent("end")} disabled={!preview || draftRange.loading || !draftRange.count} className="rounded-lg border border-border/70 px-2 py-1 text-[9px] hover:bg-muted disabled:opacity-40">{t("web.workbench.canvas.timeline.setEnd")}</button>
                            {isValidCanvasTimeRange(draftRange) ? <span className="ml-auto text-[9px] text-muted-foreground">{t("web.workbench.canvas.timeline.selected")} {(canvasTimelineSeconds(draftRange, draftRange.endIndexExclusive) - canvasTimelineSeconds(draftRange, draftRange.startIndex)).toFixed(draftRange.displayPrecision).replace(/0+$/, "").replace(/\.$/, "")}s</span> : null}
                        </div>
                    </>
                )}
                {draftRange.error ? <div className="mt-2 text-[9px] text-red-500">{draftRange.error}</div> : null}
            </div>
        </div>
    );
}
