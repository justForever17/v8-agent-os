"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, File, ImageIcon, Loader2, Music, Paperclip, Send, Trash2, Video, X } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import type { CreativeCanvasWorkbenchDocument } from "@/lib/workbench";

type ShelfItem = { id: string; origin: "artifact" | "source"; name: string; mimeType: string; url?: string; caption?: string };
type CanvasNode = ShelfItem & { nodeId: string; x: number; y: number };
const MAX_NODES = 100;

function value(record: Record<string, unknown>, ...keys: string[]) {
    for (const key of keys) if (typeof record[key] === "string" && String(record[key]).trim()) return String(record[key]);
    return "";
}

function normalizeItem(raw: unknown, origin: ShelfItem["origin"], index: number): ShelfItem | null {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const record = raw as Record<string, unknown>;
    const id = value(record, "id", "artifactId", "artifact_id", "sourceId", "source_id") || `${origin}-${index}`;
    const name = value(record, "displayLabel", "display_label", "title", "name", "filename", "fileName") || id;
    const mimeType = value(record, "mimeType", "mime_type", "contentType", "content_type") || "application/octet-stream";
    const url = value(record, "previewUrl", "preview_url", "downloadUrl", "download_url", "url", "resourceUrl", "resource_url") || undefined;
    return { id, origin, name, mimeType, url, caption: value(record, "caption", "description", "summary") || undefined };
}

function ItemIcon({ item }: { item: ShelfItem }) {
    if (item.mimeType.startsWith("image/")) return <ImageIcon className="h-4 w-4" />;
    if (item.mimeType.startsWith("video/")) return <Video className="h-4 w-4" />;
    if (item.mimeType.startsWith("audio/")) return <Music className="h-4 w-4" />;
    return item.origin === "artifact" ? <Box className="h-4 w-4" /> : <Paperclip className="h-4 w-4" />;
}

function ItemPreview({ item, compact = false }: { item: ShelfItem; compact?: boolean }) {
    if (item.url && item.mimeType.startsWith("image/")) return <img src={item.url} alt="" loading="lazy" draggable={false} className="h-full w-full object-contain" />;
    if (item.url && item.mimeType.startsWith("video/") && !compact) return <video src={item.url} controls preload="metadata" className="h-full w-full object-contain" />;
    if (item.url && item.mimeType.startsWith("audio/") && !compact) return <audio src={item.url} controls preload="metadata" className="w-[90%]" />;
    return <ItemIcon item={item} />;
}

export type CanvasTaskRequest = { text: string; refs: Array<Pick<ShelfItem, "id" | "origin" | "name" | "url" | "caption">> };

