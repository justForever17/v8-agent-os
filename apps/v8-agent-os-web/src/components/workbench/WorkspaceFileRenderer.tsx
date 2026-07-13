"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Copy, Download, FileWarning, MessageSquarePlus, RefreshCw, Search, Send, X } from "lucide-react";

import type { WorkspaceFileWorkbenchDocument } from "@/lib/workbench";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";


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

export type WorkspaceFileLineComment = {
    path: string;
    line: number;
    lineText: string;
    comment: string;
};

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
    const csp = "default-src 'none'; img-src data: blob:; media-src data: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data:; form-action 'none'; base-uri 'none'";
    const meta = `<meta http-equiv="Content-Security-Policy" content="${csp}">`;
    if (/<head[\s>]/i.test(content)) {
        return content.replace(/<head([^>]*)>/i, `<head$1>${meta}`);
    }
    return `<!doctype html><html><head>${meta}<meta charset="utf-8"></head><body>${content}</body></html>`;
}

function countMatches(value: string, query: string) {
    const needle = query.trim().toLowerCase();
    if (!needle) return 0;
    const haystack = value.toLowerCase();
    let count = 0;
    let cursor = 0;
    while ((cursor = haystack.indexOf(needle, cursor)) >= 0) {
        count += 1;
        cursor += Math.max(1, needle.length);
    }
    return count;
}

function highlightedSourceText(value: string, query: string) {
    const needle = query.trim();
    if (!needle) return value || " ";
    const lower = value.toLowerCase();
    const normalizedNeedle = needle.toLowerCase();
    const parts: Array<string | { value: string; key: number }> = [];
    let cursor = 0;
    let key = 0;
    let matchAt = lower.indexOf(normalizedNeedle, cursor);
    while (matchAt >= 0) {
        if (matchAt > cursor) parts.push(value.slice(cursor, matchAt));
        parts.push({ value: value.slice(matchAt, matchAt + needle.length), key: key++ });
        cursor = matchAt + needle.length;
        matchAt = lower.indexOf(normalizedNeedle, cursor);
    }
    if (!parts.length) return value || " ";
    if (cursor < value.length) parts.push(value.slice(cursor));
    return parts.map((part, index) => typeof part === "string"
        ? <span key={`text-${index}`}>{part}</span>
        : <mark key={`match-${part.key}`} data-workbench-search-match="true" className="rounded-sm bg-amber-300/35 text-inherit data-[active=true]:bg-amber-400/80">{part.value}</mark>);
}

