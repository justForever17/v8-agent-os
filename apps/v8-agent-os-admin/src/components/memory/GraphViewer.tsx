"use client";
import { createPortal } from "react-dom";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Link2, Loader2, Pause, Play, RefreshCw, Search, Trash2, Unlink2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useT } from "@/components/providers/LocaleProvider";
import GalaxyCanvas from "./GalaxyCanvas";
import { visualNodeId, type GalaxyCluster, type GalaxyNode } from "./galaxy-layout";

type Relation = { relationId: string; subject: string; predicate: string; object: string; scope: string; confidence: number; version: string };
type Selection = { cluster: GalaxyCluster; node: GalaxyNode };
type RelationPage = { relations: Relation[]; total: number; nextOffset: number | null };
const GRAPH_URL = "/api/memory/graph";

async function readGraph(query: URLSearchParams, signal?: AbortSignal) {
    const response = await fetch(`${GRAPH_URL}?${query}`, { signal, cache: "no-store" });
    const value = await response.json();
    if (!response.ok) throw new Error(String(value.detail || value.error || response.status));
    return value;
}

export default function GraphViewer({ filterNode = "" }: { filterNode?: string }) {
    const t = useT();
    const [clusters, setClusters] = useState<GalaxyCluster[]>([]);
    const [selected, setSelected] = useState<string | null>(null);
    const [nodeSelection, setNodeSelection] = useState<Selection | null>(null);
    const [query, setQuery] = useState(filterNode);
    const [page, setPage] = useState(0);
    const [workspaceSearch, setWorkspaceSearch] = useState("");
    const [writeWorkspace, setWriteWorkspace] = useState("");
    const clusterTriggers = useRef(new Map<string, HTMLButtonElement>());
    const overviewTrigger = useRef<HTMLButtonElement | null>(null);
    const menuRef = useRef<HTMLElement | null>(null);
    const [nextPage, setNextPage] = useState<number | null>(null);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [paused, setPaused] = useState(false);
    const [reduced, setReduced] = useState(false);
    const [relations, setRelations] = useState<RelationPage>({ relations: [], total: 0, nextOffset: null });
    const [relationOffset, setRelationOffset] = useState(0);
    const [relationLoading, setRelationLoading] = useState(false);
    const [mode, setMode] = useState<"summary" | "root" | "connect" | "disconnect">("summary");
    const [target, setTarget] = useState("");
    const [predicate, setPredicate] = useState("RELATED_TO");
    const [mutating, setMutating] = useState(false);
    const busy = useRef(false);
    const overviewRequest = useRef<AbortController | null>(null);
    const selectionRef = useRef<Selection | null>(null);
    const host = useRef<HTMLDivElement>(null);
    const lastRead = useRef(0);
    const draft = mode === "connect" && (Boolean(target.trim()) || predicate !== "RELATED_TO");
    const activeCluster = clusters.find(item => item.clusterId === selected);

    const load = useCallback(async (offset: number, force = false) => {
        if (overviewRequest.current && !force) return;
        overviewRequest.current?.abort();
        const controller = new AbortController(); overviewRequest.current = controller;
        setLoading(true);
        try {
            const data = await readGraph(new URLSearchParams({ overview: "1", offset: String(offset), workspaceQuery: workspaceSearch }), controller.signal);
            if (controller.signal.aborted) return;
            setClusters(current => {
                const global = current.find(item => item.clusterId === "global");
                return offset && global ? [global, ...data.items] : data.items;
            });
            setTotal(data.totalWorkspaces); setNextPage(data.nextOffset); setError(""); lastRead.current = Date.now();
        } catch (reason) { if (!controller.signal.aborted) setError(String(reason)); }
        finally { if (overviewRequest.current === controller) { overviewRequest.current = null; setLoading(false); } }
    }, [workspaceSearch]);
    useEffect(() => { const timer = setTimeout(() => void load(page, true), workspaceSearch ? 250 : 0); return () => { clearTimeout(timer); overviewRequest.current?.abort(); }; }, [load, page, workspaceSearch]);
    useEffect(() => {
        const media = matchMedia("(prefers-reduced-motion: reduce)");
        const update = () => setReduced(media.matches);
        const initial = requestAnimationFrame(update); media.addEventListener("change", update);
        return () => { cancelAnimationFrame(initial); media.removeEventListener("change", update); };
    }, []);
    useEffect(() => {
        if (selected || nodeSelection) return;
        let visible = false;
        const refresh = () => { if (visible && !document.hidden && Date.now() - lastRead.current > 30000) void load(page); };
        const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; refresh(); });
        if (host.current) observer.observe(host.current);
        document.addEventListener("visibilitychange", refresh);
        window.addEventListener("focus", refresh);
        return () => { observer.disconnect(); document.removeEventListener("visibilitychange", refresh); window.removeEventListener("focus", refresh); };
    }, [load, page, selected, nodeSelection]);
    useEffect(() => {
        if (!selected) return;
        const controller = new AbortController();
        void readGraph(new URLSearchParams({ overview: "1", clusterId: selected }), controller.signal).then(data => {
            if (!controller.signal.aborted) setClusters(current => current.map(item => item.clusterId === selected ? data.items[0] : item));
        }).catch(reason => { if (!controller.signal.aborted) setError(String(reason)); });
        return () => controller.abort();
    }, [selected]);
    useEffect(() => {
        if (!nodeSelection) return;
        menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
    }, [nodeSelection]);
    useEffect(() => {
        if (!nodeSelection) return;
        const controller = new AbortController();
        setRelationLoading(true);
        void readGraph(new URLSearchParams({ overview: "1", clusterId: nodeSelection.cluster.clusterId, entity: nodeSelection.node.id, relationOffset: String(relationOffset) }), controller.signal).then(data => {
            if (!controller.signal.aborted) setRelations(data);
        }).catch(reason => { if (!controller.signal.aborted) setError(String(reason)); }).finally(() => { if (!controller.signal.aborted) setRelationLoading(false); });
        return () => controller.abort();
    }, [nodeSelection, relationOffset]);

    const canLeave = useCallback(() => !busy.current && (!draft || window.confirm(t("admin.galaxy.discardDraft"))), [draft, t]);
    const background = useCallback(() => {
        if (!canLeave()) return false;
        setSelected(null); setNodeSelection(null); selectionRef.current = null; setMode("summary"); setTarget(""); setPredicate("RELATED_TO");
        const returnTarget = clusterTriggers.current.get(selected || "") || overviewTrigger.current;
        returnTarget?.focus({ preventScroll: true });
        return true;
    }, [canLeave, selected]);
    useEffect(() => {
        if (!nodeSelection) return;
        const key = (event: KeyboardEvent) => { if (event.key === "Escape" && !event.isComposing) { event.preventDefault(); background(); } };
        document.addEventListener("keydown", key);
        return () => document.removeEventListener("keydown", key);
    }, [nodeSelection, background]);
    const selectCluster = useCallback((id: string) => {
        if (!canLeave()) return;
        setSelected(id); setNodeSelection(null); selectionRef.current = null; setMode("summary"); setTarget(""); setPredicate("RELATED_TO");
    }, [canLeave]);
    const selectNode = useCallback((cluster: GalaxyCluster, node: GalaxyNode) => {
        if (!canLeave()) return;
        setWriteWorkspace("");
        const selection = { cluster, node }; selectionRef.current = selection; setSelected(cluster.clusterId); setNodeSelection(selection); setMode("summary"); setRelationOffset(0); setRelations({ relations: [], total: 0, nextOffset: null }); setTarget(""); setPredicate("RELATED_TO");
    }, [canLeave]);
    const refreshSelection = async (selection: Selection) => {
        const result = await readGraph(new URLSearchParams({ overview: "1", clusterId: selection.cluster.clusterId }));
        setClusters(current => current.map(item => item.clusterId === selection.cluster.clusterId ? result.items[0] : item));
        if (selectionRef.current === selection) {
            const page = await readGraph(new URLSearchParams({ overview: "1", clusterId: selection.cluster.clusterId, entity: selection.node.id }));
            if (selectionRef.current === selection) { setRelations(page); setRelationOffset(0); }
        }
    };
    const mutate = async (action: "add_relation" | "delete_relation" | "delete_entity", relation?: Relation) => {
        const selection = selectionRef.current;
        if (!selection || busy.current) return;
        const destination = selection.cluster.scopeKind === "global" ? clusters.find(item => item.workspaceKey === writeWorkspace) : selection.cluster;
        if (!destination?.workspaceKey || (selection.cluster.scopeKind === "global" && action !== "add_relation")) return;
        if (relation && relation.scope === "global") return;
        if (action === "delete_entity" && !window.confirm(t("admin.galaxy.deleteImpact", { name: selection.node.label, workspace: selection.cluster.label }))) return;
        if (action === "add_relation" && (!target.trim() || !predicate.trim())) return;
        busy.current = true; setMutating(true); setError("");
        try {
            const payload = { action, workspaceKey: destination.workspaceKey,
                ...(action === "delete_entity" ? { name: selection.node.id } : { subject: relation?.subject || selection.node.id, predicate: relation?.predicate || predicate.trim(), object: relation?.object || target.trim(), scope: relation?.scope, maintainerSource: "human_admin", confidence: 1 }) };
            const response = await fetch(GRAPH_URL, { method: action === "add_relation" ? "POST" : "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
            const result = await response.json();
            if (!response.ok || (action === "add_relation" ? result.created !== true : result.deleted !== true)) throw new Error(String(result.detail || result.error || t("admin.galaxy.notApplied")));
            if (destination.clusterId !== selection.cluster.clusterId) {
                const graph = await readGraph(new URLSearchParams({ overview: "1", clusterId: destination.clusterId }));
                const nextSelection = { cluster: graph.items[0], node: selection.node };
                setClusters(current => current.map(item => item.clusterId === destination.clusterId ? graph.items[0] : item));
                selectionRef.current = nextSelection; setNodeSelection(nextSelection); setSelected(destination.clusterId);
                setTarget(""); setPredicate("RELATED_TO"); setMode("disconnect");
            } else await refreshSelection(selection);
            if (selectionRef.current === selection) { setTarget(""); setPredicate("RELATED_TO"); setMode("disconnect"); if (action === "delete_entity") { setNodeSelection(null); selectionRef.current = null; (clusterTriggers.current.get(selection.cluster.clusterId) || overviewTrigger.current)?.focus({ preventScroll: true }); } }
        } catch (reason) { setError(`${t("admin.galaxy.writeFailed")} ${String(reason)}`); }
        finally { busy.current = false; setMutating(false); }
    };
    const visibleClusters = useMemo(() => clusters.filter(cluster => !query || cluster.label.toLowerCase().includes(query.toLowerCase()) || cluster.nodes.some(node => node.label.toLowerCase().includes(query.toLowerCase()))), [clusters, query]);
    const readOnly = nodeSelection?.cluster.scopeKind === "global";

    return <div ref={host} className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[150px] flex-1"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground"/><Input value={query} onChange={event => setQuery(event.target.value)} aria-label={t("admin.galaxy.search")} placeholder={t("admin.galaxy.search")} className="pl-9"/></div>
            <Button variant="outline" onClick={() => setPaused(value => !value)} aria-pressed={paused || reduced}>{paused || reduced ? <Play size={16} className="mr-2"/> : <Pause size={16} className="mr-2"/>}{t(paused || reduced ? "admin.galaxy.resume" : "admin.galaxy.pause")}</Button>
            <Button ref={overviewTrigger} variant="outline" onClick={background}><ArrowLeft size={16} className="mr-2"/>{t("admin.galaxy.overview")}</Button>
            <Button variant="ghost" size="icon" disabled={loading || mutating || Boolean(nodeSelection)} onClick={() => void load(page, true)} aria-label={t("admin.galaxy.refresh")}><RefreshCw size={16} className={loading ? "animate-spin" : ""}/></Button>
        </div>
        {error ? <div role="alert" className="rounded-lg border border-destructive/40 px-3 py-2 text-sm text-destructive">{error}</div> : null}
        <div className="relative rounded-xl border border-border bg-card">
            <GalaxyCanvas clusters={clusters} selected={selected} paused={paused} reduced={reduced} label={t("admin.galaxy.canvas")} onCluster={selectCluster} onNode={selectNode} onBackground={background}/>
            {loading && !clusters.length ? <div role="status" className="absolute inset-0 flex items-center justify-center gap-2"><Loader2 size={18} className="animate-spin"/>{t("admin.galaxy.loading")}</div> : null}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>{t("admin.galaxy.scopeHint", { count: total })}</span><span>{reduced ? t("admin.galaxy.reduced") : t("admin.galaxy.gestures")}</span></div>
        <div className="grid gap-3 md:grid-cols-[minmax(180px,1fr)_minmax(0,2fr)]">
            <div className="space-y-2">
                <Input aria-label={t("admin.galaxy.workspaceSearch")} placeholder={t("admin.galaxy.workspaceSearch")} value={workspaceSearch} disabled={mutating || draft} onChange={event => { if (canLeave()) { background(); setPage(0); setWorkspaceSearch(event.target.value); } }}/>
                <div className="max-h-[260px] space-y-1 overflow-auto" aria-label={t("admin.galaxy.clusters")}>
                    {visibleClusters.map(cluster => <button key={cluster.clusterId} ref={element => { if (element) clusterTriggers.current.set(cluster.clusterId, element); else clusterTriggers.current.delete(cluster.clusterId); }} type="button" aria-pressed={selected === cluster.clusterId} onClick={() => selectCluster(cluster.clusterId)} className={`flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm ${selected === cluster.clusterId ? "bg-accent text-primary" : "hover:bg-muted"}`}><span className="min-w-0 break-words">{cluster.scopeKind === "global" ? t("admin.galaxy.global") : cluster.label}</span><span className="shrink-0 text-xs text-muted-foreground">{cluster.meta.totalEntities} / {cluster.meta.totalRelations}</span></button>)}
                </div>
                {page > 0 || nextPage !== null ? <div className="flex gap-2"><Button variant="outline" size="sm" disabled={!page || mutating || draft} onClick={() => { background(); setPage(Math.max(0, page - 8)); }}>{t("admin.galaxy.previous")}</Button><Button variant="outline" size="sm" disabled={nextPage === null || mutating || draft} onClick={() => { background(); setPage(nextPage!); }}>{t("admin.galaxy.next")}</Button></div> : null}
            </div>
            <div className="min-w-0 space-y-3">
                {activeCluster ? <><div className="text-sm font-medium">{activeCluster.scopeKind === "global" ? t("admin.galaxy.global") : activeCluster.label}<span className="ml-2 text-xs font-normal text-muted-foreground">{t("admin.galaxy.rendered", { nodes: activeCluster.meta.renderedEntities, total: activeCluster.meta.totalEntities, edges: activeCluster.meta.renderedRelations, edgeTotal: activeCluster.meta.totalRelations })}</span></div><div className="flex max-h-[160px] flex-wrap gap-2 overflow-auto">{activeCluster.nodes.filter(node => !query || node.label.toLowerCase().includes(query.toLowerCase()) || activeCluster.label.toLowerCase().includes(query.toLowerCase())).map(node => <Button key={visualNodeId(activeCluster.clusterId, node.id)} variant="outline" size="sm" onClick={() => selectNode(activeCluster, node)}>{node.label}</Button>)}{!activeCluster.nodes.length ? <span className="text-sm text-muted-foreground">{t("admin.galaxy.empty")}</span> : null}</div></> : null}
                {nodeSelection ? createPortal(<section ref={menuRef} aria-label={t("admin.galaxy.nodeMenu")} className="admin-node-menu fixed right-3 top-[64px] z-40 grid max-h-[min(560px,calc(100dvh-80px))] w-[min(360px,calc(100vw-24px))] grid-rows-[auto_minmax(0,1fr)_auto] rounded-xl border border-border bg-card shadow-xl">
                    <div className="flex items-start justify-between gap-3 border-b border-border p-3"><div className="min-w-0"><h3 className="break-words text-sm font-semibold">{nodeSelection.node.label}</h3><p className="text-xs text-muted-foreground">{nodeSelection.node.type} · {nodeSelection.cluster.label} · {relations.total}</p></div><Button variant="ghost" size="icon" aria-label={t("admin.galaxy.close")} onClick={() => { if (canLeave()) { setNodeSelection(null); selectionRef.current = null; (clusterTriggers.current.get(nodeSelection.cluster.clusterId) || overviewTrigger.current)?.focus({ preventScroll: true }); } }}><X size={16}/></Button></div>
                    <div className="min-h-0 space-y-3 overflow-auto p-3">
                        {readOnly ? <p className="text-xs text-muted-foreground">{t("admin.galaxy.globalReadonly")}</p> : null}
                        {relationLoading ? <Loader2 size={16} className="animate-spin"/> : (mode === "summary" ? relations.relations.slice(0, 5) : relations.relations).map(relation => <div key={relation.relationId} className="flex items-start justify-between gap-2 border-b border-border/60 py-2 text-xs"><span className="break-all">{relation.subject} → {relation.predicate} → {relation.object}<span className="block text-muted-foreground">{relation.scope}</span></span>{mode === "disconnect" && !readOnly ? <Button variant="ghost" size="icon" disabled={mutating} aria-label={t("admin.galaxy.disconnect")} onClick={() => void mutate("delete_relation", relation)}><Unlink2 size={14}/></Button> : null}</div>)}
                        {mode === "connect" ? <div className="space-y-3">{readOnly ? <><Label htmlFor="galaxy-workspace">{t("admin.galaxy.chooseWorkspace")}</Label><select id="galaxy-workspace" className="h-10 w-full rounded-lg border border-input bg-background px-3 text-sm" value={writeWorkspace} disabled={mutating} onChange={event => setWriteWorkspace(event.target.value)}><option value="">{t("admin.galaxy.chooseWorkspace")}</option>{clusters.filter(item => item.workspaceKey).map(item => <option key={item.clusterId} value={item.workspaceKey!}>{item.label}</option>)}</select></> : null}<Label htmlFor="galaxy-target">{t("admin.galaxy.target")}</Label><Input id="galaxy-target" disabled={mutating} value={target} onChange={event => setTarget(event.target.value)} list="galaxy-targets"/><datalist id="galaxy-targets">{activeCluster?.nodes.filter(node => node.id !== nodeSelection.node.id).map(node => <option key={node.id} value={node.id}/>)}</datalist><p className="text-xs text-muted-foreground">{t("admin.galaxy.targetHint")}</p><Label htmlFor="galaxy-predicate">{t("admin.galaxy.predicate")}</Label><Input id="galaxy-predicate" disabled={mutating} value={predicate} onChange={event => setPredicate(event.target.value)}/></div> : null}
                        {mode !== "summary" && (relationOffset > 0 || relations.nextOffset !== null) ? <div className="flex gap-2"><Button size="sm" variant="outline" disabled={!relationOffset || relationLoading} onClick={() => setRelationOffset(Math.max(0, relationOffset - 30))}>{t("admin.galaxy.previous")}</Button><Button size="sm" variant="outline" disabled={relations.nextOffset === null || relationLoading} onClick={() => setRelationOffset(relations.nextOffset!)}>{t("admin.galaxy.next")}</Button></div> : null}
                    </div>
                    <div className="flex flex-wrap gap-2 border-t border-border p-3">
                        {mode === "connect" ? <><Button disabled={mutating || !target.trim() || !predicate.trim() || (readOnly && !writeWorkspace)} onClick={() => void mutate("add_relation")}>{mutating ? <Loader2 size={14} className="mr-2 animate-spin"/> : <Link2 size={14} className="mr-2"/>}{t("admin.galaxy.connect")}</Button><Button variant="outline" disabled={mutating} onClick={() => { setTarget(""); setPredicate("RELATED_TO"); setMode("root"); }}>{t("admin.galaxy.cancel")}</Button></> : <><Button variant="outline" size="sm" onClick={() => setMode("disconnect")}>{t("admin.galaxy.relations")}</Button>{readOnly ? <Button variant="outline" size="sm" onClick={() => setMode("connect")}>{t("admin.galaxy.createInWorkspace")}</Button> : <><Button variant="outline" size="sm" onClick={() => setMode("connect")}><Link2 size={14} className="mr-2"/>{t("admin.galaxy.connect")}</Button><Button variant="ghost" size="sm" disabled={mutating} onClick={() => void mutate("delete_entity")}><Trash2 size={14} className="mr-2"/>{t("admin.experience.delete")}</Button></>}</>}
                    </div>
                </section>, document.body) : null}
            </div>
        </div>
    </div>;
}
