"use client";

/* eslint-disable @next/next/no-img-element -- Canvas versions use governed, session-bound media URLs. */

import {
    Check,
    CircleX,
    Columns2,
    Download,
    Info,
    Loader2,
    Music2,
    Pause,
    PanelLeft,
    Play,
    RefreshCw,
    Send,
    Volume2,
    X,
} from "lucide-react";
import {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
    type ReactNode,
} from "react";

import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";

import type {
    CanvasActionDefinition,
    CanvasGraphRuntime,
    CanvasNode,
    CanvasResource,
    CanvasOutputVersion,
    CanvasOutputVersionProof,
    CanvasDeliveryProjection,
} from "./types";

export type CanvasInspectorMode = "details" | "review";

export type CanvasReviewVersion = {
    identity: string;
    resultNodeId: string;
    version: CanvasOutputVersion;
    resource: CanvasResource;
};

function outputVersionProof(version: CanvasOutputVersion | null | undefined): CanvasOutputVersionProof | null {
    const proof = version?.proof || version?.outputProof;
    return proof && typeof proof === "object" ? proof : null;
}

function outputVersionState(version: CanvasOutputVersion | null | undefined) {
    const proof = outputVersionProof(version);
    const raw = String(proof?.status || version?.status || "").trim().toLowerCase();
    if (raw === "completed") return "succeeded";
    if (["queued", "running", "cancelling", "succeeded", "failed", "cancelled", "waiting", "interrupted"].includes(raw)) return raw;
    return proof?.available === false ? "unavailable" : proof ? "ready" : "unavailable";
}

function outputVersionValue(value: unknown, unavailable: string) {
    if (value === null || value === undefined || String(value).trim() === "") return unavailable;
    return String(value);
}

export type CanvasInspectorInput = {
    label: string;
    mediaType: string;
};

type InspectorAction = {
    label: string;
    definition: CanvasActionDefinition;
    configured: boolean;
    runtimeState: Record<string, unknown>;
};

function mediaKind(resource: CanvasResource | null | undefined) {
    const explicit = String(resource?.mediaType || "").toLowerCase();
    const mime = String(resource?.mimeType || "").toLowerCase();
    if (explicit && explicit !== "unknown") return explicit;
    if (mime.startsWith("image/")) return "image";
    if (mime.startsWith("video/")) return "video";
    if (mime.startsWith("audio/")) return "audio";
    return "unknown";
}

function mediaLabelKey(value: string) {
    const normalized = String(value || "unknown").toLowerCase();
    return `web.workbench.canvas.inspector.media.${[
        "image", "video", "audio", "model_3d", "psd", "document", "text", "motion", "mask", "metadata",
    ].includes(normalized) ? normalized : "unknown"}`;
}

function statusTone(status: string) {
    if (["succeeded", "completed", "ready"].includes(status)) {
        return "border-emerald-300/60 bg-emerald-500/10 text-emerald-700 dark:border-emerald-500/20 dark:text-emerald-300";
    }
    if (["failed", "interrupted"].includes(status)) {
        return "border-red-300/60 bg-red-500/10 text-red-700 dark:border-red-500/20 dark:text-red-300";
    }
    if (["queued", "running", "cancelling", "waiting"].includes(status)) {
        return "border-amber-300/60 bg-amber-500/10 text-amber-800 dark:border-amber-500/20 dark:text-amber-200";
    }
    return "border-border/70 bg-muted/45 text-muted-foreground";
}

function runtimeStatusKey(status: string) {
    const normalized = status.toLowerCase();
    if (["queued", "running", "cancelling", "succeeded", "failed", "cancelled", "waiting", "idle"].includes(normalized)) {
        return `web.workbench.canvas.graph.state.${normalized}`;
    }
    if (normalized === "completed") return "web.workbench.canvas.graph.state.succeeded";
    if (normalized === "interrupted") return "web.workbench.canvas.inspector.state.interrupted";
    if (normalized === "unavailable") return "web.workbench.canvas.inspector.state.unavailable";
    return "web.workbench.canvas.graph.state.idle";
}

