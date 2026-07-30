"use client";

/* eslint-disable @next/next/no-img-element -- PSD previews are authenticated session resources */

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { ChevronDown, ChevronUp, Eye, EyeOff, Layers3, Loader2 } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";
import type { CreativeCanvasMediaResource } from "./CreativeCanvasMedia";

export type CanvasPsdCompositionLayer = {
    sourceNodeId: string;
    name: string;
    x: number;
    y: number;
    scalePercent: number;
    opacityPercent: number;
    visible: boolean;
    order: number;
    width?: number;
    height?: number;
};

export type CanvasPsdComposition = {
    canvas: { width: number; height: number; background: string };
    layers: CanvasPsdCompositionLayer[];
};

export type CanvasPsdLayerEdit = {
    layerPath: string;
    name?: string;
    visible?: boolean;
    opacityPercent?: number;
    x?: number;
    y?: number;
    order?: number;
    targetParentPath?: string;
};

type PsdManifestLayer = {
    layerPath: string;
    parentPath: string;
    index: number;
    name: string;
    kind: string;
    visible: boolean;
    opacityPercent: number;
    left: number;
    top: number;
    right: number;
    bottom: number;
    width: number;
    height: number;
    children: PsdManifestLayer[];
};

type PsdManifest = {
    width: number;
    height: number;
    layerCount: number;
    layers: PsdManifestLayer[];
};

type CompositionSource = { nodeId: string; resource: CreativeCanvasMediaResource };

function numberValue(value: string, fallback: number, minimum: number, maximum: number) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(minimum, Math.min(parsed, maximum)) : fallback;
}

function flattenLayers(items: PsdManifestLayer[], depth = 0): Array<PsdManifestLayer & { depth: number }> {
    return items.flatMap((item) => [{ ...item, depth }, ...flattenLayers(item.children || [], depth + 1)]);
}

