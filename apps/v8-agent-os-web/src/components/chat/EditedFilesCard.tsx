"use client";

import React, { useState, useRef, useMemo } from "react";
import { Code2, Undo2, ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "@/lib/utils";

export interface EditedFileItem {
    id: string;
    filePath: string;
    fileName: string;
    additions: number;
    deletions: number;
    diff?: string;
    onClick?: () => void;
    onUndo?: () => void;
}

export interface EditedFilesCardProps {
    files: EditedFileItem[];
    totalAdditions?: number;
    totalDeletions?: number;
    onUndoAll?: () => void;
    className?: string;
}

interface ParsedDiffLine {
    type: "add" | "delete" | "header" | "normal";
    text: string;
    oldLineNumber?: number;
    newLineNumber?: number;
}

function parseUnifiedDiff(diffText: string): ParsedDiffLine[] {
    if (!diffText) return [];
    const lines = diffText.split("\n");
    const result: ParsedDiffLine[] = [];
    let oldLine = 1;
    let newLine = 1;

    for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, "");
        if (line.startsWith("@@")) {
            const match = line.match(/@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/);
            if (match) {
                oldLine = parseInt(match[1], 10);
                newLine = parseInt(match[2], 10);
            }
            result.push({ type: "header", text: line });
        } else if (line.startsWith("+") && !line.startsWith("+++")) {
            result.push({
                type: "add",
                text: line.slice(1),
                newLineNumber: newLine++,
            });
        } else if (line.startsWith("-") && !line.startsWith("---")) {
            result.push({
                type: "delete",
                text: line.slice(1),
                oldLineNumber: oldLine++,
            });
        } else {
            result.push({
                type: "normal",
                text: line.startsWith(" ") ? line.slice(1) : line,
                oldLineNumber: oldLine++,
                newLineNumber: newLine++,
            });
        }
    }
    return result;
}