export function WorkspaceFileRenderer({
    document,
    onSendLineComment,
}: {
    document: WorkspaceFileWorkbenchDocument;
    onSendLineComment?: (comment: WorkspaceFileLineComment) => Promise<boolean> | boolean;
}) {
    const { sessionId, workspacePath, line } = document.subjectRef;
    const initialStart = Math.max(1, Number(line || 1) - 80);
    const [startLine, setStartLine] = useState(initialStart);
    const [payload, setPayload] = useState<FilePayload | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [query, setQuery] = useState("");
    const [copied, setCopied] = useState(false);
    const [showPreview, setShowPreview] = useState(document.renderer === "markdown" || document.renderer === "html");
    const [activeMatchIndex, setActiveMatchIndex] = useState(0);
    const [commentLine, setCommentLine] = useState<FileLine | null>(null);
    const [commentText, setCommentText] = useState("");
    const [commentBusy, setCommentBusy] = useState(false);
    const contentRef = useRef<HTMLDivElement | null>(null);

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

    useEffect(() => {
        setStartLine(Math.max(1, Number(line || 1) - 80));
        setQuery("");
        setShowPreview(document.renderer === "markdown" || document.renderer === "html");
        setCommentLine(null);
        setCommentText("");
    }, [document.documentId, document.renderer, line]);

    const matchCount = useMemo(() => {
        const normalized = query.trim().toLowerCase();
        if (!normalized) return 0;
        return (payload?.lines || []).reduce((count, item) => count + countMatches(item.text, normalized), 0);
    }, [payload?.lines, query]);

    const focusMatch = useCallback((requestedIndex: number) => {
        const matches = Array.from(contentRef.current?.querySelectorAll<HTMLElement>("[data-workbench-search-match]") || []);
        if (!matches.length) return;
        const nextIndex = ((requestedIndex % matches.length) + matches.length) % matches.length;
        matches.forEach((match, index) => {
            if (index === nextIndex) match.dataset.active = "true";
            else delete match.dataset.active;
        });
        matches[nextIndex].scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
        setActiveMatchIndex(nextIndex);
    }, []);

    useEffect(() => {
        setActiveMatchIndex(0);
        if (!query.trim()) return;
        if (document.renderer === "html" && showPreview) setShowPreview(false);
        const timer = window.setTimeout(() => focusMatch(0), 40);
        return () => window.clearTimeout(timer);
    }, [document.renderer, focusMatch, payload?.content, payload?.lines, query, showPreview]);

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
    const canPreview = document.renderer === "markdown" || document.renderer === "html";

    const submitLineComment = async () => {
        if (!commentLine || !commentText.trim() || !onSendLineComment || commentBusy) return;
        setCommentBusy(true);
        try {
            const accepted = await onSendLineComment({
                path: payload.workspacePath || workspacePath,
                line: commentLine.number,
                lineText: commentLine.text,
                comment: commentText.trim(),
            });
            if (accepted !== false) {
                setCommentLine(null);
                setCommentText("");
            }
        } finally {
            setCommentBusy(false);
        }
    };

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
                        <span className="text-[10px] text-muted-foreground">{matchCount ? `${activeMatchIndex + 1} / ${matchCount}` : ""}</span>
                        {matchCount ? (
                            <div className="flex items-center">
                                <button type="button" onClick={() => focusMatch(activeMatchIndex - 1)} className="rounded-sm p-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="上一个匹配"><ChevronUp className="h-3.5 w-3.5" /></button>
                                <button type="button" onClick={() => focusMatch(activeMatchIndex + 1)} className="rounded-sm p-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="下一个匹配"><ChevronDown className="h-3.5 w-3.5" /></button>
                            </div>
                        ) : null}
                        {canPreview ? (
                            <button type="button" onClick={() => setShowPreview((value) => !value)} className="ml-auto rounded-sm px-2 py-1 text-[11px] hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary">
                                {showPreview ? "查看源码" : "查看预览"}
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
                    <div ref={contentRef} className="min-h-0 flex-1 overflow-auto">
                        {document.renderer === "html" && showPreview ? (
                            <iframe title={payload.name} srcDoc={safeHtmlPreview(content)} sandbox="allow-scripts" className="h-full min-h-[360px] w-full border-0 bg-white" />
                        ) : document.renderer === "markdown" && showPreview ? (
                            <article className="mx-auto max-w-3xl px-6 py-5"><MarkdownRenderer content={content} searchQuery={query} surface="document" /></article>
                        ) : (
                            <div className="min-w-max py-2 font-mono text-[12px] leading-5">
                                {(payload.lines || []).map((item) => (
                                    <div key={item.number} id={`L${item.number}`} className="group/line relative hover:bg-muted/45">
                                        <div className="flex min-h-5">
                                            <a href={`#L${item.number}`} className="sticky left-0 w-14 shrink-0 select-none border-r border-border/45 bg-background/96 pr-2 text-right text-muted-foreground/65">{item.number}</a>
                                            <code className="min-w-0 flex-1 whitespace-pre px-3 pr-10">{highlightedSourceText(item.text, query)}</code>
                                            {onSendLineComment ? (
                                                <button
                                                    type="button"
                                                    onClick={() => {
                                                        setCommentLine(item);
                                                        setCommentText("");
                                                    }}
                                                    className="sticky right-2 my-0.5 hidden h-5 w-5 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground shadow-sm group-hover/line:flex focus:flex focus-visible:ring-2 focus-visible:ring-primary"
                                                    aria-label={`评论第 ${item.number} 行`}
                                                    title={`评论第 ${item.number} 行`}
                                                >
                                                    <MessageSquarePlus className="h-3 w-3" />
                                                </button>
                                            ) : null}
                                        </div>
                                        {commentLine?.number === item.number ? (
                                            <div className="sticky left-14 right-2 z-10 ml-14 mr-2 mb-2 rounded-xl border border-border/70 bg-popover p-2 font-sans shadow-xl">
                                                <div className="mb-1 flex items-center gap-2 text-[10px] text-muted-foreground">
                                                    <span className="min-w-0 flex-1 truncate">{payload.name} · 第 {item.number} 行</span>
                                                    <button type="button" onClick={() => setCommentLine(null)} className="rounded p-1 hover:bg-muted" aria-label="取消评论"><X className="h-3 w-3" /></button>
                                                </div>
                                                <textarea
                                                    value={commentText}
                                                    onChange={(event) => setCommentText(event.target.value)}
                                                    onKeyDown={(event) => {
                                                        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                                                            event.preventDefault();
                                                            void submitLineComment();
                                                        }
                                                    }}
                                                    autoFocus
                                                    rows={2}
                                                    placeholder="说明希望如何修改这一行…"
                                                    className="w-full resize-none rounded-lg border border-border/60 bg-background px-2.5 py-2 text-xs text-foreground outline-none focus:border-primary/60 focus:ring-2 focus:ring-primary/15"
                                                />
                                                <div className="mt-2 flex justify-end gap-1.5">
                                                    <button type="button" onClick={() => setCommentLine(null)} className="rounded-md px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted">取消</button>
                                                    <button type="button" disabled={!commentText.trim() || commentBusy} onClick={() => void submitLineComment()} className="inline-flex items-center gap-1 rounded-md bg-primary px-2 py-1 text-[11px] text-primary-foreground disabled:opacity-45">
                                                        <Send className="h-3 w-3" />发送
                                                    </button>
                                                </div>
                                            </div>
                                        ) : null}
                                    </div>
                                ))}
                            </div>
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
