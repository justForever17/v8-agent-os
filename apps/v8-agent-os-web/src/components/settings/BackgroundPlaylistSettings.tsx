"use client";
/* eslint-disable @next/next/no-img-element */
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ImagePlus, Video, X } from "lucide-react";
import { backgroundFromAppearance, normalizeBackgroundPlaylist, type BackgroundItem } from "@v8/product-ui/background-playlist";
import { useClientProfile } from "@/hooks/use-client-profile";
import { resolveLightBackgroundMediaSrc } from "@/lib/personalization";
import { updateUserAppearance } from "@/lib/actions/user.actions";
import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";

export function BackgroundPlaylistSettings({ open }: { open: boolean }) {
    const t = useT();
    const { profile, applyProfile, refreshProfile } = useClientProfile();
    const raw = JSON.stringify(profile?.appearance || {});
    const canonical = useMemo(() => { try { return backgroundFromAppearance(JSON.parse(raw)); } catch { return normalizeBackgroundPlaylist({}); } }, [raw]);
    const [draft, setDraft] = useState(canonical);
    const [dirty, setDirty] = useState(false);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState("");
    const [selected, setSelected] = useState("");
    const input = useRef<HTMLInputElement>(null);
    const upload = useRef<AbortController | null>(null);
    const dragged = useRef<string | null>(null);
    useEffect(() => { if (!dirty) setDraft(canonical); }, [canonical, dirty]);
    useEffect(() => { if (!open) upload.current?.abort(); return () => upload.current?.abort(); }, [open]);
    const item = draft.items.find((value) => value.id === selected) || draft.items[0];
    const patchItem = (patch: Partial<BackgroundItem>) => { if (!item) return; setDirty(true); setDraft((current) => ({ ...current, items: current.items.map((value) => value.id === item.id ? { ...value, ...patch } : value) })); };
    const move = (id: string, target: number) => {
        setDirty(true);
        setDraft((current) => {
            const from = current.items.findIndex((value) => value.id === id);
            if (from < 0 || target < 0 || target >= current.items.length) return current;
            const items = [...current.items]; const [moving] = items.splice(from, 1); items.splice(target, 0, moving);
            return { ...current, items };
        });
    };
    const add = async (files: File[]) => {
        if (!files.length || busy) return;
        if (draft.items.length + files.length > 50) { setMessage(t("web.background.limit")); return; }
        const controller = new AbortController(); upload.current = controller; setBusy(true); setMessage("");
        try {
            for (const file of files) {
                const response = await fetch("/api/user-background-upload", { method: "POST", headers: { "content-type": file.type }, body: file, signal: controller.signal });
                const data = await response.json();
                if (controller.signal.aborted) break;
                if (!response.ok || !data.receipt || !data.path) throw new Error(data.error || t("web.background.importFailed"));
                const added: BackgroundItem = { id: crypto.randomUUID(), media: data.path, kind: data.mediaType, fit: "cover", position: "50% 50%" };
                setDirty(true); setDraft((current) => ({ ...current, enabled: true, items: [...current.items, added] })); setSelected(added.id);
            }
        } catch (error) { if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : t("web.background.importFailed")); }
        finally { if (upload.current === controller) { upload.current = null; setBusy(false); } }
    };
    const save = async () => {
        setBusy(true); setMessage("");
        try {
            const result = await updateUserAppearance({ webBackground: normalizeBackgroundPlaylist(draft) });
            if (!result.success || !result.user) throw new Error(result.error || t("web.background.saveFailed"));
            await applyProfile(result.user); setDraft(backgroundFromAppearance(result.user.appearance)); setDirty(false); setMessage(t("web.background.saved"));
        } catch (error) { setMessage(error instanceof Error ? error.message : t("web.background.saveFailed")); }
        finally { setBusy(false); }
    };
    return <section className="space-y-3 rounded-2xl border border-border/70 bg-card/55 p-4" aria-label={t("web.background.playlist")}>
        <div className="flex flex-wrap items-center justify-between gap-2">
            <div><h3 className="text-sm font-medium">{t("web.background.title")}</h3><p className="text-xs text-muted-foreground">{t("web.background.description")}</p></div>
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={draft.enabled} onChange={(e) => { setDirty(true); setDraft({ ...draft, enabled: e.target.checked }); }} />{t("web.background.enable")}</label>
        </div>
        <div data-testid="background-preview" className="flex h-32 items-center justify-center overflow-hidden rounded-xl border border-border/60" style={item?.kind === "image" ? { backgroundImage: `url(${JSON.stringify(resolveLightBackgroundMediaSrc(item.media))})`, backgroundPosition: item.position, backgroundRepeat: "no-repeat", backgroundSize: item.fit } : undefined}>
            {item?.kind === "video" ? <div data-testid="background-video-summary" className="flex items-center gap-2 text-sm text-muted-foreground"><Video className="h-5 w-5" />{t("web.background.videoPreview")}</div> : !item ? <span className="text-xs text-muted-foreground">{t("web.background.empty")}</span> : null}
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs">
            <label className="flex items-center gap-2">{t("web.background.interval")}<input aria-label={t("web.background.intervalSeconds")} className="w-20 rounded border bg-background px-2 py-1" type="number" min="1" max="3600" value={draft.imageDurationMs / 1000} onChange={(e) => { setDirty(true); setDraft({ ...draft, imageDurationMs: Number(e.target.value) * 1000 }); }} />{t("web.background.seconds")}</label>
            <label className="flex items-center gap-2">{t("web.background.order")}<select aria-label={t("web.background.order")} className="rounded border bg-background p-1" value={draft.order} onChange={(e) => { setDirty(true); setDraft({ ...draft, order: e.target.value as "ordered" | "shuffle" }); }}><option value="ordered">{t("web.background.ordered")}</option><option value="shuffle">{t("web.background.shuffle")}</option></select></label>
        </div>
        <ol className="max-h-52 space-y-1 overflow-y-auto" aria-label={t("web.background.media")}>
            {draft.items.map((value, index) => <li key={value.id} draggable onDragStart={() => { dragged.current = value.id; }} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); if (dragged.current) move(dragged.current, index); dragged.current = null; }} className={`flex items-center gap-2 rounded-lg border p-1.5 ${item?.id === value.id ? "border-primary/60" : "border-transparent"}`}>
                <button type="button" onClick={() => setSelected(value.id)} className="flex min-w-0 flex-1 items-center gap-2 text-left text-xs" aria-label={t("web.background.previewItem", { number: index + 1 })}>
                    {value.kind === "image" ? <img loading="lazy" alt="" className="h-9 w-14 rounded object-cover" src={resolveLightBackgroundMediaSrc(value.media.replace(/\.webp$/, ".thumb.webp"))} onError={(event) => { event.currentTarget.style.visibility = "hidden"; }} /> : <Video className="h-9 w-14" />}
                    <span className="truncate">{index + 1} · {t(value.kind === "video" ? "web.background.video" : "web.background.image")}</span>
                </button>
                <button type="button" disabled={index === 0} aria-label={t("web.background.moveUp", { number: index + 1 })} onClick={() => move(value.id, index - 1)} className="rounded p-1 hover:bg-muted disabled:opacity-30"><ArrowUp className="h-4 w-4" /></button>
                <button type="button" disabled={index === draft.items.length - 1} aria-label={t("web.background.moveDown", { number: index + 1 })} onClick={() => move(value.id, index + 1)} className="rounded p-1 hover:bg-muted disabled:opacity-30"><ArrowDown className="h-4 w-4" /></button>
                <button type="button" aria-label={t("web.background.remove", { number: index + 1 })} onClick={() => { setDirty(true); setDraft({ ...draft, items: draft.items.filter((candidate) => candidate.id !== value.id) }); }} className="rounded p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
            </li>)}
        </ol>
        {item ? <div className="flex flex-wrap items-center gap-3 text-xs">
            <label>{t("web.background.fit")} <select aria-label={t("web.background.fit")} className="rounded border bg-background p-1" value={item.fit} onChange={(e) => patchItem({ fit: e.target.value as "cover" | "contain" })}><option value="cover">{t("web.background.cover")}</option><option value="contain">{t("web.background.contain")}</option></select></label>
            {["web.background.horizontal", "web.background.vertical"].map((label, axis) => <label key={axis} className="flex items-center gap-1">{t(label)}<input aria-label={t(label)} className="w-20" type="range" min="0" max="100" value={parseInt(item.position.split(" ")[axis])} onChange={(e) => { const xy = item.position.split(" "); xy[axis] = `${e.target.value}%`; patchItem({ position: xy.join(" ") }); }} /></label>)}
        </div> : null}
        <input ref={input} type="file" multiple accept="image/jpeg,image/png,image/webp,video/mp4" className="hidden" onChange={(event) => { void add(Array.from(event.target.files || [])); event.target.value = ""; }} />
        <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => input.current?.click()}><ImagePlus className="mr-1 h-4 w-4" />{t("web.background.add")}</Button>
            <Button size="sm" disabled={!dirty || busy} onClick={() => void save()}>{t(busy ? "web.background.busy" : "web.background.save")}</Button>
            {dirty ? <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setDirty(false); setDraft(canonical); setMessage(""); }}> {t("web.background.cancel")}</Button> : null}
            {message ? <button type="button" className="text-xs underline" onClick={() => void refreshProfile()}> {t("web.background.refresh")}</button> : null}
        </div>
        {message ? <p role="status" className="text-xs text-muted-foreground">{message}</p> : null}
        <p className="text-[11px] text-muted-foreground">{t("web.background.importRetention")}</p>
    </section>;
}
