"use client";
/* eslint-disable @next/next/no-img-element -- session-authorized source previews */

import dynamic from "next/dynamic";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Plus, Trash2, Upload, X, Play, Pause, Check, Loader2 } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";
import type { CanvasResource } from "./types";
import { EMPTY_POSE, SCENE_ROLES, newSceneEntity, sampleEntity, sceneReferenceDigest, updateSceneEntity, type ProxyScene, type SceneEntity, type SceneReference, type SceneVector } from "./proxy-scene";

const Preview = dynamic(() => import("./proxy-scene-preview"), { ssr: false });
const inputClass = "w-full rounded-lg border border-border bg-background px-2 py-1.5 text-xs text-foreground outline-none focus:border-violet-500 disabled:opacity-40";

function Field({ label, children }: { label: string; children: ReactNode }) {
    return <label className="block space-y-1.5 text-[11px] text-muted-foreground"><span>{label}</span>{children}</label>;
}
function Vector({ label, value, onChange }: { label: string; value: SceneVector; onChange: (value: SceneVector) => void }) {
    return <div className="space-y-1 text-[11px] text-muted-foreground"><span>{label}</span><div className="grid grid-cols-3 gap-2">
        {value.map((number, i) => <label key={i} className="flex items-center gap-1"><span>{["X", "Y", "Z"][i]}</span><input aria-label={`${label} ${["X", "Y", "Z"][i]}`} className={inputClass} type="number" step="0.1" value={number} onChange={(event) => { const next = [...value] as SceneVector; next[i] = Number(event.target.value); onChange(next); }} /></label>)}
    </div></div>;
}

