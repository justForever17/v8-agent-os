"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Copy, Download, FileWarning, RefreshCw, Search } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { WorkspaceFileWorkbenchDocument } from "@/lib/workbench";


type FileLine = { number: number; text: string };
type FilePayload = {
    workspacePath: string;
    name: string;
    mimeType: string;
    language?: string;
    encoding?: string;
    binary: boolean;
    previewable: boolean;
    size: number;
    mtime: string;
    etag: string;
    startLine?: number;
    endLine?: number;
    lineCount?: number;
    totalLines?: number | null;
    hasMore?: boolean;
    truncatedByBytes?: boolean;
    content?: string | null;
    lines?: FileLine[];
};

const PAGE_LINES = 500;

function formatBytes(value: number) {
    if (!Number.isFinite(value) || value < 1024) return `${Math.max(0, value || 0)} B`;
    const units = ["KB", "MB", "GB"];
    let current = value / 1024;
    let unit = units[0];
    for (let index = 1; index < units.length && current >= 1024; index += 1) {
        current /= 1024;
        unit = units[index];
    }
    return `${current.toFixed(current >= 10 ? 1 : 2)} ${unit}`;
}
function safeHtmlPreview(content: string) {
    const csp = "default-src 'none'; img-src data: blob:; media-src data: blob:; style-src 'unsafe-inline'; font-src data:; form-action 'none'; base-uri 'none'";
    const meta = `<meta http-equiv="Content-Security-Policy" content="${csp}">`;
    if (/<head[\s>]/i.test(content)) {
        return content.replace(/<head([^>]*)>/i, `<head$1>${meta}`);
    }
    return `<!doctype html><html><head>${meta}<meta charset="utf-8"></head><body>${content}</body></html>`;
}

