"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { Blocks, Loader2, RefreshCw, Search } from "lucide-react";
import { AdminPageHeader } from "@admin/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@admin/components/admin-shell/AdminPageShell";
import { Button } from "@admin/components/ui/button";
import { Badge } from "@admin/components/ui/badge";
import { Input } from "@admin/components/ui/input";
import { Label } from "@admin/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@admin/components/ui/dialog";
import { useT } from "@admin/components/providers/LocaleProvider";
import { fetchAdminJson, peekAdminJsonCache } from "@admin/lib/admin-client-cache";
import { StoreRequestOwner, storeTarget, type StoreKind, type StoreProvider } from "@admin/lib/extensions-store-state";

const Markdown = dynamic(() => import("react-markdown"));
type Item = { id: string; name: string; source: string; skillId: string; description?: string; detailUrl: string; installed?: boolean; revision?: string };
type List = { items: Item[]; warnings?: string[]; hasMore?: boolean; sourceCoverage?: string };
type Candidate = { id: string; label: string; serverName: string; transport: string; command?: string; args?: string[];
    requirements?: { key: string; label: string; name: string; required: boolean; secret: boolean; target: string }[] };
type Detail = { markdown?: string; description?: string; candidates?: Candidate[]; configRevision?: string;
    configRevisions?: Record<string, string>; configured?: boolean; configuredServers?: string[]; isHosted?: boolean; setupUrl?: string; revision?: string };
type Selection = { item: Item; kind: StoreKind; provider: StoreProvider; target: string };
type Operation = { operationId: string; target: string; itemId: string; skillId?: string; candidateId?: string; source: string; kind: StoreKind; provider: StoreProvider; updatedAt?: number;
    status: string; phase: string; canCancel: boolean; message?: string; choices?: { name: string; folder: string }[]; result?: { installed?: unknown[]; skipped?: unknown[]; conflicts?: unknown[] } };