export function EditedFilesCard({
    files,
    totalAdditions: customAdditions,
    totalDeletions: customDeletions,
    onUndoAll,
    className,
}: EditedFilesCardProps) {
    const [expanded, setExpanded] = useState(false);
    const [hoveredFileId, setHoveredFileId] = useState<string | null>(null);
    const [hoverPosition, setHoverPosition] = useState<{ top: number; left: number } | null>(null);
    const hoverTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const cardRef = useRef<HTMLDivElement>(null);

    const calculatedAdditions = useMemo(
        () => files.reduce((acc, f) => acc + (f.additions || 0), 0),
        [files]
    );
    const calculatedDeletions = useMemo(
        () => files.reduce((acc, f) => acc + (f.deletions || 0), 0),
        [files]
    );

    const totalAdditions = customAdditions ?? calculatedAdditions;
    const totalDeletions = customDeletions ?? calculatedDeletions;

    const visibleFiles = expanded ? files : files.slice(0, 3);
    const remainingCount = Math.max(0, files.length - 3);

    const hoveredFile = useMemo(
        () => files.find((f) => f.id === hoveredFileId) || null,
        [files, hoveredFileId]
    );

    const parsedDiffLines = useMemo(() => {
        if (!hoveredFile?.diff) return [];
        return parseUnifiedDiff(hoveredFile.diff);
    }, [hoveredFile?.diff]);

    const handleMouseEnterFile = (e: React.MouseEvent<HTMLDivElement>, file: EditedFileItem) => {
        if (hoverTimeoutRef.current) {
            clearTimeout(hoverTimeoutRef.current);
            hoverTimeoutRef.current = null;
        }
        if (!file.diff) {
            setHoveredFileId(null);
            return;
        }
        const rect = e.currentTarget.getBoundingClientRect();
        const cardRect = cardRef.current?.getBoundingClientRect();
        if (cardRect) {
            setHoverPosition({
                top: rect.top - cardRect.top - 10,
                left: Math.min(cardRect.width - 20, 20),
            });
        }
        setHoveredFileId(file.id);
    };

    const handleMouseLeaveFile = () => {
        hoverTimeoutRef.current = setTimeout(() => {
            setHoveredFileId(null);
        }, 150);
    };

    const handleHoverPopoverEnter = () => {
        if (hoverTimeoutRef.current) {
            clearTimeout(hoverTimeoutRef.current);
            hoverTimeoutRef.current = null;
        }
    };

    if (!files.length) return null;

    return (
        <div
            ref={cardRef}
            className={cn(
                "relative my-2 w-full rounded-xl border border-border/75 bg-card/85 p-3 text-card-foreground shadow-sm backdrop-blur-md transition-all dark:border-white/10 dark:bg-[#141416] dark:text-zinc-200 dark:shadow-md",
                className
            )}
        >
            {/* Header: Title + Stats on Left, Undo button on Right */}
            <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2.5">
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border/80 bg-muted/60 text-foreground/80 dark:border-white/10 dark:bg-zinc-800/80 dark:text-zinc-300">
                        <Code2 className="h-4 w-4" />
                    </div>
                    <div className="flex flex-col">
                        <span className="text-xs font-semibold text-foreground dark:text-zinc-100">
                            已编辑 {files.length} 个文件
                        </span>
                        <div className="flex items-center gap-1.5 font-mono text-[11px] font-medium leading-none">
                            <span className="text-emerald-600 dark:text-emerald-400">+{totalAdditions}</span>
                            <span className="text-rose-600 dark:text-rose-400">-{totalDeletions}</span>
                        </div>
                    </div>
                </div>

                {onUndoAll && (
                    <button
                        type="button"
                        onClick={onUndoAll}
                        className="group flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground dark:text-zinc-400 dark:hover:bg-white/5 dark:hover:text-zinc-200 focus-visible:outline-none"
                    >
                        <span>撤销</span>
                        <Undo2 className="h-3.5 w-3.5 transition-transform group-hover:-rotate-45" />
                    </button>
                )}
            </div>

            {/* Separator */}
            <div className="my-2.5 h-[1px] w-full bg-border/60 dark:bg-white/5" />

            {/* File items list */}
            <div className="space-y-0.5">
                {visibleFiles.map((file) => (
                    <div
                        key={file.id}
                        onMouseEnter={(e) => handleMouseEnterFile(e, file)}
                        onMouseLeave={handleMouseLeaveFile}
                        onClick={file.onClick}
                        className="group flex cursor-pointer items-center justify-between gap-4 rounded-md px-2 py-1 text-xs transition-colors hover:bg-muted/60 dark:hover:bg-white/[0.04]"
                    >
                        <span
                            className="truncate font-mono text-[11px] text-foreground/85 transition-colors group-hover:text-foreground dark:text-zinc-300 dark:group-hover:text-zinc-100"
                            title={file.filePath}
                        >
                            {file.filePath}
                        </span>
                        <div className="flex shrink-0 items-center gap-1.5 font-mono text-[11px] font-medium">
                            {file.additions > 0 && (
                                <span className="text-emerald-600 dark:text-emerald-400">+{file.additions}</span>
                            )}
                            {file.deletions > 0 && (
                                <span className="text-rose-600 dark:text-rose-400">-{file.deletions}</span>
                            )}
                            {file.additions === 0 && file.deletions === 0 && (
                                <span className="text-muted-foreground dark:text-zinc-500">~0</span>
                            )}
                        </div>
                    </div>
                ))}
            </div>

            {/* Expand / Collapse Disclosure */}
            {remainingCount > 0 && (
                <button
                    type="button"
                    onClick={() => setExpanded(!expanded)}
                    className="mt-2 flex w-full items-center justify-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground dark:text-zinc-400 dark:hover:text-zinc-200 focus-visible:outline-none"
                >
                    <span>
                        {expanded ? "收起" : `再显示 ${remainingCount} 个文件`}
                    </span>
                    {expanded ? (
                        <ChevronUp className="h-3.5 w-3.5" />
                    ) : (
                        <ChevronDown className="h-3.5 w-3.5" />
                    )}
                </button>
            )}

            {/* Hover Diff Popover (Dual-theme support matching Screenshots 1 & 2) */}
            {hoveredFile && parsedDiffLines.length > 0 && (
                <div
                    onMouseEnter={handleHoverPopoverEnter}
                    onMouseLeave={handleMouseLeaveFile}
                    style={{
                        top: hoverPosition ? `${Math.max(10, hoverPosition.top - 180)}px` : "-180px",
                        left: hoverPosition ? `${hoverPosition.left}px` : "20px",
                    }}
                    className="pointer-events-auto absolute z-50 w-[94%] max-w-2xl rounded-xl border border-border/90 bg-popover/95 text-popover-foreground shadow-xl backdrop-blur-xl animate-in fade-in zoom-in-95 duration-150 dark:border-zinc-700/80 dark:bg-[#0d1117]/95 dark:text-zinc-200 dark:shadow-2xl"
                >
                    {/* Popover Header */}
                    <div className="flex items-center justify-between border-b border-border/70 bg-muted/40 px-3 py-1.5 text-xs dark:border-zinc-800/80 dark:bg-zinc-900/90">
                        <span className="truncate font-mono font-medium text-foreground dark:text-zinc-200">
                            {hoveredFile.filePath}
                        </span>
                        <div className="flex shrink-0 items-center gap-1.5 font-mono text-[11px] font-medium">
                            <span className="text-emerald-600 dark:text-emerald-400">+{hoveredFile.additions}</span>
                            <span className="text-rose-600 dark:text-rose-400">-{hoveredFile.deletions}</span>
                        </div>
                    </div>

                    {/* Popover Diff Body */}
                    <div className="max-h-60 overflow-x-auto overflow-y-auto p-2 font-mono text-[11px] leading-5">
                        {parsedDiffLines.map((line, idx) => {
                            if (line.type === "header") {
                                return (
                                    <div
                                        key={idx}
                                        className="text-sky-700 dark:text-sky-400/90 select-none py-0.5"
                                    >
                                        {line.text}
                                    </div>
                                );
                            }
                            if (line.type === "add") {
                                return (
                                    <div
                                        key={idx}
                                        className="relative flex items-center bg-emerald-500/10 text-emerald-800 hover:bg-emerald-500/15 dark:bg-emerald-950/35 dark:text-emerald-300 dark:hover:bg-emerald-950/50"
                                    >
                                        <div className="w-1 absolute left-0 top-0 bottom-0 bg-emerald-600 dark:bg-emerald-500" />
                                        <span className="w-10 select-none pl-2 text-right text-emerald-700/70 dark:text-emerald-600/70 shrink-0">
                                            {line.newLineNumber}
                                        </span>
                                        <span className="ml-3 select-none text-emerald-600 dark:text-emerald-500 shrink-0">+</span>
                                        <span className="ml-1 whitespace-pre pr-2 font-mono">{line.text}</span>
                                    </div>
                                );
                            }
                            if (line.type === "delete") {
                                return (
                                    <div
                                        key={idx}
                                        className="relative flex items-center bg-rose-500/10 text-rose-800 hover:bg-rose-500/15 dark:bg-rose-950/35 dark:text-rose-300 dark:hover:bg-rose-950/50"
                                    >
                                        <div className="w-1 absolute left-0 top-0 bottom-0 bg-rose-600 dark:bg-rose-500" />
                                        <span className="w-10 select-none pl-2 text-right text-rose-700/70 dark:text-rose-600/70 shrink-0">
                                            {line.oldLineNumber}
                                        </span>
                                        <span className="ml-3 select-none text-rose-600 dark:text-rose-500 shrink-0">-</span>
                                        <span className="ml-1 whitespace-pre pr-2 font-mono">{line.text}</span>
                                    </div>
                                );
                            }
                            return (
                                <div
                                    key={idx}
                                    className="flex items-center text-foreground/75 hover:bg-muted/30 dark:text-zinc-400 dark:hover:bg-white/[0.02]"
                                >
                                    <span className="w-10 select-none pl-2 text-right text-muted-foreground/60 dark:text-zinc-600 shrink-0">
                                        {line.newLineNumber || line.oldLineNumber || ""}
                                    </span>
                                    <span className="ml-3 select-none text-transparent shrink-0"> </span>
                                    <span className="ml-1 whitespace-pre pr-2 font-mono text-foreground/90 dark:text-zinc-300">{line.text}</span>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
}