export function CreativeCanvasPsdCompositionEditor({
    sources,
    value,
    onChange,
}: {
    sources: CompositionSource[];
    value: CanvasPsdComposition;
    onChange: (value: CanvasPsdComposition) => void;
}) {
    const t = useT();
    const stageRef = useRef<HTMLDivElement | null>(null);
    const dragRef = useRef<{ pointerId: number; sourceNodeId: string; startX: number; startY: number; x: number; y: number } | null>(null);
    const [selectedId, setSelectedId] = useState(() => value.layers.at(-1)?.sourceNodeId || "");
    const resources = useMemo(() => new Map(sources.map((item) => [item.nodeId, item.resource])), [sources]);
    const selected = value.layers.find((item) => item.sourceNodeId === selectedId) || value.layers.at(-1) || null;

    const updateLayer = (sourceNodeId: string, patch: Partial<CanvasPsdCompositionLayer>) => {
        onChange({
            ...value,
            layers: value.layers.map((layer) => layer.sourceNodeId === sourceNodeId ? { ...layer, ...patch } : layer),
        });
    };
    const moveLayer = (sourceNodeId: string, delta: number) => {
        const ordered = [...value.layers].sort((left, right) => left.order - right.order);
        const index = ordered.findIndex((item) => item.sourceNodeId === sourceNodeId);
        const target = Math.max(0, Math.min(ordered.length - 1, index + delta));
        if (index < 0 || index === target) return;
        const [layer] = ordered.splice(index, 1);
        ordered.splice(target, 0, layer);
        onChange({ ...value, layers: ordered.map((item, order) => ({ ...item, order })) });
    };
    const onLayerPointerDown = (event: ReactPointerEvent<HTMLButtonElement>, layer: CanvasPsdCompositionLayer) => {
        if (event.button !== 0) return;
        event.stopPropagation();
        event.currentTarget.setPointerCapture(event.pointerId);
        setSelectedId(layer.sourceNodeId);
        dragRef.current = { pointerId: event.pointerId, sourceNodeId: layer.sourceNodeId, startX: event.clientX, startY: event.clientY, x: layer.x, y: layer.y };
    };
    const onLayerPointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
        const drag = dragRef.current;
        const rect = stageRef.current?.getBoundingClientRect();
        if (!drag || drag.pointerId !== event.pointerId || !rect) return;
        updateLayer(drag.sourceNodeId, {
            x: Math.round(drag.x + (event.clientX - drag.startX) * value.canvas.width / Math.max(1, rect.width)),
            y: Math.round(drag.y + (event.clientY - drag.startY) * value.canvas.height / Math.max(1, rect.height)),
        });
    };

    return (
        <div className="space-y-2">
            <div className="flex items-center gap-2 px-1">
                <label className="flex items-center gap-1 text-[9px] text-muted-foreground">{t("web.workbench.canvas.psd.width")}<input type="number" min={1} max={32768} value={value.canvas.width} onChange={(event) => onChange({ ...value, canvas: { ...value.canvas, width: numberValue(event.target.value, value.canvas.width, 1, 32768) } })} className="h-7 w-20 rounded-lg border border-border/70 bg-background px-2 text-[10px] text-foreground" /></label>
                <label className="flex items-center gap-1 text-[9px] text-muted-foreground">{t("web.workbench.canvas.psd.height")}<input type="number" min={1} max={32768} value={value.canvas.height} onChange={(event) => onChange({ ...value, canvas: { ...value.canvas, height: numberValue(event.target.value, value.canvas.height, 1, 32768) } })} className="h-7 w-20 rounded-lg border border-border/70 bg-background px-2 text-[10px] text-foreground" /></label>
                <label className="ml-auto flex items-center gap-1 text-[9px] text-muted-foreground">{t("web.workbench.canvas.psd.background")}<input type="color" value={value.canvas.background === "transparent" ? "#ffffff" : value.canvas.background} onChange={(event) => onChange({ ...value, canvas: { ...value.canvas, background: event.target.value } })} className="h-7 w-8 rounded border-0 bg-transparent p-0" /></label>
                <button type="button" onClick={() => onChange({ ...value, canvas: { ...value.canvas, background: "transparent" } })} className={cn("h-7 rounded-lg px-2 text-[9px]", value.canvas.background === "transparent" ? "bg-violet-500/12 text-violet-700" : "bg-muted text-muted-foreground")}>{t("web.workbench.canvas.psd.transparent")}</button>
            </div>
            <div ref={stageRef} style={{ aspectRatio: `${value.canvas.width} / ${value.canvas.height}`, backgroundColor: value.canvas.background === "transparent" ? "rgba(148,163,184,.12)" : value.canvas.background }} className="relative max-h-[260px] min-h-[180px] w-full overflow-hidden rounded-xl border border-border/70">
                {[...value.layers].sort((left, right) => left.order - right.order).map((layer) => {
                    const resource = resources.get(layer.sourceNodeId);
                    const source = resource?.previewUrl || resource?.url;
                    return (
                        <button
                            key={layer.sourceNodeId}
                            type="button"
                            onPointerDown={(event) => onLayerPointerDown(event, layer)}
                            onPointerMove={onLayerPointerMove}
                            onPointerUp={() => { dragRef.current = null; }}
                            onPointerCancel={() => { dragRef.current = null; }}
                            style={{
                                left: `${layer.x / Math.max(1, value.canvas.width) * 100}%`,
                                top: `${layer.y / Math.max(1, value.canvas.height) * 100}%`,
                                width: `${Math.max(7, Math.min(70, 18 * layer.scalePercent / 100))}%`,
                                opacity: layer.visible ? Math.max(0.08, layer.opacityPercent / 100) : 0.16,
                                zIndex: layer.order + 1,
                            }}
                            className={cn("absolute touch-none overflow-hidden rounded border bg-background/70 shadow-sm", selectedId === layer.sourceNodeId ? "border-violet-500 ring-2 ring-violet-500/25" : "border-white/70")}
                            title={layer.name}
                        >
                            {source ? <img src={source} alt="" draggable={false} className="pointer-events-none h-auto w-full object-contain" /> : <Layers3 className="m-3 h-5 w-5 text-muted-foreground" />}
                        </button>
                    );
                })}
            </div>
            <div className="max-h-32 overflow-y-auto border-y border-border/50 py-1">
                {[...value.layers].sort((left, right) => right.order - left.order).map((layer) => (
                    <div key={layer.sourceNodeId} className={cn("flex h-8 items-center gap-1.5 px-1.5", selectedId === layer.sourceNodeId && "bg-violet-500/[.08]")}>
                        <button type="button" onClick={() => updateLayer(layer.sourceNodeId, { visible: !layer.visible })} className="rounded p-1 text-muted-foreground hover:text-foreground" title={layer.visible ? t("web.workbench.canvas.psd.hide") : t("web.workbench.canvas.psd.show")}>{layer.visible ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}</button>
                        <button type="button" onClick={() => setSelectedId(layer.sourceNodeId)} className="min-w-0 flex-1 truncate text-left text-[10px] font-medium">{layer.name}</button>
                        <button type="button" onClick={() => moveLayer(layer.sourceNodeId, 1)} className="rounded p-1 text-muted-foreground hover:text-foreground" title={t("web.workbench.canvas.psd.raise")}><ChevronUp className="h-3.5 w-3.5" /></button>
                        <button type="button" onClick={() => moveLayer(layer.sourceNodeId, -1)} className="rounded p-1 text-muted-foreground hover:text-foreground" title={t("web.workbench.canvas.psd.lower")}><ChevronDown className="h-3.5 w-3.5" /></button>
                    </div>
                ))}
            </div>
            {selected ? (
                <div className="grid grid-cols-5 gap-1.5 px-1">
                    <label className="text-[8px] text-muted-foreground">X<input type="number" value={selected.x} onChange={(event) => updateLayer(selected.sourceNodeId, { x: numberValue(event.target.value, selected.x, -32768, 32768) })} className="mt-1 h-7 w-full rounded-lg border border-border/70 bg-background px-1.5 text-[9px] text-foreground" /></label>
                    <label className="text-[8px] text-muted-foreground">Y<input type="number" value={selected.y} onChange={(event) => updateLayer(selected.sourceNodeId, { y: numberValue(event.target.value, selected.y, -32768, 32768) })} className="mt-1 h-7 w-full rounded-lg border border-border/70 bg-background px-1.5 text-[9px] text-foreground" /></label>
                    <label className="text-[8px] text-muted-foreground">{t("web.workbench.canvas.psd.scale")}<input type="number" min={1} max={800} value={selected.scalePercent} onChange={(event) => updateLayer(selected.sourceNodeId, { scalePercent: numberValue(event.target.value, selected.scalePercent, 1, 800) })} className="mt-1 h-7 w-full rounded-lg border border-border/70 bg-background px-1.5 text-[9px] text-foreground" /></label>
                    <label className="text-[8px] text-muted-foreground">{t("web.workbench.canvas.psd.opacity")}<input type="number" min={0} max={100} value={selected.opacityPercent} onChange={(event) => updateLayer(selected.sourceNodeId, { opacityPercent: numberValue(event.target.value, selected.opacityPercent, 0, 100) })} className="mt-1 h-7 w-full rounded-lg border border-border/70 bg-background px-1.5 text-[9px] text-foreground" /></label>
                    <label className="text-[8px] text-muted-foreground">{t("web.workbench.canvas.psd.order")}<input type="number" min={0} max={59} value={selected.order} onChange={(event) => updateLayer(selected.sourceNodeId, { order: numberValue(event.target.value, selected.order, 0, 59) })} className="mt-1 h-7 w-full rounded-lg border border-border/70 bg-background px-1.5 text-[9px] text-foreground" /></label>
                </div>
            ) : null}
        </div>
    );
}