const SOURCE_KEY = "v8os.extensions.store.provider";
async function post<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`/api/admin/extensions/store/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail?.message || data.detail || data.error || `HTTP ${response.status}`);
    return data;
}

export default function ExtensionsStorePage() {
    const t = useT();
    const [provider, setProvider] = useState<StoreProvider>("international");
    const [ready, setReady] = useState(false);
    const [kind, setKind] = useState<StoreKind>("skills");
    const [query, setQuery] = useState("");
    const [search, setSearch] = useState("");
    const [composing, setComposing] = useState(false);
    const [page, setPage] = useState(1);
    const [list, setList] = useState<List>({ items: [] });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [refresh, setRefresh] = useState(0);
    const listOwner = useRef(new StoreRequestOwner());
    const detailOwner = useRef(new StoreRequestOwner());
    const detailAbort = useRef<AbortController | null>(null);
    const submitting = useRef(new Set<string>());
    const [pending, setPending] = useState<Record<string, boolean>>({});
    const [operations, setOperations] = useState<Record<string, Operation>>({});
    const [selection, setSelection] = useState<Selection | null>(null);
    const [detail, setDetail] = useState<Detail | null>(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [detailError, setDetailError] = useState("");
    const [candidateId, setCandidateId] = useState("");
    const [values, setValues] = useState<Record<string, string>>({});
    const [docs, setDocs] = useState(false);
    const [replace, setReplace] = useState(false);
    const [mirror, setMirror] = useState(false);
    const [connection, setConnection] = useState("");
    const [selectedSkill, setSelectedSkill] = useState("");
    useEffect(() => {
        let saved = "international";
        try { saved = localStorage.getItem(SOURCE_KEY) || saved; } catch { /* storage can be unavailable */ }
        const params = new URLSearchParams(window.location.search);
        setProvider(saved === "modelscope" ? "modelscope" : "international");
        setKind(params.get("kind") === "mcp" ? "mcp" : "skills");
        setQuery(params.get("query") || ""); setSearch(params.get("query") || ""); setReady(true);
        const details = detailOwner.current, lists = listOwner.current;
        return () => { detailAbort.current?.abort(); details.invalidate(); lists.invalidate(); };
    }, []);
    useEffect(() => {
        if (composing) return;
        const timer = setTimeout(() => { setSearch(query.trim()); setPage(1); }, 320);
        return () => clearTimeout(timer);
    }, [query, composing]);
    useEffect(() => {
        if (!ready) return;
        window.history.replaceState(null, "", `${window.location.pathname}?${new URLSearchParams({ kind, query: search })}`);
        const url = `/api/admin/extensions/store/${kind}?${new URLSearchParams({ provider, query: search, limit: "30", page: String(page) })}`;
        const owner = listOwner.current, generation = owner.begin(url);
        const cached = peekAdminJsonCache<List>(url);
        setLoading(true); setError("");
        if (page === 1) setList(cached || { items: [] });
        void fetchAdminJson<List>(refresh ? `${url}&refresh=true` : url, { force: Boolean(refresh) }).then(data => {
            if (!owner.accepts(generation)) return;
            setList(previous => ({ ...data, items: page === 1 ? data.items : [...previous.items, ...data.items.filter(item => !previous.items.some(old => old.id === item.id))] }));
        }).catch(err => { if (owner.accepts(generation)) setError(String(err.message || err)); })
          .finally(() => { if (owner.accepts(generation)) setLoading(false); });
        return () => owner.invalidate();
    }, [kind, provider, search, page, refresh, ready]);
    const mergeOperation = useCallback((op: Operation) => setOperations(previous => ({ ...previous, [op.target]: op })), []);
    const hasRunning = Object.values(operations).some(op => op.status === "running");
    useEffect(() => {
        let active = true;
        let timer: ReturnType<typeof setTimeout>;
        const load = async () => {
            try {
                const data = await fetchAdminJson<{ operations: Operation[] }>("/api/admin/extensions/store/operations", { force: true });
                if (active) setOperations(previous => {
                    const next = { ...previous };
                    for (const op of data.operations) if (!next[op.target] || (op.updatedAt || 0) >= (next[op.target].updatedAt || 0)) next[op.target] = op;
                    return next;
                });
            } catch { /* list remains usable, explicit submit errors are shown */ }
            if (active && hasRunning) timer = setTimeout(load, document.hidden ? 10000 : 1500);
        };
        void load();
        return () => { active = false; clearTimeout(timer); };
    }, [hasRunning]);
    const closeDetail = () => { detailAbort.current?.abort(); detailOwner.current.invalidate(); setSelection(null); setValues({}); };
    const switchProvider = () => {
        const next = provider === "international" ? "modelscope" : "international";
        listOwner.current.invalidate(); closeDetail(); setList({ items: [] }); setProvider(next); setPage(1); setRefresh(0);
        try { localStorage.setItem(SOURCE_KEY, next); } catch { /* preference persistence is optional */ }
    };
    const openDetail = async (item: Item, source = provider, tab = kind) => {
        detailAbort.current?.abort();
        const chosen = { item, provider: source, kind: tab, target: storeTarget(source, tab, item) };
        const generation = detailOwner.current.begin(chosen.target);
        const controller = new AbortController(); detailAbort.current = controller;
        setSelection(chosen); setDetail(null); setDetailLoading(true); setDetailError(""); setValues({});
        setCandidateId(""); setDocs(false); setReplace(false); setMirror(false); setConnection("");
        setSelectedSkill("");
        const params = new URLSearchParams(tab === "skills" ? { source: item.source, skillId: item.skillId, provider: source } : { id: item.id, provider: source });
        try {
            const response = await fetch(`/api/admin/extensions/store/${tab}/detail?${params}`, { signal: controller.signal });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${response.status}`);
            if (detailOwner.current.accepts(generation)) { setDetail(data); setCandidateId(operations[chosen.target]?.candidateId || data.candidates?.[0]?.id || ""); }
        } catch (err) { if (detailOwner.current.accepts(generation) && !controller.signal.aborted) setDetailError((err as Error).message); }
        finally { if (detailOwner.current.accepts(generation)) setDetailLoading(false); }
    };
    const candidate = detail?.candidates?.find(item => item.id === candidateId);
    const chosenOperation = selection ? operations[selection.target] : undefined;
    const busy = selection ? pending[selection.target] || chosenOperation?.status === "running" : false;
    const install = async () => {
        if (!selection || !detail || detailLoading || !detailOwner.current.isCurrent(selection.target)) return;
        if (selection.kind === "mcp" && !candidate) return;
        const chosen = selection;
        const generation = detailOwner.current.capture();
        if (submitting.current.has(chosen.target)) return;
        submitting.current.add(chosen.target); setPending(old => ({ ...old, [chosen.target]: true })); setDetailError("");
        try {
            const operation = await post<Operation>(`operations/${chosen.kind}`, {
                provider: chosen.provider, source: chosen.item.source, skillId: chosen.item.skillId, id: chosen.item.id,
                candidateId: candidate?.id, values, overwrite: replace, replace, revision: detail.revision || chosen.item.revision,
                selectedSkill: selectedSkill || undefined,
                retry: Boolean(chosenOperation), configRevision: detail.configRevision || detail.configRevisions?.[candidate?.serverName || ""],
                packageSource: mirror ? "domestic" : "original",
            });
            mergeOperation(operation);
        } catch (err) { if (detailOwner.current.accepts(generation)) setDetailError((err as Error).message); }
        finally { submitting.current.delete(chosen.target); setPending(old => ({ ...old, [chosen.target]: false })); }
    };
    const statusLabel = (op?: Operation) => {
        if (!op) return "";
        if (op.status === "completed") return t(op.result?.conflicts?.length ? "extensions.store.partial" : op.kind === "mcp" ? "extensions.store.configured" : !op.result?.installed?.length && op.result?.skipped?.length ? "extensions.store.unchanged" : "extensions.store.installed");
        return t(`extensions.store.${op.status === "running" ? op.phase : op.status}`);
    };
    const cancel = (op: Operation) => void post<Operation>(`operations/${op.operationId}/cancel`, {}).then(mergeOperation).catch(err => setError(err.message));
    const waitForSetup = () => {
        if (!selection) return;
        const chosen = selection;
        void post<Operation>("operations/mcp", { provider: chosen.provider, id: chosen.item.id, source: chosen.item.source,
            candidateId, waitForInput: true, retry: true }).then(mergeOperation).catch(err => {
                if (detailOwner.current.isCurrent(chosen.target)) setDetailError(err.message);
            });
    };
    const operationSourceUrl = (op: Operation) => op.provider === "modelscope"
        ? `https://modelscope.cn/${op.kind === "skills" ? "skills" : "mcp/servers"}/${op.itemId}`
        : op.kind === "skills" ? `https://skills.sh/${op.source}/${op.skillId}` : `https://github.com/mcp/${op.itemId}`;
    return <AdminPageShell className="max-w-[var(--v8-product-settings-width,1040px)] gap-4">
        <AdminPageHeader title="app.admin.dashboard.extensions.store.page.title" actions={<Button size="sm" variant="outline" asChild><Link href="/admin/extensions"><Blocks className="mr-2 h-4 w-4" />{t("extensions.store.manage")}</Link></Button>} />
        <div className="sticky top-0 z-10 space-y-3 bg-background py-2">
            <div className="flex flex-wrap items-center gap-2">
                <div className="flex gap-1" aria-label={t("extensions.store.kind")}>{(["skills", "mcp"] as const).map(value => <Button key={value} size="sm" variant={kind === value ? "default" : "ghost"} aria-pressed={kind === value} onClick={() => { listOwner.current.invalidate(); closeDetail(); setList({ items: [] }); setKind(value); setPage(1); setRefresh(0); }}>{value === "skills" ? "Skills" : "MCP"}</Button>)}</div>
                <div className="relative min-w-48 flex-1"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input aria-label={t("extensions.store.search")} className="h-10 pl-9" value={query} placeholder={t("extensions.store.search")} onChange={event => setQuery(event.target.value)} onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { setSearch(query.trim()); setPage(1); } }} /></div>
                <Button size="icon" variant="outline" aria-label={t("extensions.store.refresh")} title={t("extensions.store.refresh")} onClick={() => { setPage(1); setRefresh(Date.now()); }} disabled={loading}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin motion-reduce:animate-none" : ""}`} /></Button>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><Badge variant="secondary">{t(provider === "international" ? "extensions.store.international" : "extensions.store.domestic")}</Badge><Button size="sm" variant="ghost" onClick={switchProvider}>{t(provider === "international" ? "extensions.store.toDomestic" : "extensions.store.toInternational")}</Button><span>{t("extensions.store.manualSource")}</span></div>
        </div>
        {error && <p role="alert" className="rounded-lg border border-destructive/30 p-3 text-sm text-destructive">{error}</p>}
        {list.warnings?.map(warning => <p key={warning} className="text-sm text-muted-foreground">{warning}</p>)}
        {loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />{t("extensions.store.loading")}</p>}
        <div className="grid gap-2 sm:grid-cols-2" aria-busy={loading}>{list.items.map(item => {
            const target = storeTarget(provider, kind, item), op = operations[target];
            return <article key={target} className="flex min-h-24 items-center gap-3 rounded-[var(--v8-product-panel-radius,12px)] border border-border bg-card px-3 py-2"><button className="min-w-0 flex-1 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => void openDetail(item)}><span className="block truncate text-sm font-medium">{item.name}</span><span className="mt-1 line-clamp-2 text-xs text-muted-foreground">{item.description || item.source || item.id}</span><span className="mt-1 block truncate text-[11px] text-muted-foreground">{item.source || item.id}</span></button><Button size="sm" variant="outline" className="max-w-32 shrink-0" onClick={() => void openDetail(item)}><span className="truncate">{statusLabel(op) || t(item.installed ? "extensions.store.view" : kind === "skills" ? "extensions.store.install" : "extensions.store.setup")}</span></Button></article>;
        })}</div>
        {!loading && !list.items.length && !error && <p className="py-8 text-center text-sm text-muted-foreground">{t("extensions.store.empty")}</p>}
        <div className="flex items-center justify-between text-xs text-muted-foreground"><span>{t("extensions.store.count", { count: list.items.length })} · {t(list.sourceCoverage === "catalog" ? "extensions.store.catalog" : "extensions.store.curated")}</span>{list.hasMore && <Button size="sm" variant="outline" disabled={loading} onClick={() => { setRefresh(0); setPage(value => value + 1); }}>{t("extensions.store.more")}</Button>}</div>
        {Object.values(operations).length > 0 && <details className="rounded-lg border p-3 text-sm" open={hasRunning}><summary className="cursor-pointer">{t("extensions.store.operations")}</summary><div className="mt-2 max-h-48 space-y-2 overflow-auto">{Object.values(operations).map(op => <div key={op.operationId} className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate">{op.itemId}</span><span className="text-xs text-muted-foreground">{statusLabel(op)}</span>{op.canCancel && <Button size="sm" variant="ghost" onClick={() => cancel(op)}>{t("extensions.store.cancel")}</Button>}<Button size="sm" variant="ghost" onClick={() => void openDetail({ id: op.itemId, name: op.itemId, source: op.source, skillId: op.skillId || op.itemId, detailUrl: operationSourceUrl(op) }, op.provider, op.kind)}>{t("extensions.store.continue")}</Button></div>)}</div></details>}
        <Dialog open={Boolean(selection)} onOpenChange={open => { if (!open) closeDetail(); }}><DialogContent className="grid max-h-[88dvh] w-[calc(100%-2rem)] max-w-2xl grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0">
            <DialogHeader className="border-b px-5 py-4 pr-12"><DialogTitle className="truncate">{selection?.item.name}</DialogTitle><DialogDescription className="truncate">{selection?.item.source || selection?.item.id}</DialogDescription></DialogHeader>
            <div className="min-h-0 overflow-y-auto overscroll-contain p-5"><div className="mb-4 flex gap-2"><Button size="sm" variant={!docs ? "secondary" : "ghost"} onClick={() => setDocs(false)}>{t("extensions.store.setup")}</Button><Button size="sm" variant={docs ? "secondary" : "ghost"} onClick={() => setDocs(true)}>{t("extensions.store.docs")}</Button><Button size="sm" variant="ghost" asChild><a href={selection?.item.detailUrl} target="_blank" rel="noreferrer">{t("extensions.store.source")}</a></Button></div>
                {detailLoading && <p role="status">{t("extensions.store.loading")}</p>}{detailError && <p role="alert" className="mb-3 text-sm text-destructive">{detailError}</p>}
                {detail && docs && <div className="max-w-none break-words text-sm leading-relaxed [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-5 [&_h2]:font-semibold [&_p]:my-2 [&_pre]:overflow-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 [&_a]:underline [&_ul]:list-disc [&_ul]:pl-5"><Markdown>{detail.markdown || detail.description || ""}</Markdown></div>}
                {chosenOperation?.choices?.length ? <Label className="block space-y-2">{t("extensions.store.chooseSkill")}<select className="h-10 w-full rounded-md border bg-background px-3" value={selectedSkill} onChange={event => setSelectedSkill(event.target.value)}><option value="">{t("extensions.store.chooseSkill")}</option>{chosenOperation.choices.map(choice => <option key={choice.folder} value={choice.folder}>{choice.name}</option>)}</select></Label> : null}
                {detail && !docs && <div className="space-y-4 text-sm">{selection?.kind === "skills" ? <><p>{t("extensions.store.skillContents")}</p><p className="text-xs text-muted-foreground">{t("extensions.store.skillReady")}</p><Label className="flex items-center gap-2"><input type="checkbox" checked={replace} onChange={event => setReplace(event.target.checked)} />{t("extensions.store.overwrite")}</Label></> : <>
                    <Label htmlFor="store-candidate">{t("extensions.store.execution")}</Label><select id="store-candidate" className="h-10 w-full rounded-md border border-input bg-background px-3" value={candidateId} onChange={event => { setCandidateId(event.target.value); setValues({}); }} disabled={busy}>{detail.candidates?.map(choice => <option value={choice.id} key={choice.id}>{choice.label}</option>)}</select>
                    {detail.isHosted && <div className="rounded-lg bg-muted/40 p-3"><p>{t("extensions.store.hostedStep")}</p><a className="mt-2 inline-block underline underline-offset-4" href={detail.setupUrl} target="_blank" rel="noreferrer" onClick={waitForSetup}>{t("extensions.store.goConfigure")}</a></div>}
                    <p className="text-xs text-muted-foreground">{t(candidate?.transport === "stdio" ? "extensions.store.localExecution" : "extensions.store.cloudExecution")}</p>
                    {candidate?.requirements?.map(field => <div key={field.key} className="space-y-1.5"><Label htmlFor={`store-${field.key}`}>{field.label || field.name}{field.required ? " *" : ""}</Label><Input id={`store-${field.key}`} type={field.secret || field.target === "url" ? "password" : "text"} autoComplete="off" value={values[field.key] || ""} onChange={event => setValues(old => ({ ...old, [field.key]: event.target.value }))} disabled={busy} /></div>)}
                    {candidate?.transport === "stdio" && <><Label className="flex items-center gap-2"><input type="checkbox" checked={mirror} onChange={event => setMirror(event.target.checked)} />{t("extensions.store.mirror")}</Label><p className="text-xs text-muted-foreground">{t("extensions.store.mirrorLimit")}</p><details><summary className="cursor-pointer">{t("extensions.store.command")}</summary><pre className="mt-2 overflow-auto rounded bg-muted p-2 text-xs">{candidate.command} {candidate.args?.join(" ")}</pre></details></>}
                    {(detail.configured || detail.configuredServers?.includes(candidate?.serverName || "")) && <Label className="flex items-center gap-2"><input type="checkbox" checked={replace} onChange={event => setReplace(event.target.checked)} />{t("extensions.store.replace")}</Label>}{!candidate && <p>{t("extensions.store.noCandidate")}</p>}
                </>}{chosenOperation && <div role="status" className="rounded-lg border p-3"><p>{statusLabel(chosenOperation)}</p>{chosenOperation.message && <p className="mt-1 text-xs text-muted-foreground">{chosenOperation.message}</p>}</div>}{connection && <p role="status">{connection}</p>}</div>}
            </div>
            <DialogFooter className="flex-wrap border-t bg-background px-5 py-3">{chosenOperation && <span role="status" className="mr-auto self-center text-xs text-muted-foreground">{statusLabel(chosenOperation)}</span>}{chosenOperation?.canCancel && <Button variant="outline" onClick={() => cancel(chosenOperation)}>{t("extensions.store.cancel")}</Button>}
                {selection?.kind === "mcp" && candidate && <Button variant="outline" onClick={async () => { const target = selection.target; try { const data = await post<{ connection: { status: string; lastError?: string } }>("mcp/check", { serverName: candidate.serverName }); if (detailOwner.current.isCurrent(target)) setConnection(data.connection.lastError || t(data.connection.status === "connected" ? "extensions.store.connected" : "extensions.store.connecting")); } catch (err) { if (detailOwner.current.isCurrent(target)) setDetailError((err as Error).message); } }}>{t("extensions.store.check")}</Button>}
                <Button disabled={Boolean(busy || !detail || detailLoading || selection?.kind === "mcp" && !candidate)} onClick={() => void install()}>{busy && <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />}{t(chosenOperation ? "extensions.store.continue" : selection?.kind === "mcp" ? "extensions.store.connect" : "extensions.store.install")}</Button>
            </DialogFooter>
        </DialogContent></Dialog>
    </AdminPageShell>;
}