export function CreativeArtifactCanvas({ document, onSubmitTask }: { document: CreativeCanvasWorkbenchDocument; onSubmitTask?: (request: CanvasTaskRequest) => Promise<boolean> | boolean }) {
    const t = useT();
    const sessionId = document.subjectRef.sessionId;
    const storageKey = `v8-web-creative-canvas:v1:${sessionId}`;
    const boardRef = useRef<HTMLDivElement | null>(null);
    const [items, setItems] = useState<ShelfItem[]>([]);
    const [nodes, setNodes] = useState<CanvasNode[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [instruction, setInstruction] = useState("");
    const [loading, setLoading] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        try {
            const parsed = JSON.parse(window.localStorage.getItem(storageKey) || "[]");
            setNodes(Array.isArray(parsed) ? parsed.slice(0, MAX_NODES) : []);
        } catch { setNodes([]); }
    }, [storageKey]);

    const persist = useCallback((next: CanvasNode[]) => {
        setNodes(next);
        try { window.localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* local persistence is best effort */ }
    }, [storageKey]);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError("");
        const load = async (path: string, key: "artifacts" | "sources", origin: ShelfItem["origin"]) => {
            const params = new URLSearchParams({ sessionId, limit: "100" });
            const response = await fetch(`${path}?${params.toString()}`, { cache: "no-store", signal: controller.signal });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
            return (Array.isArray(payload?.[key]) ? payload[key] : []).map((entry: unknown, index: number) => normalizeItem(entry, origin, index)).filter(Boolean) as ShelfItem[];
        };
        void Promise.all([load("/api/artifacts", "artifacts", "artifact"), load("/api/sources", "sources", "source")])
            .then(([artifacts, sources]) => { if (!controller.signal.aborted) setItems([...artifacts, ...sources]); })
            .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [sessionId]);

    const addItem = useCallback((item: ShelfItem, point?: { x: number; y: number }) => {
        if (nodes.length >= MAX_NODES) return;
        const offset = nodes.length % 8;
        persist([...nodes, { ...item, nodeId: `${item.origin}:${item.id}:${Date.now()}`, x: point?.x ?? 28 + offset * 24, y: point?.y ?? 28 + offset * 20 }]);
    }, [nodes, persist]);

    const removeSelected = useCallback(() => {
        if (!selectedId) return;
        persist(nodes.filter((node) => node.nodeId !== selectedId));
        setSelectedId(null);
    }, [nodes, persist, selectedId]);

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if ((event.key === "Delete" || event.key === "Backspace") && selectedId && !(event.target instanceof HTMLInputElement) && !(event.target instanceof HTMLTextAreaElement)) removeSelected();
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [removeSelected, selectedId]);

    const groups = useMemo(() => ({ artifacts: items.filter((item) => item.origin === "artifact"), sources: items.filter((item) => item.origin === "source") }), [items]);

    const submit = async () => {
        if (!instruction.trim() || !nodes.length || !onSubmitTask || submitting) return;
        setSubmitting(true);
        try {
            const accepted = await onSubmitTask({ text: instruction.trim(), refs: nodes.map(({ id, origin, name, url, caption }) => ({ id, origin, name, url, caption })) });
            if (accepted !== false) setInstruction("");
        } finally { setSubmitting(false); }
    };

    return <div className="grid h-full min-h-0 grid-cols-[168px_minmax(0,1fr)] bg-background">
        <aside className="min-h-0 overflow-y-auto border-r border-border/60 bg-muted/[0.18] p-2">
            <div className="px-1 pb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{t("web.workbench.canvas.shelf")}</div>
            {loading ? <div className="flex justify-center py-8"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /></div> : null}
            {error ? <div className="rounded-md bg-destructive/5 p-2 text-[10px] text-destructive">{error}</div> : null}
            {(["artifacts", "sources"] as const).map((group) => <div key={group} className="mb-3">
                <div className="mb-1 px-1 text-[10px] text-muted-foreground">{t(`web.workbench.canvas.${group}`)}</div>
                {groups[group].map((item) => <button key={`${item.origin}:${item.id}`} type="button" draggable onDragStart={(event) => event.dataTransfer.setData("application/x-v8-canvas-item", `${item.origin}:${item.id}`)} onClick={() => addItem(item)} className="mb-1 flex w-full items-center gap-2 rounded-lg p-1.5 text-left hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary">
                    <span className="flex h-8 w-10 shrink-0 items-center justify-center overflow-hidden rounded-md bg-background text-muted-foreground shadow-sm"><ItemPreview item={item} compact /></span>
                    <span className="min-w-0 flex-1 truncate text-[10px]">{item.name}</span>
                </button>)}
            </div>)}
            {!loading && !items.length ? <div className="px-2 py-8 text-center text-[10px] text-muted-foreground">{t("web.workbench.canvas.emptyShelf")}</div> : null}
        </aside>
        <section className="flex min-h-0 min-w-0 flex-col">
            <div className="flex h-9 shrink-0 items-center justify-end gap-1 border-b border-border/60 px-2">
                <button type="button" disabled={!selectedId} onClick={removeSelected} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.remove")}><Trash2 className="h-3.5 w-3.5" /></button>
                <button type="button" disabled={!nodes.length} onClick={() => { persist([]); setSelectedId(null); }} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.clear")}><X className="h-3.5 w-3.5" /></button>
            </div>
            <div ref={boardRef} onClick={() => setSelectedId(null)} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const key = event.dataTransfer.getData("application/x-v8-canvas-item"); const item = items.find((candidate) => `${candidate.origin}:${candidate.id}` === key); const rect = boardRef.current?.getBoundingClientRect(); if (item && rect) addItem(item, { x: event.clientX - rect.left - 80, y: event.clientY - rect.top - 60 }); }} className="relative min-h-0 flex-1 overflow-hidden bg-[radial-gradient(circle_at_1px_1px,hsl(var(--border)/.55)_1px,transparent_0)] bg-[size:18px_18px]">
                {!nodes.length ? <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-center text-xs text-muted-foreground"><File className="h-6 w-6 opacity-45" /><span>{t("web.workbench.canvas.empty")}</span></div> : null}
                {nodes.map((node) => <div key={node.nodeId} onClick={(event) => { event.stopPropagation(); setSelectedId(node.nodeId); }} onPointerDown={(event) => { if ((event.target as HTMLElement).closest("audio,video,button")) return; event.currentTarget.setPointerCapture(event.pointerId); const origin = { x: event.clientX, y: event.clientY, nodeX: node.x, nodeY: node.y }; const element = event.currentTarget; const move = (moveEvent: PointerEvent) => { const x = Math.max(0, origin.nodeX + moveEvent.clientX - origin.x); const y = Math.max(0, origin.nodeY + moveEvent.clientY - origin.y); element.style.transform = `translate(${x}px, ${y}px)`; }; const up = (upEvent: PointerEvent) => { const x = Math.max(0, origin.nodeX + upEvent.clientX - origin.x); const y = Math.max(0, origin.nodeY + upEvent.clientY - origin.y); persist(nodes.map((item) => item.nodeId === node.nodeId ? { ...item, x, y } : item)); element.releasePointerCapture(upEvent.pointerId); element.removeEventListener("pointermove", move); element.removeEventListener("pointerup", up); }; element.addEventListener("pointermove", move); element.addEventListener("pointerup", up); }} style={{ transform: `translate(${node.x}px, ${node.y}px)` }} className={`absolute left-0 top-0 flex h-32 w-44 touch-none select-none flex-col overflow-hidden rounded-xl border bg-background shadow-sm transition-shadow ${selectedId === node.nodeId ? "border-primary ring-2 ring-primary/15 shadow-lg" : "border-border/75"}`}>
                    <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-muted/30 text-muted-foreground"><ItemPreview item={node} /></div>
                    <div className="shrink-0 truncate border-t border-border/60 px-2 py-1.5 text-[10px] font-medium">{node.name}</div>
                </div>)}
            </div>
            <div className="flex shrink-0 gap-2 border-t border-border/60 p-2">
                <textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={2} placeholder={t("web.workbench.canvas.taskPlaceholder")} className="min-w-0 flex-1 resize-none rounded-lg border border-border bg-background px-3 py-2 text-xs outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/10" />
                <button type="button" disabled={!instruction.trim() || !nodes.length || submitting} onClick={() => void submit()} className="flex w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground disabled:opacity-40" aria-label={t("web.workbench.canvas.submit")}>{submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}</button>
            </div>
        </section>
    </div>;
}