export function WorkspaceFileRenderer({ document }: { document: WorkspaceFileWorkbenchDocument }) {
    const { sessionId, workspacePath, line } = document.subjectRef;
    const initialStart = Math.max(1, Number(line || 1) - 80);
    const [startLine, setStartLine] = useState(initialStart);
    const [payload, setPayload] = useState<FilePayload | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [query, setQuery] = useState("");
    const [copied, setCopied] = useState(false);
    const [markdownPreview, setMarkdownPreview] = useState(document.renderer === "markdown");

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const params = new URLSearchParams({
                path: workspacePath,
                startLine: String(startLine),
                lineCount: String(PAGE_LINES),
            });
            const response = await fetch(
                `/api/workbench/sessions/${encodeURIComponent(sessionId)}/files/read?${params.toString()}`,
                { cache: "no-store" },
            );
            const next = await response.json().catch(() => ({})) as FilePayload & { detail?: unknown; error?: unknown };
            if (!response.ok) {
                throw new Error(String(next.detail || next.error || `HTTP ${response.status}`));
            }
            setPayload(next);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setLoading(false);
        }
    }, [sessionId, startLine, workspacePath]);

    useEffect(() => {
        void load();
    }, [load]);

    const matchingLines = useMemo(() => {
        const normalized = query.trim().toLowerCase();
        if (!normalized) return new Set<number>();
        return new Set((payload?.lines || [])
            .filter((item) => item.text.toLowerCase().includes(normalized))
            .map((item) => item.number));
    }, [payload?.lines, query]);

    const downloadUrl = useMemo(() => {
        const params = new URLSearchParams({ path: workspacePath, download: "true" });
        return `/api/workbench/sessions/${encodeURIComponent(sessionId)}/files/read?${params.toString()}`;
    }, [sessionId, workspacePath]);

    if (loading && !payload) {
        return <div className="flex h-full items-center justify-center text-xs text-muted-foreground">正在读取文件…</div>;
    }
    if (error) {
        return (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center text-sm text-muted-foreground">
                <FileWarning className="h-6 w-6" />
                <span>{error}</span>
                <button type="button" onClick={() => void load()} className="rounded border border-border px-3 py-1.5 text-xs text-foreground focus-visible:ring-2 focus-visible:ring-primary">重试</button>
            </div>
        );
    }
    if (!payload) return null;

    const content = payload.content || "";
    const previousStart = Math.max(1, startLine - PAGE_LINES);
    const canGoPrevious = startLine > 1;
    const canGoNext = Boolean(payload.hasMore);

    return (
        <div className="flex h-full min-h-0 flex-col bg-background">
            <div className="flex h-8 shrink-0 items-center gap-2 border-b border-border/60 px-2 text-[11px] text-muted-foreground">
                <span className="min-w-0 flex-1 truncate font-mono" title={payload.workspacePath}>{payload.workspacePath}</span>
                <span>{formatBytes(payload.size)}</span>
                <span>{payload.encoding || "binary"}</span>
                <button type="button" onClick={() => void load()} className="rounded-sm p-1 hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary" aria-label="刷新文件"><RefreshCw className="h-3.5 w-3.5" /></button>
                <a href={downloadUrl} download={payload.name} className="rounded-sm p-1 hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary" aria-label="下载文件"><Download className="h-3.5 w-3.5" /></a>
            </div>
            {payload.binary ? (
                <div className="flex flex-1 flex-col items-center justify-center gap-2 px-8 text-center text-sm text-muted-foreground">
                    <FileWarning className="h-7 w-7" />
                    <div className="font-medium text-foreground">该文件不支持文本预览</div>
                    <div>{payload.mimeType || "application/octet-stream"} · {formatBytes(payload.size)}</div>
                    <a href={downloadUrl} download={payload.name} className="mt-2 rounded border border-border px-3 py-1.5 text-xs text-foreground hover:bg-muted">下载文件</a>
                </div>
            ) : (
                <>
                    <div className="flex h-8 shrink-0 items-center gap-1 border-b border-border/60 px-2">
                        <label className="flex h-6 min-w-0 max-w-64 flex-1 items-center gap-1.5 border-b border-border/80 px-1 text-xs focus-within:border-primary">
                            <Search className="h-3.5 w-3.5 text-muted-foreground" />
                            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="在当前片段搜索" className="min-w-0 flex-1 bg-transparent outline-none" />
                        </label>
                        <span className="text-[10px] text-muted-foreground">{matchingLines.size ? `${matchingLines.size} 处` : ""}</span>
                        {document.renderer === "markdown" ? (
                            <button type="button" onClick={() => setMarkdownPreview((value) => !value)} className="ml-auto rounded-sm px-2 py-1 text-[11px] hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary">
                                {markdownPreview ? "查看源码" : "查看预览"}
                            </button>
                        ) : <span className="ml-auto" />}
                        <button
                            type="button"
                            onClick={async () => {
                                await navigator.clipboard.writeText(content);
                                setCopied(true);
                                window.setTimeout(() => setCopied(false), 1200);
                            }}
                            className="rounded-sm p-1 hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary"
                            aria-label="复制当前片段"
                        >
                            {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                        </button>
                    </div>
                    <div className="min-h-0 flex-1 overflow-auto">
                        {document.renderer === "html" ? (
                            <iframe title={payload.name} srcDoc={safeHtmlPreview(content)} sandbox="" className="h-full min-h-[360px] w-full border-0 bg-white" />
                        ) : document.renderer === "markdown" && markdownPreview ? (
                            <article className="prose prose-sm dark:prose-invert mx-auto max-w-3xl px-6 py-5"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></article>
                        ) : (
                            <pre className="min-w-max py-2 font-mono text-[12px] leading-5">
                                {(payload.lines || []).map((item) => (
                                    <div key={item.number} id={`L${item.number}`} className={`flex min-h-5 ${matchingLines.has(item.number) ? "bg-amber-300/20" : "hover:bg-muted/45"}`}>
                                        <a href={`#L${item.number}`} className="sticky left-0 w-14 shrink-0 select-none border-r border-border/45 bg-background/96 pr-2 text-right text-muted-foreground/65">{item.number}</a>
                                        <code className="whitespace-pre px-3">{item.text || " "}</code>
                                    </div>
                                ))}
                            </pre>
                        )}
                    </div>
                    <div className="flex h-8 shrink-0 items-center justify-between border-t border-border/60 px-2 text-[10px] text-muted-foreground">
                        <span>行 {payload.startLine || 0}–{payload.endLine || 0} / {payload.totalLines ?? "—"}{payload.truncatedByBytes ? " · 已达 512 KiB 上限" : ""}</span>
                        <div className="flex items-center gap-1">
                            <button type="button" disabled={!canGoPrevious} onClick={() => setStartLine(previousStart)} className="rounded-sm p-1 disabled:opacity-35 hover:bg-muted" aria-label="上一段"><ChevronLeft className="h-3.5 w-3.5" /></button>
                            <button type="button" disabled={!canGoNext} onClick={() => setStartLine((payload.endLine || startLine) + 1)} className="rounded-sm p-1 disabled:opacity-35 hover:bg-muted" aria-label="下一段"><ChevronRight className="h-3.5 w-3.5" /></button>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