function formattedTime(seconds: number) {
    if (!Number.isFinite(seconds) || seconds < 0) return "00:00";
    const whole = Math.floor(seconds);
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    const remaining = whole % 60;
    return hours
        ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`
        : `${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
}

function versionLabel(version: CanvasReviewVersion) {
    return `v${version.version.version}`;
}

function CanvasReviewSelector({
    label,
    value,
    versions,
    onChange,
}: {
    label: string;
    value: string;
    versions: CanvasReviewVersion[];
    onChange: (identity: string) => void;
}) {
    const selectedIndex = Math.max(0, versions.findIndex((candidate) => candidate.identity === value));
    return (
        <label className="flex min-w-0 items-center gap-2 text-[10px] font-semibold text-muted-foreground">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-foreground text-[9px] text-background">{label}</span>
            <select
                data-canvas-review-selector={label}
                value={String(selectedIndex)}
                onChange={(event) => onChange(versions[Number(event.currentTarget.value)]?.identity || value)}
                className="h-8 min-w-0 flex-1 rounded-lg border border-border/70 bg-background px-2 text-[10px] font-medium text-foreground outline-none focus:border-violet-400"
            >
                {versions.map((candidate, index) => (
                    <option key={candidate.identity} value={String(index)}>{versionLabel(candidate)}</option>
                ))}
            </select>
        </label>
    );
}

function useSynchronizedMediaPair(kind: "video" | "audio") {
    const mediaARef = useRef<HTMLMediaElement | null>(null);
    const mediaBRef = useRef<HTMLMediaElement | null>(null);
    const syncingRef = useRef(false);
    const [playing, setPlaying] = useState(false);
    const [duration, setDuration] = useState(0);
    const [position, setPosition] = useState(0);
    const [audition, setAudition] = useState<"a" | "b">("a");
    const [mediaState, setMediaState] = useState<{ a: "loading" | "ready" | "error"; b: "loading" | "ready" | "error" }>({ a: "loading", b: "loading" });

    const updateDuration = useCallback(() => {
        const durations = [mediaARef.current?.duration, mediaBRef.current?.duration]
            .filter((value): value is number => typeof value === "number" && Number.isFinite(value) && value > 0);
        setDuration(durations.length === 2 ? Math.min(...durations) : Math.max(0, ...durations));
    }, []);

    const seek = useCallback((next: number) => {
        const bounded = Math.max(0, Math.min(duration || next, next));
        syncingRef.current = true;
        for (const media of [mediaARef.current, mediaBRef.current]) {
            if (media && Number.isFinite(media.duration)) media.currentTime = Math.min(bounded, media.duration);
        }
        syncingRef.current = false;
        setPosition(bounded);
    }, [duration]);

    const syncFrom = useCallback((source: "a" | "b") => {
        if (syncingRef.current) return;
        const leader = source === "a" ? mediaARef.current : mediaBRef.current;
        const follower = source === "a" ? mediaBRef.current : mediaARef.current;
        if (!leader) return;
        const next = Math.min(duration || leader.currentTime, leader.currentTime);
        setPosition(next);
        if (follower && Math.abs(follower.currentTime - next) > 0.12) {
            syncingRef.current = true;
            follower.currentTime = Math.min(next, Number.isFinite(follower.duration) ? follower.duration : next);
            syncingRef.current = false;
        }
    }, [duration]);

    const toggle = useCallback(async () => {
        const pair = ([
            ["a", mediaARef.current],
            ["b", mediaBRef.current],
        ] as const).filter((entry): entry is ["a" | "b", HTMLMediaElement] => Boolean(entry[1]) && mediaState[entry[0]] !== "error");
        if (playing) {
            pair.forEach(([, media]) => media.pause());
            setPlaying(false);
            return;
        }
        const outcomes = await Promise.allSettled(pair.map(([, media]) => media.play()));
        setPlaying(outcomes.some((outcome) => outcome.status === "fulfilled"));
    }, [mediaState, playing]);

    const ended = useCallback((side: "a" | "b") => {
        const other = side === "a" ? mediaBRef.current : mediaARef.current;
        if (!other || other.paused || other.ended) setPlaying(false);
    }, []);

    useEffect(() => {
        const mediaA = mediaARef.current;
        const mediaB = mediaBRef.current;
        if (mediaA) mediaA.muted = audition !== "a";
        if (mediaB) mediaB.muted = audition !== "b";
    }, [audition, kind]);

    useEffect(() => () => {
        syncingRef.current = true;
        for (const media of [mediaARef.current, mediaBRef.current]) {
            if (!media) continue;
            media.pause();
            media.removeAttribute("src");
            media.load();
        }
        mediaARef.current = null;
        mediaBRef.current = null;
    }, []);

    return {
        audition,
        bindA: (value: HTMLMediaElement | null) => { mediaARef.current = value; },
        bindB: (value: HTMLMediaElement | null) => { mediaBRef.current = value; },
        duration,
        playing,
        position,
        seek,
        setAudition,
        setMediaState: (side: "a" | "b", state: "loading" | "ready" | "error") => setMediaState((current) => ({ ...current, [side]: state })),
        mediaState,
        syncFrom,
        toggle,
        updateDuration,
        ended,
    };
}

export function CanvasSynchronizedMediaReview({
    kind,
    left,
    right,
}: {
    kind: "video" | "audio";
    left: CanvasReviewVersion;
    right: CanvasReviewVersion;
}) {
    const t = useT();
    const controls = useSynchronizedMediaPair(kind);
    const setRef = (side: "a" | "b", value: HTMLMediaElement | null) => {
        if (side === "a") controls.bindA(value);
        else controls.bindB(value);
    };
    const renderMedia = (candidate: CanvasReviewVersion, side: "a" | "b") => {
        const url = String(candidate.resource.url || "").trim();
        const state = controls.mediaState[side];
        const unavailable = !url;
        const effectiveState = unavailable ? "error" : state;
        return (
        <div data-canvas-media-side={side} data-canvas-media-state={effectiveState} className="relative flex min-h-0 flex-col overflow-hidden rounded-lg border border-white/10 bg-neutral-950 text-white">
            <div className={cn("relative min-h-0 flex-1", kind === "video" ? "aspect-video" : "grid min-h-40 place-items-center")}>
                {kind === "video" && url ? (
                    <video
                        ref={(value) => setRef(side, value)}
                        src={url}
                        preload="metadata"
                        playsInline
                        onLoadedMetadata={() => { controls.setMediaState(side, "ready"); controls.updateDuration(); }}
                        onCanPlay={() => controls.setMediaState(side, "ready")}
                        onTimeUpdate={() => controls.syncFrom(side)}
                        onError={() => controls.setMediaState(side, "error")}
                        onEnded={() => controls.ended(side)}
                        className="h-full w-full object-contain"
                    />
                ) : kind === "audio" && url ? (
                    <>
                        <audio
                            ref={(value) => setRef(side, value)}
                            src={url}
                            preload="metadata"
                            onLoadedMetadata={() => { controls.setMediaState(side, "ready"); controls.updateDuration(); }}
                            onCanPlay={() => controls.setMediaState(side, "ready")}
                            onTimeUpdate={() => controls.syncFrom(side)}
                            onError={() => controls.setMediaState(side, "error")}
                            onEnded={() => controls.ended(side)}
                        />
                        <Music2 className="h-10 w-10 text-white/55" />
                    </>
                ) : <div className="flex h-full min-h-40 items-center justify-center px-4 text-center text-xs text-white/65">{t(unavailable ? "web.workbench.canvas.review.mediaUnavailable" : "web.workbench.canvas.review.mediaUnsupported")}</div>}
                {effectiveState === "error" && url ? <div className="absolute inset-x-3 bottom-3 rounded-md bg-red-950/85 px-2 py-1.5 text-center text-[10px] text-red-100">{t("web.workbench.canvas.review.mediaLoadError")}</div> : null}
                <span className="absolute left-2 top-2 rounded-md bg-black/70 px-2 py-1 text-[10px] font-semibold">{side.toUpperCase()} · {versionLabel(candidate)}</span>
            </div>
            <div className="truncate border-t border-white/10 px-3 py-2 text-[10px] text-white/65">{candidate.resource.name}</div>
        </div>
        );
    };
    return (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
            <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 md:grid-cols-2">
                {renderMedia(left, "a")}
                {renderMedia(right, "b")}
            </div>
            <div className="shrink-0 rounded-lg border border-border/70 bg-background/90 p-3">
                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        onClick={() => void controls.toggle()}
                        className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-foreground text-background hover:opacity-85"
                        aria-label={t(controls.playing ? "web.workbench.canvas.review.pause" : "web.workbench.canvas.review.play")}
                    >
                        {controls.playing ? <Pause className="h-4 w-4 fill-current" /> : <Play className="ml-0.5 h-4 w-4 fill-current" />}
                    </button>
                    <span className="w-11 text-right text-[10px] tabular-nums text-muted-foreground">{formattedTime(controls.position)}</span>
                    <input
                        type="range"
                        min={0}
                        max={Math.max(0.001, controls.duration)}
                        step={0.01}
                        value={Math.min(controls.position, controls.duration || controls.position)}
                        onChange={(event) => controls.seek(Number(event.currentTarget.value))}
                        aria-label={t("web.workbench.canvas.review.timeline")}
                        className="h-5 min-w-0 flex-1 accent-violet-600"
                    />
                    <span className="w-11 text-[10px] tabular-nums text-muted-foreground">{formattedTime(controls.duration)}</span>
                    {(
                        <div className="flex shrink-0 items-center rounded-lg border border-border/70 p-0.5" aria-label={t("web.workbench.canvas.review.audition")}>
                            <Volume2 className="mx-1 h-3.5 w-3.5 text-muted-foreground" />
                            {(["a", "b"] as const).map((side) => (
                                <button key={side} data-canvas-audition={side} type="button" onClick={() => controls.setAudition(side)} className={cn("h-7 w-7 rounded-md text-[9px] font-semibold uppercase", controls.audition === side ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted")}>{side}</button>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

export function CanvasABReview({ resultNodeId, versions }: { resultNodeId: string; versions: CanvasReviewVersion[] }) {
    const t = useT();
    const scopedVersions = useMemo(
        () => versions.filter((candidate) => candidate.resultNodeId === resultNodeId),
        [resultNodeId, versions],
    );
    const [leftIdentity, setLeftIdentity] = useState("");
    const [rightIdentity, setRightIdentity] = useState("");
    const [imageMode, setImageMode] = useState<"side" | "wipe">("side");
    const [wipePosition, setWipePosition] = useState(50);

    const left = scopedVersions.find((candidate) => candidate.identity === leftIdentity) || scopedVersions[0];
    const right = scopedVersions.find((candidate) => candidate.identity === rightIdentity) || scopedVersions[1] || scopedVersions[0];
    const leftKind = mediaKind(left?.resource);
    const rightKind = mediaKind(right?.resource);
    const comparableKind = leftKind === rightKind && ["image", "video", "audio"].includes(leftKind) ? leftKind : "unsupported";

    if (!left || !right) {
        return <div className="grid min-h-64 place-items-center text-sm text-muted-foreground">{t("web.workbench.canvas.review.needVersions")}</div>;
    }
    return (
        <div data-canvas-ab-review className="flex min-h-0 flex-1 flex-col gap-3 p-3 sm:p-4">
            <div className="grid shrink-0 grid-cols-2 gap-2">
                <CanvasReviewSelector label="A" value={left.identity} versions={scopedVersions} onChange={setLeftIdentity} />
                <CanvasReviewSelector label="B" value={right.identity} versions={scopedVersions} onChange={setRightIdentity} />
            </div>
            {comparableKind === "image" ? (
                <div className="flex min-h-0 flex-1 flex-col gap-2">
                    <div className="flex shrink-0 justify-end">
                        <div className="flex rounded-lg border border-border/70 bg-background p-0.5">
                            <button type="button" onClick={() => setImageMode("side")} className={cn("grid h-7 w-7 place-items-center rounded-md", imageMode === "side" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted")} aria-label={t("web.workbench.canvas.review.sideBySide")} title={t("web.workbench.canvas.review.sideBySide")}><Columns2 className="h-3.5 w-3.5" /></button>
                            <button type="button" onClick={() => setImageMode("wipe")} className={cn("grid h-7 w-7 place-items-center rounded-md", imageMode === "wipe" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted")} aria-label={t("web.workbench.canvas.review.wipe")} title={t("web.workbench.canvas.review.wipe")}><PanelLeft className="h-3.5 w-3.5" /></button>
                        </div>
                    </div>
                    {imageMode === "side" ? (
                        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 md:grid-cols-2">
                            {[left, right].map((candidate, index) => (
                                <div key={`${index}:${candidate.identity}`} className="relative min-h-64 overflow-hidden rounded-lg border border-white/10 bg-neutral-950">
                                    {candidate.resource.url ? <img src={candidate.resource.url} alt={candidate.resource.name} className="h-full w-full object-contain" /> : <div className="flex h-full items-center justify-center px-4 text-center text-xs text-white/65">{t("web.workbench.canvas.review.mediaUnavailable")}</div>}
                                    <span className="absolute left-2 top-2 rounded-md bg-black/70 px-2 py-1 text-[10px] font-semibold text-white">{index ? "B" : "A"} · {versionLabel(candidate)}</span>
                                    <span className="absolute inset-x-0 bottom-0 truncate bg-black/65 px-3 py-2 text-[10px] text-white/70">{candidate.resource.name}</span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="relative min-h-64 flex-1 overflow-hidden rounded-lg border border-white/10 bg-neutral-950">
                            {left.resource.url ? <img src={left.resource.url} alt={left.resource.name} className="absolute inset-0 h-full w-full object-contain" /> : <div className="absolute inset-0 flex items-center justify-center px-4 text-center text-xs text-white/65">{t("web.workbench.canvas.review.mediaUnavailable")}</div>}
                            <div className="absolute inset-0 overflow-hidden" style={{ clipPath: `inset(0 ${100 - wipePosition}% 0 0)` }}>
                                {right.resource.url ? <img src={right.resource.url} alt={right.resource.name} className="h-full w-full object-contain" /> : <div className="flex h-full items-center justify-center px-4 text-center text-xs text-white/65">{t("web.workbench.canvas.review.mediaUnavailable")}</div>}
                            </div>
                            <div className="pointer-events-none absolute inset-y-0 w-px bg-white shadow-[0_0_0_1px_rgba(0,0,0,.45)]" style={{ left: `${wipePosition}%` }} />
                            <span className="absolute left-2 top-2 rounded-md bg-black/70 px-2 py-1 text-[10px] font-semibold text-white">B · {versionLabel(right)}</span>
                            <span className="absolute right-2 top-2 rounded-md bg-black/70 px-2 py-1 text-[10px] font-semibold text-white">A · {versionLabel(left)}</span>
                            <div className="absolute inset-x-3 bottom-3 rounded-lg bg-black/65 px-2 py-1.5">
                                <input type="range" min={0} max={100} step={1} value={wipePosition} onChange={(event) => setWipePosition(Number(event.currentTarget.value))} aria-label={t("web.workbench.canvas.review.wipePosition")} className="h-4 w-full accent-white" />
                            </div>
                        </div>
                    )}
                </div>
            ) : comparableKind === "video" || comparableKind === "audio" ? (
                <CanvasSynchronizedMediaReview key={`${left.identity}:${right.identity}`} kind={comparableKind} left={left} right={right} />
            ) : (
                <div className="grid min-h-64 flex-1 place-items-center rounded-lg border border-dashed border-border/70 bg-muted/20 px-6 text-center">
                    <div><Columns2 className="mx-auto mb-3 h-7 w-7 text-muted-foreground" /><p className="text-sm font-semibold">{t("web.workbench.canvas.review.unsupported")}</p></div>
                </div>
            )}
        </div>
    );
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
    return (
        <div className="grid grid-cols-[104px_minmax(0,1fr)] gap-3 border-b border-border/50 py-2.5 last:border-0">
            <dt className="text-[10px] font-medium text-muted-foreground">{label}</dt>
            <dd className="min-w-0 text-[10px] font-medium leading-4 text-foreground">{children}</dd>
        </div>
    );
}

function resourceOriginKey(resource: CanvasResource) {
    if (resource.origin === "workspace_asset") return "web.workbench.canvas.inspector.origin.workspace";
    if (resource.origin === "source") return "web.workbench.canvas.inspector.origin.source";
    return "web.workbench.canvas.inspector.origin.artifact";
}

function reviewDecisionKey(decision: string) {
    const normalized = String(decision || "pending").toLowerCase();
    return `web.workbench.canvas.review.decision.${["pending", "approved", "rejected"].includes(normalized) ? normalized : "pending"}`;
}

function deliveryStatusKey(status: string) {
    const normalized = String(status || "").toLowerCase();
    return `web.workbench.canvas.review.deliveryStatus.${["ready", "delivered", "blocked", "unavailable"].includes(normalized) ? normalized : "pending"}`;
}

function CanvasOutputReviewControls({
    version,
    busy,
    error,
    delivery,
    onReviewChange,
    onDryRunDelivery,
    onConfirmDelivery,
}: {
    version: CanvasReviewVersion;
    busy: boolean;
    error?: string;
    delivery?: CanvasDeliveryProjection | null;
    onReviewChange?: (version: CanvasReviewVersion, decision: "pending" | "approved" | "rejected", note: string, selectedForDelivery: boolean) => void;
    onDryRunDelivery?: (version: CanvasReviewVersion) => void;
    onConfirmDelivery?: (version: CanvasReviewVersion) => void;
}) {
    const t = useT();
    const review = version.version.review || {};
    const decision = ["pending", "approved", "rejected"].includes(String(review.decision || "pending"))
        ? String(review.decision || "pending") as "pending" | "approved" | "rejected"
        : "pending";
    const selected = Boolean(review.selectedForDelivery);
    const [note, setNote] = useState(String(review.note || ""));

    const submit = (nextDecision: "pending" | "approved" | "rejected", nextSelected = selected) => {
        onReviewChange?.(version, nextDecision, note.trim(), nextSelected && nextDecision === "approved");
    };
    const deliveryStatus = String(delivery?.status || "").toLowerCase();
    const canDeliver = decision === "approved" && selected;
    const delivered = deliveryStatus === "delivered" || Boolean(review.deliveryManifestArtifactId || review.deliveredAt);
    const deliveryReason = String(delivery?.reason || delivery?.unavailableReason || "").trim();

    return (
        <section data-canvas-review-controls className="rounded-lg border border-border/70 bg-muted/10 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
                <h3 className="text-[11px] font-semibold">{t("web.workbench.canvas.review.controls")}</h3>
                <span data-canvas-review-decision={decision} className={cn("rounded-full border px-2 py-1 text-[9px] font-semibold", statusTone(decision === "approved" ? "ready" : decision === "rejected" ? "failed" : "idle"))}>
                    {t(reviewDecisionKey(decision))}
                </span>
            </div>
            <textarea
                data-canvas-review-note
                value={note}
                maxLength={1000}
                rows={3}
                onChange={(event) => setNote(event.currentTarget.value)}
                placeholder={t("web.workbench.canvas.review.notePlaceholder")}
                aria-label={t("web.workbench.canvas.review.note")}
                className="w-full resize-y rounded-lg border border-border/70 bg-background px-2.5 py-2 text-[10px] leading-4 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-400/15"
            />
            <div className="mt-2 flex flex-wrap gap-2">
                <button
                    type="button"
                    data-canvas-review-action="approve"
                    disabled={busy}
                    onClick={() => submit("approved")}
                    className="flex h-8 items-center gap-1.5 rounded-lg bg-emerald-600 px-2.5 text-[10px] font-semibold text-white hover:bg-emerald-700 disabled:opacity-45"
                >
                    {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                    {t("web.workbench.canvas.review.approve")}
                </button>
                <button
                    type="button"
                    data-canvas-review-action="reject"
                    disabled={busy}
                    onClick={() => submit("rejected", false)}
                    className="flex h-8 items-center gap-1.5 rounded-lg border border-red-300/70 px-2.5 text-[10px] font-semibold text-red-700 hover:bg-red-500/10 dark:border-red-500/30 dark:text-red-300 disabled:opacity-45"
                >
                    {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CircleX className="h-3.5 w-3.5" />}
                    {t("web.workbench.canvas.review.reject")}
                </button>
                <label className={cn("flex h-8 items-center gap-1.5 rounded-lg border border-border/70 px-2.5 text-[10px] font-semibold", (!onReviewChange || decision !== "approved" || busy) && "opacity-45")}>
                    <input
                        type="checkbox"
                        data-canvas-review-selected
                        checked={selected}
                        disabled={!onReviewChange || decision !== "approved" || busy}
                        onChange={(event) => submit(decision, event.currentTarget.checked)}
                        className="accent-violet-600"
                    />
                    {t("web.workbench.canvas.review.selectedForDelivery")}
                </label>
            </div>
            <div data-canvas-delivery className="mt-3 border-t border-border/60 pt-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                    <h4 className="text-[10px] font-semibold">{t("web.workbench.canvas.review.delivery")}</h4>
                    {deliveryStatus ? <span className={cn("rounded-full border px-2 py-1 text-[9px] font-semibold", statusTone(deliveryStatus))}>{t(deliveryStatusKey(deliveryStatus))}</span> : null}
                </div>
                {!canDeliver ? <p className="text-[10px] leading-4 text-muted-foreground">{t("web.workbench.canvas.review.deliveryBlocked")}</p> : delivered ? <p className="text-[10px] leading-4 text-emerald-700 dark:text-emerald-300">{t("web.workbench.canvas.review.deliveryCreated")}</p> : (
                    <div className="flex flex-wrap gap-2">
                        <button
                            type="button"
                            data-canvas-delivery-action="dry-run"
                            disabled={busy || deliveryStatus === "ready"}
                            onClick={() => onDryRunDelivery?.(version)}
                            className="flex h-8 items-center gap-1.5 rounded-lg border border-border/70 px-2.5 text-[10px] font-semibold hover:bg-muted disabled:opacity-45"
                        >
                            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Info className="h-3.5 w-3.5" />}
                            {t("web.workbench.canvas.review.checkDelivery")}
                        </button>
                        <button
                            type="button"
                            data-canvas-delivery-action="confirm"
                            disabled={busy || deliveryStatus !== "ready"}
                            onClick={() => onConfirmDelivery?.(version)}
                            className="flex h-8 items-center gap-1.5 rounded-lg bg-violet-600 px-2.5 text-[10px] font-semibold text-white hover:bg-violet-700 disabled:opacity-45"
                        >
                            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                            {t("web.workbench.canvas.review.confirmDelivery")}
                        </button>
                    </div>
                )}
                {deliveryReason ? <p className="mt-2 text-[10px] leading-4 text-amber-700 dark:text-amber-300">{deliveryReason}</p> : null}
            </div>
            {error ? <p data-canvas-review-error className="mt-2 rounded-md bg-red-500/10 px-2 py-1.5 text-[10px] leading-4 text-red-700 dark:text-red-300">{error}</p> : null}
        </section>
    );
}

export function CanvasInspectorReviewPanel({
    node,
    resource,
    versions,
    selectedVersion,
    mode,
    action,
    inputs,
    outputLabel,
    graphRuntime,
    renderPreview,
    onModeChange,
    onVersionChange,
    reviewBusy,
    reviewError,
    deliveryState,
    onReviewChange,
    onDryRunDelivery,
    onConfirmDelivery,
    onRetry,
    onClose,
}: {
    node: CanvasNode;
    resource: CanvasResource | null;
    versions: CanvasReviewVersion[];
    selectedVersion?: CanvasReviewVersion | null;
    mode: CanvasInspectorMode;
    action: InspectorAction | null;
    inputs: CanvasInspectorInput[];
    outputLabel: string;
    graphRuntime: CanvasGraphRuntime;
    renderPreview: (resource: CanvasResource) => ReactNode;
    onModeChange: (mode: CanvasInspectorMode) => void;
    onVersionChange?: (identity: string) => void;
    reviewBusy?: boolean;
    reviewError?: string;
    deliveryState?: CanvasDeliveryProjection | null;
    onReviewChange?: (version: CanvasReviewVersion, decision: "pending" | "approved" | "rejected", note: string, selectedForDelivery: boolean) => void;
    onDryRunDelivery?: (version: CanvasReviewVersion) => void;
    onConfirmDelivery?: (version: CanvasReviewVersion) => void;
    onRetry: () => void;
    onClose: () => void;
}) {
    const t = useT();
    const versionProof = outputVersionProof(selectedVersion?.version);
    const versionBound = Boolean(selectedVersion);
    const runtimeState = versionBound ? { state: outputVersionState(selectedVersion?.version) } : action?.runtimeState || {};
    const state = String(runtimeState.state || (action ? graphRuntime.status : resource ? "ready" : "idle")).toLowerCase();
    const remoteUncertain = versionBound ? false : Boolean(runtimeState.providerCancellationRemoteTaskMayContinue);
    const recoverable = versionBound ? false : Boolean(runtimeState.recoverable) && Boolean(graphRuntime.recovery?.canRetry);
    const unavailable = t("web.workbench.canvas.inspector.unavailable");
    const provider = versionBound
        ? outputVersionValue(versionProof?.providerLabel || versionProof?.provider, unavailable)
        : action?.definition.providerLabel || t("web.workbench.canvas.inspector.providerRuntime");
    const model = versionBound
        ? outputVersionValue(versionProof?.modelLabel || versionProof?.model, unavailable)
        : action?.definition.modelLabel || t("web.workbench.canvas.inspector.modelRuntimeResolved");
    const readiness = versionBound
        ? versionProof?.available === false || !versionProof
            ? unavailable
            : t("web.workbench.canvas.inspector.readiness.available")
        : action
        ? state === "running" || state === "queued"
            ? t("web.workbench.canvas.inspector.readiness.running")
            : action.configured
                ? t("web.workbench.canvas.inspector.readiness.ready")
                : t("web.workbench.canvas.inspector.readiness.needsConfig")
        : resource
            ? t("web.workbench.canvas.inspector.readiness.available")
            : t("web.workbench.canvas.inspector.readiness.unavailable");
    const canReview = node.kind === "result" && versions.length > 0;
    const title = action?.label || resource?.name || node.title || t("web.workbench.canvas.inspector.title");

    return (
        <section
            data-canvas-inspector
            data-canvas-inspector-mode={mode}
            className={cn(
                "absolute z-50 flex min-h-0 flex-col overflow-hidden border border-white/80 bg-background shadow-[0_30px_100px_rgba(15,23,42,.28)] backdrop-blur-xl dark:border-white/10",
                mode === "review"
                    ? "inset-3 rounded-lg sm:inset-6"
                    : "bottom-3 right-3 top-14 w-[min(420px,calc(100%-24px))] rounded-lg",
            )}
        >
            <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border/60 px-3">
                <Info className="h-4 w-4 shrink-0 text-violet-600" />
                <span className="min-w-0 flex-1 truncate text-xs font-semibold">{title}</span>
                <div className="flex shrink-0 rounded-lg border border-border/70 p-0.5">
                    <button type="button" onClick={() => onModeChange("details")} className={cn("grid h-7 w-7 place-items-center rounded-md", mode === "details" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted")} aria-label={t("web.workbench.canvas.inspector.details")} title={t("web.workbench.canvas.inspector.details")}><Info className="h-3.5 w-3.5" /></button>
                    <button type="button" disabled={!canReview} onClick={() => onModeChange("review")} className={cn("grid h-7 w-7 place-items-center rounded-md", mode === "review" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted", !canReview && "opacity-30")} aria-label={t("web.workbench.canvas.review.title")} title={t(canReview ? "web.workbench.canvas.review.title" : "web.workbench.canvas.review.needVersions")}><Columns2 className="h-3.5 w-3.5" /></button>
                </div>
                {resource?.url ? <a href={resource.url} download={resource.name} className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.workbench.canvas.preview.download")}><Download className="h-4 w-4" /></a> : null}
                <button type="button" onClick={onClose} className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.workbench.canvas.preview.close")}><X className="h-4 w-4" /></button>
            </header>
            {mode === "review" ? (
                <div className="custom-scrollbar min-h-0 flex-1 overflow-y-auto">
                    <CanvasABReview resultNodeId={node.nodeId} versions={versions} />
                    {selectedVersion ? <div className="px-3 pb-3 sm:px-4 sm:pb-4"><CanvasOutputReviewControls
                        key={`${selectedVersion.identity}:${selectedVersion.version.review?.revision ?? 0}`}
                        version={selectedVersion}
                        busy={Boolean(reviewBusy)}
                        error={reviewError}
                        delivery={deliveryState}
                        onReviewChange={onReviewChange}
                        onDryRunDelivery={onDryRunDelivery}
                        onConfirmDelivery={onConfirmDelivery}
                    /></div> : null}
                </div>
            ) : (
                <div className="custom-scrollbar min-h-0 flex-1 overflow-y-auto">
                    {resource ? <div className="h-52 overflow-hidden border-b border-border/60 bg-black/5 p-2">{renderPreview(resource)}</div> : null}
                    <div className="space-y-4 p-3">
                        {node.kind === "result" && versions.length > 0 ? (
                            <label className="flex items-center gap-2 text-[10px] font-semibold text-muted-foreground">
                                <span className="shrink-0">{t("web.workbench.canvas.inspector.version")}</span>
                                <select
                                    value={selectedVersion?.identity || versions[0].identity}
                                    onChange={(event) => onVersionChange?.(event.currentTarget.value)}
                                    className="h-8 min-w-0 flex-1 rounded-lg border border-border/70 bg-background px-2 text-[10px] font-medium text-foreground outline-none focus:border-violet-400"
                                    aria-label={t("web.workbench.canvas.inspector.version")}
                                >
                                    {versions.map((candidate) => <option key={candidate.identity} value={candidate.identity}>{versionLabel(candidate)}</option>)}
                                </select>
                            </label>
                        ) : null}
                        <section>
                            <div className="mb-2 flex items-center justify-between gap-2">
                                <h3 className="text-[11px] font-semibold">{t("web.workbench.canvas.inspector.runtime")}</h3>
                                <span className={cn("rounded-full border px-2 py-1 text-[9px] font-semibold", statusTone(state))}>{t(runtimeStatusKey(state))}</span>
                            </div>
                            <dl className="rounded-lg border border-border/60 bg-muted/15 px-3">
                                {versionBound ? <>
                                    <DetailRow label={t("web.workbench.canvas.inspector.provider")}>{provider}</DetailRow>
                                    <DetailRow label={t("web.workbench.canvas.inspector.model")}>{model}</DetailRow>
                                    <DetailRow label={t("web.workbench.canvas.inspector.recipe")}>{outputVersionValue(versionProof?.recipeId || versionProof?.operation || versionProof?.operationKind, unavailable)}</DetailRow>
                                    <DetailRow label={t("web.workbench.canvas.inspector.elapsed")}>{versionProof?.elapsedMs !== undefined || versionProof?.durationMs !== undefined ? `${Number(versionProof.elapsedMs ?? versionProof.durationMs)} ms` : unavailable}</DetailRow>
                                    <DetailRow label={t("web.workbench.canvas.inspector.cost")}>{versionProof?.cost === undefined ? unavailable : `${String(versionProof.cost)}${versionProof.currency ? ` ${versionProof.currency}` : ""}`}</DetailRow>
                                    <DetailRow label={t("web.workbench.canvas.inspector.quality")}>{outputVersionValue((versionProof?.quality || versionProof?.qa) && typeof (versionProof?.quality || versionProof?.qa) === "object" ? ((versionProof?.quality || versionProof?.qa) as Record<string, unknown>).status : undefined, unavailable)}</DetailRow>
                                </> : action ? <>
                                    <DetailRow label={t("web.workbench.canvas.inspector.provider")}>{provider}</DetailRow>
                                    <DetailRow label={t("web.workbench.canvas.inspector.model")}>{model}</DetailRow>
                                </> : resource ? <DetailRow label={t("web.workbench.canvas.inspector.source")}>{t(resourceOriginKey(resource))}</DetailRow> : null}
                                <DetailRow label={t("web.workbench.canvas.inspector.readiness")}>{readiness}</DetailRow>
                                <DetailRow label={t("web.workbench.canvas.inspector.recovery")}>
                                    {remoteUncertain
                                        ? t("web.workbench.canvas.inspector.recovery.remoteUncertain")
                                        : recoverable
                                            ? t("web.workbench.canvas.inspector.recovery.retryReady")
                                            : state === "failed"
                                                ? t("web.workbench.canvas.inspector.recovery.blocked")
                                                : t("web.workbench.canvas.inspector.recovery.notNeeded")}
                                </DetailRow>
                            </dl>
                            {recoverable ? <button type="button" onClick={onRetry} className="mt-2 flex h-8 items-center gap-1.5 rounded-lg bg-foreground px-3 text-[10px] font-semibold text-background hover:opacity-85"><RefreshCw className="h-3.5 w-3.5" />{t("web.workbench.canvas.graph.retry")}</button> : null}
                        </section>
                        {selectedVersion ? <CanvasOutputReviewControls
                            key={`${selectedVersion.identity}:${selectedVersion.version.review?.revision ?? 0}`}
                            version={selectedVersion}
                            busy={Boolean(reviewBusy)}
                            error={reviewError}
                            delivery={deliveryState}
                            onReviewChange={onReviewChange}
                            onDryRunDelivery={onDryRunDelivery}
                            onConfirmDelivery={onConfirmDelivery}
                        /> : null}
                        <section>
                            <h3 className="mb-2 text-[11px] font-semibold">{t("web.workbench.canvas.inspector.flow")}</h3>
                            <dl className="rounded-lg border border-border/60 bg-muted/15 px-3">
                                <DetailRow label={t("web.workbench.canvas.inspector.inputs")}>
                                    {inputs.length ? <ul className="space-y-1">{inputs.map((input, index) => <li key={`${input.label}:${index}`} className="flex items-center gap-2"><span className="h-1.5 w-1.5 shrink-0 rounded-full bg-violet-500" /><span className="min-w-0 flex-1 truncate">{input.label}</span><span className="shrink-0 text-[9px] text-muted-foreground">{t(mediaLabelKey(input.mediaType))}</span></li>)}</ul> : t("web.workbench.canvas.inspector.noInputs")}
                                </DetailRow>
                                <DetailRow label={t("web.workbench.canvas.inspector.output")}>{outputLabel || t("web.workbench.canvas.inspector.awaitingOutput")}</DetailRow>
                                {resource ? <DetailRow label={t("web.workbench.canvas.inspector.format")}>{t(mediaLabelKey(mediaKind(resource)))}</DetailRow> : null}
                            </dl>
                        </section>
                        {canReview ? <button type="button" onClick={() => onModeChange("review")} className="flex h-9 w-full items-center justify-center gap-2 rounded-lg border border-border/70 bg-background text-[10px] font-semibold hover:bg-muted"><Columns2 className="h-4 w-4" />{t("web.workbench.canvas.review.open", { count: versions.length })}</button> : null}
                    </div>
                </div>
            )}
        </section>
    );
}
