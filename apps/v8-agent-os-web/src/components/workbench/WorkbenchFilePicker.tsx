"use client";

import { useEffect, useState } from "react";
import { FileAudio, FileCode2, FileImage, FileText, FileVideo, FolderOpen, Loader2, Search } from "lucide-react";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useT } from "@/components/providers/LocaleProvider";
import { listWorkspaceFiles, resolveAndOpenWorkspaceFile, type WorkbenchFileCatalogItem } from "@/lib/workbench-actions";

function iconFor(item: WorkbenchFileCatalogItem) {
    if (item.kind === "image") return FileImage;
    if (item.kind === "video") return FileVideo;
    if (item.kind === "audio") return FileAudio;
    if (item.kind === "code") return FileCode2;
    return FileText;
}

export function WorkbenchFilePicker({ sessionId, open, onOpenChange }: { sessionId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
    const t = useT();
    const [query, setQuery] = useState("");
    const [debouncedQuery, setDebouncedQuery] = useState("");
    const [items, setItems] = useState<WorkbenchFileCatalogItem[]>([]);
    const [nextCursor, setNextCursor] = useState<string | null>(null);
    const [hasMore, setHasMore] = useState(false);
    const [loading, setLoading] = useState(false);
    const [openingPath, setOpeningPath] = useState("");
    const [error, setError] = useState("");

    useEffect(() => {
        const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 180);
        return () => window.clearTimeout(timer);
    }, [query]);

    useEffect(() => {
        if (!open || !sessionId) return;
        const controller = new AbortController();
        setLoading(true);
        setError("");
        void listWorkspaceFiles(sessionId, { query: debouncedQuery, signal: controller.signal })
            .then((page) => {
                if (controller.signal.aborted) return;
                setItems(page.items);
                setNextCursor(page.nextCursor || null);
                setHasMore(page.hasMore);
            })
            .catch((reason) => {
                if (!controller.signal.aborted) {
                    setItems([]);
                    setError(reason instanceof Error ? reason.message : String(reason));
                }
            })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [debouncedQuery, open, sessionId]);

    const loadMore = async () => {
        if (!nextCursor || loading) return;
        setLoading(true);
        setError("");
        try {
            const page = await listWorkspaceFiles(sessionId, { query: debouncedQuery, cursor: nextCursor });
            if (page.sessionId !== sessionId) return;
            setItems((current) => [...current, ...page.items]);
            setNextCursor(page.nextCursor || null);
            setHasMore(page.hasMore);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setLoading(false);
        }
    };

    const openFile = async (item: WorkbenchFileCatalogItem) => {
        setOpeningPath(item.workspacePath);
        setError("");
        try {
            await resolveAndOpenWorkspaceFile(item.workspacePath, { sessionId, activate: true });
            onOpenChange(false);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setOpeningPath("");
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="flex max-h-[min(76dvh,680px)] min-h-[420px] w-[min(92vw,620px)] flex-col gap-0 overflow-hidden p-0 sm:max-w-[620px]">
                <DialogHeader className="border-b border-border/60 px-5 py-4">
                    <DialogTitle className="flex items-center gap-2 text-sm"><FolderOpen className="h-4 w-4" />{t("web.workbench.filePicker.title")}</DialogTitle>
                </DialogHeader>
                <div className="border-b border-border/60 p-3">
                    <label className="flex h-9 items-center gap-2 rounded-lg border border-border/70 bg-muted/20 px-3 focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/10">
                        <Search className="h-3.5 w-3.5 text-muted-foreground" />
                        <input value={query} onChange={(event) => setQuery(event.target.value)} autoFocus placeholder={t("web.workbench.filePicker.search")} className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground" />
                    </label>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-2">
                    {error ? <div className="m-2 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-2 text-xs text-destructive">{error}</div> : null}
                    {!loading && !error && !items.length ? <div className="flex h-56 items-center justify-center px-8 text-center text-xs text-muted-foreground">{t("web.workbench.filePicker.empty")}</div> : null}
                    {items.map((item) => {
                        const Icon = iconFor(item);
                        return <button key={item.workspacePath} type="button" disabled={openingPath === item.workspacePath} onClick={() => void openFile(item)} className="group flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-muted/55 focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-60">
                            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"><Icon className="h-4 w-4" /></span>
                            <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-foreground">{item.name}</span><span className="block truncate text-[10px] text-muted-foreground">{item.workspacePath}</span></span>
                            {openingPath === item.workspacePath ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" /> : null}
                        </button>;
                    })}
                    {loading ? <div className="flex items-center justify-center gap-2 py-5 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />{t("web.workbench.filePicker.loading")}</div> : null}
                    {hasMore && !loading ? <button type="button" onClick={() => void loadMore()} className="mx-auto my-2 block rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary">{t("web.workbench.filePicker.loadMore")}</button> : null}
                </div>
            </DialogContent>
        </Dialog>
    );
}