export function CreativeCanvasPsdLayerEditor({
    sessionId,
    resource,
    edits,
    onChange,
    readOnly = false,
}: {
    sessionId: string;
    resource: CreativeCanvasMediaResource;
    edits: CanvasPsdLayerEdit[];
    onChange: (edits: CanvasPsdLayerEdit[]) => void;
    readOnly?: boolean;
}) {
    const t = useT();
    const previewRef = useRef<HTMLDivElement | null>(null);
    const dragRef = useRef<{ pointerId: number; startX: number; startY: number; x: number; y: number } | null>(null);
    const [manifest, setManifest] = useState<PsdManifest | null>(null);
    const [selectedPath, setSelectedPath] = useState("");
    const [error, setError] = useState("");
    useEffect(() => {
        const controller = new AbortController();
        setManifest(null);
        setError("");
        const origin = String(resource.origin || "");
        const id = String(resource.id || "");
        void fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/psd/${origin}/${encodeURIComponent(id)}/manifest`, { cache: "no-store", signal: controller.signal })
            .then(async (response) => {
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
                setManifest(payload as PsdManifest);
                const first = flattenLayers(Array.isArray(payload?.layers) ? payload.layers : [])[0];
                setSelectedPath(first?.layerPath || "");
            })
            .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
        return () => controller.abort();
    }, [resource.id, resource.origin, sessionId]);
    const flat = useMemo(() => flattenLayers(manifest?.layers || []), [manifest]);
    const selected = flat.find((item) => item.layerPath === selectedPath) || null;
    const selectedEdit = edits.find((item) => item.layerPath === selectedPath);
    const effective = selected ? {
        name: selectedEdit?.name ?? selected.name,
        visible: selectedEdit?.visible ?? selected.visible,
        opacityPercent: selectedEdit?.opacityPercent ?? selected.opacityPercent,
        x: selectedEdit?.x ?? selected.left,
        y: selectedEdit?.y ?? selected.top,
        order: selectedEdit?.order ?? selected.index,
        targetParentPath: selectedEdit?.targetParentPath ?? selected.parentPath,
    } : null;
    const updateEdit = (patch: Partial<CanvasPsdLayerEdit>) => {
        if (!selected || readOnly) return;
        const next = edits.filter((item) => item.layerPath !== selected.layerPath);
        next.push({ ...(selectedEdit || { layerPath: selected.layerPath }), ...patch, layerPath: selected.layerPath });
        onChange(next);
    };
    const onOutlinePointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
        if (!effective || readOnly || event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, x: effective.x, y: effective.y };
    };
    const onOutlinePointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
        const drag = dragRef.current;
        const rect = previewRef.current?.getBoundingClientRect();
        if (!drag || drag.pointerId !== event.pointerId || !rect || !manifest) return;
        updateEdit({
            x: Math.round(drag.x + (event.clientX - drag.startX) * manifest.width / Math.max(1, rect.width)),
            y: Math.round(drag.y + (event.clientY - drag.startY) * manifest.height / Math.max(1, rect.height)),
        });
    };

    if (error) return <div className="rounded-xl border border-red-300/60 bg-red-50/70 px-3 py-5 text-center text-[10px] text-red-700 dark:bg-red-950/20">{error}</div>;
    if (!manifest) return <div className="flex h-56 items-center justify-center text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" /></div>;
    const groups = flat.filter((item) => item.kind === "group");
    return (
        <div className="grid grid-cols-[minmax(0,1fr)_210px] gap-2">
            <div ref={previewRef} style={{ aspectRatio: `${manifest.width} / ${manifest.height}` }} className="relative max-h-[330px] min-h-[220px] overflow-hidden rounded-xl border border-border/70 bg-muted/20">
                {resource.previewUrl ? <img src={resource.previewUrl} alt="" draggable={false} className="h-full w-full object-contain" /> : null}
                {selected && effective ? (
                    <button
                        type="button"
                        onPointerDown={onOutlinePointerDown}
                        onPointerMove={onOutlinePointerMove}
                        onPointerUp={() => { dragRef.current = null; }}
                        onPointerCancel={() => { dragRef.current = null; }}
                        style={{
                            left: `${effective.x / Math.max(1, manifest.width) * 100}%`,
                            top: `${effective.y / Math.max(1, manifest.height) * 100}%`,
                            width: `${selected.width / Math.max(1, manifest.width) * 100}%`,
                            height: `${selected.height / Math.max(1, manifest.height) * 100}%`,
                        }}
                        disabled={readOnly}
                        className={cn("absolute min-h-4 min-w-4 border-2 border-violet-500 bg-violet-500/10 shadow-[0_0_0_1px_rgba(255,255,255,.8)]", !readOnly && "touch-none")}
                        title={effective.name}
                    />
                ) : null}
            </div>
            <div className="flex min-h-0 flex-col border-l border-border/60 pl-2">
                <div className="mb-1 flex items-center gap-1.5 px-1 text-[9px] font-semibold text-muted-foreground"><Layers3 className="h-3.5 w-3.5" />{manifest.layerCount}</div>
                <div className="max-h-44 flex-1 overflow-y-auto border-y border-border/50 py-1">
                    {flat.map((layer) => {
                        const changed = edits.some((item) => item.layerPath === layer.layerPath);
                        return <button key={layer.layerPath} type="button" onClick={() => setSelectedPath(layer.layerPath)} style={{ paddingLeft: 6 + layer.depth * 12 }} className={cn("flex h-7 w-full items-center gap-1.5 pr-1 text-left text-[9px] hover:bg-muted", selectedPath === layer.layerPath && "bg-violet-500/[.08] text-violet-700")}>{layer.kind === "group" ? <Layers3 className="h-3 w-3 shrink-0" /> : <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />}<span className="min-w-0 flex-1 truncate">{edits.find((item) => item.layerPath === layer.layerPath)?.name || layer.name}</span>{changed ? <span className="h-1.5 w-1.5 rounded-full bg-violet-500" /> : null}</button>;
                    })}
                </div>
                {selected && effective && !readOnly ? (
                    <div className="mt-2 space-y-1.5">
                        <div className="flex items-center gap-1"><button type="button" onClick={() => updateEdit({ visible: !effective.visible })} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted">{effective.visible ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}</button><input value={effective.name} onChange={(event) => updateEdit({ name: event.target.value })} className="h-7 min-w-0 flex-1 rounded-lg border border-border/70 bg-background px-2 text-[9px]" /></div>
                        <div className="grid grid-cols-3 gap-1"><input aria-label="X" type="number" value={effective.x} onChange={(event) => updateEdit({ x: numberValue(event.target.value, effective.x, -32768, 32768) })} className="h-7 rounded-lg border border-border/70 bg-background px-1.5 text-[9px]" /><input aria-label="Y" type="number" value={effective.y} onChange={(event) => updateEdit({ y: numberValue(event.target.value, effective.y, -32768, 32768) })} className="h-7 rounded-lg border border-border/70 bg-background px-1.5 text-[9px]" /><input aria-label={t("web.workbench.canvas.psd.opacity")} type="number" min={0} max={100} value={effective.opacityPercent} onChange={(event) => updateEdit({ opacityPercent: numberValue(event.target.value, effective.opacityPercent, 0, 100) })} className="h-7 rounded-lg border border-border/70 bg-background px-1.5 text-[9px]" /></div>
                        <div className="grid grid-cols-2 gap-1"><input aria-label={t("web.workbench.canvas.psd.order")} type="number" min={0} max={199} value={effective.order} onChange={(event) => updateEdit({ order: numberValue(event.target.value, effective.order, 0, 199) })} className="h-7 rounded-lg border border-border/70 bg-background px-1.5 text-[9px]" /><select aria-label={t("web.workbench.canvas.psd.parent")} value={effective.targetParentPath} onChange={(event) => updateEdit({ targetParentPath: event.target.value })} className="h-7 rounded-lg border border-border/70 bg-background px-1 text-[9px]"><option value="">{t("web.workbench.canvas.psd.root")}</option>{groups.filter((item) => item.layerPath !== selected.layerPath && !item.layerPath.startsWith(`${selected.layerPath}/`)).map((group) => <option key={group.layerPath} value={group.layerPath}>{group.name}</option>)}</select></div>
                    </div>
                ) : null}
            </div>
        </div>
    );
}