export default function ProxySceneEditor({ initialScene, initialReferences, resources, disabled, sessionId, onSave, onClose, onUpload }: {
    initialScene: ProxyScene; initialReferences: SceneReference[]; resources: CanvasResource[]; disabled: boolean; sessionId: string;
    onSave: (scene: ProxyScene, references: SceneReference[]) => void; onClose: () => void; onUpload: (files: File[]) => Promise<void>;
}) {
    const t = useT();
    const s = (key: string) => t(`web.workbench.canvas.scene.${key}`);
    const [scene, setScene] = useState(initialScene);
    const [references, setReferences] = useState(initialReferences);
    const [selectedId, setSelectedId] = useState(initialScene.entities[0]?.entityId || "");
    const [tab, setTab] = useState<"entity" | "references" | "motion" | "camera">("entity");
    const [time, setTime] = useState(0);
    const [playing, setPlaying] = useState(false);
    const [selectedResource, setSelectedResource] = useState("");
    const [role, setRole] = useState<SceneReference["semanticRole"]>("front");
    const [purpose, setPurpose] = useState("");
    const [pending, setPending] = useState(false);
    const [error, setError] = useState("");
    const requestRef = useRef<AbortController | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);
    const entity = scene.entities.find((item) => item.entityId === selectedId);
    const currentFrame = entity ? sampleEntity(entity, time) : null;
    const timeKey = entity?.motion.find((key) => Math.abs(key.time - time) < .5 / scene.fps);
    const cameraKey = scene.camera.keyframes.find((key) => Math.abs(key.time - time) < .5 / scene.fps);
    const images = resources.filter((resource) => resource.sessionId === sessionId && resource.mediaType === "image" && resource.availability !== "unavailable" && resource.adoptedByCurrentSession !== false);
    const invalidReference = references.some((ref) => !scene.entities.some((item) => item.entityId === ref.entityId) || !ref.purpose.trim() || !ref.resourceDigest || ref.resource.availability === "unavailable");
    const missingNames = scene.entities.some((item) => !item.name.trim());
    const latestKey = Math.max(0, ...scene.camera.keyframes.map((key) => key.time), ...scene.entities.flatMap((item) => item.motion.map((key) => key.time)));
    const invalidTimeline = scene.durationSeconds < latestKey || scene.camera.keyframes[0]?.time !== 0 || scene.entities.some((item) => item.motion.length && item.motion[0].time !== 0);
    const duplicateColors = new Set(scene.entities.map((item) => item.proxyColor.toLowerCase())).size !== scene.entities.length;
    useEffect(() => () => requestRef.current?.abort(), []);
    useEffect(() => {
        if (!playing) return;
        const timer = window.setInterval(() => setTime((value) => value + 1 / scene.fps >= scene.durationSeconds ? 0 : value + 1 / scene.fps), 1000 / scene.fps);
        const pause = () => { if (document.hidden) setPlaying(false); };
        document.addEventListener("visibilitychange", pause);
        return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", pause); };
    }, [playing, scene.durationSeconds, scene.fps]);

    const editEntity = (patch: Partial<SceneEntity>) => setScene((value) => ({ ...value, entities: value.entities.map((item) => item.entityId === selectedId ? updateSceneEntity(item, patch) : item) }));
    const addReference = async () => {
        const resource = images.find((item) => `${item.origin}:${item.id}` === selectedResource);
        if (!resource || !entity || !purpose.trim() || pending || disabled) return;
        const entityId = entity.entityId;
        requestRef.current?.abort();
        const request = new AbortController();
        requestRef.current = request;
        setPending(true); setError("");
        try {
            const digest = await sceneReferenceDigest(resource, request.signal);
            if (request.signal.aborted) return;
            setReferences((value) => [...value, { resource, entityId, bindingKey: crypto.randomUUID(), semanticRole: role, purpose, resourceDigest: digest }]);
            setPurpose("");
        } catch (reason) {
            if (!request.signal.aborted) setError(reason instanceof Error ? s(reason.message) : s("reference_unavailable"));
        } finally { if (!request.signal.aborted) setPending(false); }
    };
    const addMotionKey = () => {
        if (!entity || !currentFrame) return;
        editEntity({ motion: [...entity.motion.filter((key) => Math.abs(key.time - time) >= .5 / scene.fps), { time, ...currentFrame }].sort((a, b) => a.time - b.time) });
    };
    const updateMotion = (patch: Record<string, unknown>) => {
        if (!entity || !timeKey) return;
        editEntity({ motion: entity.motion.map((key) => key === timeKey ? { ...key, ...patch } : key) });
    };

    return <div className="absolute inset-0 z-[70] flex items-center justify-center bg-background/65 p-3 backdrop-blur-sm" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()} data-canvas-wheel-isolation>
        <section role="dialog" aria-modal="true" aria-label={s("title")} className="flex h-full max-h-[820px] w-full max-w-[1160px] flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-2xl">
            <header className="flex items-center gap-3 border-b border-border px-5 py-3"><div className="flex-1"><h2 className="text-sm font-semibold">{s("title")}</h2><p className="mt-1 text-[11px] text-muted-foreground">{s("subtitle")}</p></div><button type="button" aria-label={s("close")} onClick={onClose} className="rounded-lg p-2 hover:bg-muted"><X size={18} /></button></header>
            <div className="grid min-h-0 flex-1 grid-cols-[minmax(280px,1.1fr)_minmax(290px,1fr)] max-[700px]:grid-cols-1 max-[700px]:overflow-y-auto">
                <div className="flex min-h-0 flex-col border-r border-border bg-muted/20 p-4">
                    <div className="relative flex min-h-[180px] flex-1 items-center overflow-hidden rounded-xl border border-border bg-neutral-900"><div className="w-full" style={{ aspectRatio: `${scene.width}/${scene.height}` }}><Preview scene={scene} time={time} selectedId={selectedId} onSelect={setSelectedId} /></div><span className="pointer-events-none absolute left-3 top-3 rounded-full bg-background/85 px-2 py-1 text-[10px]">{s("draftPreview")}</span></div>
                    <div className="my-3 flex items-center gap-3"><button type="button" aria-label={s(playing ? "pause" : "play")} onClick={() => setPlaying(!playing)} className="rounded-lg border border-border p-2">{playing ? <Pause size={14} /> : <Play size={14} />}</button><input aria-label={s("time")} type="range" min="0" max={scene.durationSeconds} step={1 / scene.fps} value={time} onChange={(event) => { setPlaying(false); setTime(Number(event.target.value)); }} className="min-w-0 flex-1 accent-violet-600" /><span className="w-24 text-right font-mono text-[11px]">{time.toFixed(2)} / {scene.durationSeconds}s</span></div>
                    <fieldset disabled={disabled} className="grid grid-cols-3 gap-2"><Field label={s("duration")}><input className={inputClass} aria-label={s("duration")} type="number" min="2" max="15" value={scene.durationSeconds} onChange={(event) => { const durationSeconds = Number(event.target.value); setScene({ ...scene, durationSeconds }); setTime(Math.min(time, durationSeconds)); }} /></Field><Field label={s("fps")}><input className={inputClass} type="number" min="1" max="60" value={scene.fps} onChange={(event) => setScene({ ...scene, fps: Math.max(1, Number(event.target.value)) })} /></Field><Field label={s("background")}><input className="h-8 w-full rounded-lg" type="color" value={scene.background} onChange={(event) => setScene({ ...scene, background: event.target.value })} /></Field></fieldset>
                    <div className="mt-3 flex flex-wrap gap-2">{scene.entities.map((item, index) => <button type="button" key={item.entityId} onClick={() => setSelectedId(item.entityId)} className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs ${selectedId === item.entityId ? "border-violet-500 bg-violet-500/10" : "border-border"}`}><span className="h-2.5 w-2.5 rounded-full" style={{ background: item.proxyColor }} />{item.name || `${s("entity")} ${index + 1}`}</button>)}<button type="button" disabled={disabled} aria-label={s("addEntity")} onClick={() => { const next = newSceneEntity(crypto.randomUUID(), scene.entities.length); setScene({ ...scene, entities: [...scene.entities, next] }); setSelectedId(next.entityId); }} className="rounded-full border border-dashed border-border px-3 py-1.5"><Plus size={14} /></button></div>
                    <p className="mt-3 text-[11px] leading-5 text-muted-foreground">{s("softGuidance")}</p>
                </div>
                <div className="flex min-h-0 flex-col"><nav className="flex border-b border-border px-3 pt-2" aria-label={s("editorTabs")}>{(["entity", "references", "motion", "camera"] as const).map((item) => <button key={item} type="button" role="tab" aria-selected={tab === item} onClick={() => setTab(item)} className={`flex-1 border-b-2 px-2 py-3 text-xs ${tab === item ? "border-violet-600 text-violet-600" : "border-transparent text-muted-foreground"}`}>{s(item)}</button>)}</nav>
                    <fieldset disabled={disabled} className="custom-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
                        {tab === "entity" && entity ? <>
                            <div className="grid grid-cols-[1fr_64px] gap-3"><Field label={s("name")}><input className={inputClass} value={entity.name} onChange={(event) => editEntity({ name: event.target.value })} /></Field><Field label={s("color")}><input type="color" className="h-8 w-full" value={entity.proxyColor} onChange={(event) => editEntity({ proxyColor: event.target.value })} /></Field></div>
                            <div className="grid grid-cols-2 gap-3"><Field label={s("kind")}><select className={inputClass} value={entity.kind} onChange={(event) => editEntity({ kind: event.target.value as SceneEntity["kind"] })}>{["character", "object", "environment"].map((kind) => <option key={kind} value={kind}>{s(kind)}</option>)}</select></Field><Field label={s("shape")}><select className={inputClass} value={entity.shape} onChange={(event) => editEntity({ shape: event.target.value as SceneEntity["shape"] })}>{["box", "capsule", "sphere"].map((shape) => <option key={shape} value={shape}>{s(shape)}</option>)}</select></Field></div>
                            <Field label={s("appearance")}><textarea className={inputClass} rows={3} value={entity.appearance} onChange={(event) => editEntity({ appearance: event.target.value })} /></Field><Field label={s("material")}><textarea className={inputClass} rows={2} value={entity.material} onChange={(event) => editEntity({ material: event.target.value })} /></Field>
                            <Vector label={s("size")} value={entity.size} onChange={(size) => editEntity({ size })} /><Vector label={s("position")} value={entity.position} onChange={(position) => editEntity({ position })} /><Vector label={s("rotation")} value={entity.rotation} onChange={(rotation) => editEntity({ rotation })} />
                            <Field label={s("style")}><textarea className={inputClass} rows={2} value={scene.style} onChange={(event) => setScene({ ...scene, style: event.target.value })} /></Field>
                            <button type="button" className="flex items-center gap-2 text-xs text-red-600" disabled={scene.entities.length <= 1} onClick={() => { setScene({ ...scene, entities: scene.entities.filter((item) => item.entityId !== selectedId) }); setReferences(references.filter((ref) => ref.entityId !== selectedId)); setSelectedId(scene.entities.find((item) => item.entityId !== selectedId)?.entityId || ""); }}><Trash2 size={13} />{s("removeEntity")}</button>
                        </> : null}
                        {tab === "references" ? <>
                            <p className="text-xs text-muted-foreground">{s("referenceHelp")}</p>
                            {references.filter((ref) => ref.entityId === selectedId || !ref.entityId).map((ref) => <div key={ref.bindingKey} className="flex gap-3 rounded-xl border border-border p-2.5">
                                {ref.resource.url ? <img src={ref.resource.url} alt={ref.resource.name} className="h-16 w-16 rounded-lg bg-muted object-contain" /> : <div className="h-16 w-16 rounded-lg bg-muted" />}
                                <div className="min-w-0 flex-1 space-y-2"><p className="truncate text-xs">{ref.resource.name || s("reference_unavailable")}</p><select aria-label={s("role")} className={inputClass} value={ref.semanticRole} onChange={(event) => setReferences(references.map((item) => item.bindingKey === ref.bindingKey ? { ...item, semanticRole: event.target.value as SceneReference["semanticRole"], entityId: selectedId } : item))}>{SCENE_ROLES.map((item) => <option key={item} value={item}>{s(`role.${item}`)}</option>)}</select><input aria-label={s("purpose")} className={inputClass} value={ref.purpose} onChange={(event) => setReferences(references.map((item) => item.bindingKey === ref.bindingKey ? { ...item, purpose: event.target.value, entityId: selectedId } : item))} /></div>
                                <button type="button" aria-label={s("removeReference")} className="self-start p-1 text-muted-foreground" onClick={() => setReferences(references.filter((item) => item.bindingKey !== ref.bindingKey))}><X size={14} /></button>
                            </div>)}
                            <div className="space-y-3 rounded-xl border border-dashed border-border p-3"><Field label={s("chooseReference")}><select className={inputClass} value={selectedResource} onChange={(event) => setSelectedResource(event.target.value)}><option value="">{s("selectImage")}</option>{images.map((resource) => <option key={`${resource.origin}:${resource.id}`} value={`${resource.origin}:${resource.id}`}>{resource.name}</option>)}</select></Field><Field label={s("role")}><select className={inputClass} value={role} onChange={(event) => setRole(event.target.value as SceneReference["semanticRole"])}>{SCENE_ROLES.map((item) => <option key={item} value={item}>{s(`role.${item}`)}</option>)}</select></Field><Field label={s("purpose")}><textarea className={inputClass} rows={2} value={purpose} onChange={(event) => setPurpose(event.target.value)} /></Field><div className="flex items-center justify-between"><button type="button" disabled={!entity || !selectedResource || !purpose.trim() || pending} onClick={() => void addReference()} className="flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-2 text-xs text-white disabled:opacity-40">{pending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}{s("bindReference")}</button><button type="button" onClick={() => fileRef.current?.click()} className="flex items-center gap-1.5 text-xs"><Upload size={13} />{s("upload")}</button></div><input ref={fileRef} type="file" multiple accept="image/*" className="hidden" onChange={(event) => { const files = Array.from(event.target.files || []); event.target.value = ""; void onUpload(files); }} /></div>
                        </> : null}
                        {tab === "motion" && entity && currentFrame ? <>
                            <p className="text-xs leading-5 text-muted-foreground">{s("motionHelp")}</p><div className="flex flex-wrap gap-2">{entity.motion.map((key) => <button type="button" key={key.time} className="rounded-lg border border-border px-2 py-1 text-xs" onClick={() => { setPlaying(false); setTime(key.time); }}>{key.time.toFixed(2)}s</button>)}</div><button type="button" onClick={addMotionKey} className="rounded-lg border border-violet-500 px-3 py-2 text-xs text-violet-600">{s("addKey")}</button>
                            {timeKey ? <><Vector label={s("position")} value={timeKey.position} onChange={(position) => updateMotion({ position })} /><Vector label={s("rotation")} value={timeKey.rotation} onChange={(rotation) => updateMotion({ rotation })} />{entity.shape === "capsule" ? Object.keys(EMPTY_POSE).map((joint) => <Field key={joint} label={s(joint)}><input className={inputClass} type="number" min="-180" max="180" value={(timeKey.pose || EMPTY_POSE)[joint as keyof typeof EMPTY_POSE]} onChange={(event) => updateMotion({ pose: { ...(timeKey.pose || EMPTY_POSE), [joint]: Number(event.target.value) } })} /></Field>) : null}<button type="button" onClick={() => editEntity({ motion: entity.motion.filter((key) => key !== timeKey) })} className="text-xs text-red-600">{s("removeKey")}</button></> : <p className="text-xs text-muted-foreground">{s("selectKey")}</p>}
                        </> : null}
                        {tab === "camera" ? <><Field label={s("fov")}><input type="number" min="10" max="120" className={inputClass} value={scene.camera.fov} onChange={(event) => setScene({ ...scene, camera: { ...scene.camera, fov: Number(event.target.value) } })} /></Field><div className="flex flex-wrap gap-2">{scene.camera.keyframes.map((key) => <button type="button" key={key.time} className="rounded-lg border border-border px-2 py-1 text-xs" onClick={() => { setPlaying(false); setTime(key.time); }}>{key.time.toFixed(2)}s</button>)}</div><button type="button" className="rounded-lg border border-violet-500 px-3 py-2 text-xs text-violet-600" onClick={() => { if (cameraKey) return; const previous = scene.camera.keyframes.filter((key) => key.time <= time).at(-1) || scene.camera.keyframes[0]; setScene({ ...scene, camera: { ...scene.camera, keyframes: [...scene.camera.keyframes, { ...previous, time }].sort((a, b) => a.time - b.time) } }); }}>{s("addCameraKey")}</button>{cameraKey ? <><Vector label={s("cameraPosition")} value={cameraKey.position} onChange={(position) => setScene({ ...scene, camera: { ...scene.camera, keyframes: scene.camera.keyframes.map((key) => key === cameraKey ? { ...key, position } : key) } })} /><Vector label={s("cameraTarget")} value={cameraKey.target} onChange={(target) => setScene({ ...scene, camera: { ...scene.camera, keyframes: scene.camera.keyframes.map((key) => key === cameraKey ? { ...key, target } : key) } })} /><button type="button" disabled={scene.camera.keyframes.length < 2} className="text-xs text-red-600" onClick={() => setScene({ ...scene, camera: { ...scene.camera, keyframes: scene.camera.keyframes.filter((key) => key !== cameraKey) } })}>{s("removeKey")}</button></> : null}</> : null}
                    </fieldset>
                </div>
            </div>
            <footer className="flex items-center gap-3 border-t border-border px-5 py-3"><p role="status" className="min-w-0 flex-1 text-[11px] text-muted-foreground">{error || (invalidReference ? s("invalidReference") : missingNames ? s("nameRequired") : invalidTimeline ? s("invalidTimeline") : duplicateColors ? s("duplicateColors") : s("saveOnly"))}</p><button type="button" disabled={disabled || pending || invalidReference || missingNames || invalidTimeline || duplicateColors} onClick={() => onSave(scene, references)} className="flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-xs font-semibold text-white disabled:opacity-35"><Check size={14} />{s("save")}</button></footer>
        </section>
    </div>;
}
