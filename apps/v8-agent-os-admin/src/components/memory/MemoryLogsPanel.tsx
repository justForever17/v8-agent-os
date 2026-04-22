"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FileText, Folder, FolderOpen, Loader2, RefreshCw, Save, Trash2 } from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { cn } from "@/lib/utils";

type MemoryLogTreeNode = {
    id: string;
    name: string;
    kind: "directory" | "file";
    relativePath: string;
    children?: MemoryLogTreeNode[];
};

type MemoryLogFilePayload = {
    relativePath: string;
    content: string;
    exists: boolean;
    updatedAt?: string | null;
};

function collectFirstFile(nodes: MemoryLogTreeNode[]): string {
    for (const node of nodes) {
        if (node.kind === "file") return node.relativePath;
        const nested = collectFirstFile(node.children || []);
        if (nested) return nested;
    }
    return "";
}

function normalizeExpanded(nodes: MemoryLogTreeNode[], expanded: Set<string>, selectedPath: string): Set<string> {
    const next = new Set(expanded);
    if (!selectedPath) return next;
    const segments = selectedPath.split("/").filter(Boolean);
    const pathSegments: string[] = [];
    for (const segment of segments.slice(0, -1)) {
        pathSegments.push(segment);
        next.add(pathSegments.join("/"));
    }
    nodes.forEach((node) => {
        if (node.kind === "directory" && node.children?.some((child) => child.kind === "file")) {
            next.add(node.relativePath);
        }
    });
    return next;
}

export default function MemoryLogsPanel() {
    const t = useT();
    const { toast } = useToast();
    const [tree, setTree] = useState<MemoryLogTreeNode[]>([]);
    const [selectedPath, setSelectedPath] = useState("");
    const [content, setContent] = useState("");
    const [updatedAt, setUpdatedAt] = useState<string | null>("");
    const [loadingTree, setLoadingTree] = useState(true);
    const [loadingFile, setLoadingFile] = useState(false);
    const [saving, setSaving] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    const [savedContent, setSavedContent] = useState("");

    const dirty = useMemo(() => selectedPath && content !== savedContent, [content, savedContent, selectedPath]);

    const loadFile = useCallback(async (relativePath: string) => {
        if (!relativePath) return;
        setLoadingFile(true);
        try {
            const response = await fetch(`/api/memory/logs/file?path=${encodeURIComponent(relativePath)}`, { cache: "no-store" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.error || `Failed to load log: ${response.status}`);
            }
            const file = payload as MemoryLogFilePayload;
            setSelectedPath(file.relativePath);
            setContent(file.content || "");
            setSavedContent(file.content || "");
            setUpdatedAt(file.updatedAt || "");
        } catch (error) {
            console.error("[MemoryLogsPanel] loadFile failed:", error);
            toast({
                title: t("components.memory.MemoryLogsPanel.loadFailedTitle"),
                description: t("components.memory.MemoryLogsPanel.loadFailedDescription"),
                variant: "destructive",
            });
        } finally {
            setLoadingFile(false);
        }
    }, [t, toast]);

    const loadTree = useCallback(async (preferredPath?: string) => {
        setLoadingTree(true);
        try {
            const response = await fetch("/api/memory/logs/tree", { cache: "no-store" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.error || `Failed to load tree: ${response.status}`);
            }
            const nodes = Array.isArray(payload?.tree) ? payload.tree as MemoryLogTreeNode[] : [];
            setTree(nodes);
            const nextPath = preferredPath || selectedPath;
            const allPaths = new Set<string>();
            const collect = (items: MemoryLogTreeNode[]) => {
                items.forEach((item) => {
                    if (item.kind === "file") allPaths.add(item.relativePath);
                    if (item.children?.length) collect(item.children);
                });
            };
            collect(nodes);
            const resolvedPath = nextPath && allPaths.has(nextPath) ? nextPath : collectFirstFile(nodes);
            setExpanded((prev) => normalizeExpanded(nodes, prev, resolvedPath));
            if (resolvedPath) {
                await loadFile(resolvedPath);
            } else {
                setSelectedPath("");
                setContent("");
                setSavedContent("");
                setUpdatedAt("");
            }
        } catch (error) {
            console.error("[MemoryLogsPanel] loadTree failed:", error);
            toast({
                title: t("components.memory.MemoryLogsPanel.treeFailedTitle"),
                description: t("components.memory.MemoryLogsPanel.treeFailedDescription"),
                variant: "destructive",
            });
        } finally {
            setLoadingTree(false);
        }
    }, [loadFile, selectedPath, t, toast]);

    useEffect(() => {
        void loadTree();
    }, [loadTree]);

    const toggleExpanded = useCallback((relativePath: string) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(relativePath)) next.delete(relativePath);
            else next.add(relativePath);
            return next;
        });
    }, []);

    const handleSave = useCallback(async () => {
        if (!selectedPath) return;
        setSaving(true);
        try {
            const response = await fetch("/api/memory/logs/file", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ relativePath: selectedPath, content }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.error || `Failed to save log: ${response.status}`);
            }
            const file = payload as MemoryLogFilePayload;
            setContent(file.content || "");
            setSavedContent(file.content || "");
            setUpdatedAt(file.updatedAt || "");
            toast({
                title: t("components.memory.MemoryLogsPanel.saveSuccessTitle"),
                description: t("components.memory.MemoryLogsPanel.saveSuccessDescription"),
            });
            await loadTree(file.relativePath);
        } catch (error) {
            console.error("[MemoryLogsPanel] save failed:", error);
            toast({
                title: t("components.memory.MemoryLogsPanel.saveFailedTitle"),
                description: t("components.memory.MemoryLogsPanel.saveFailedDescription"),
                variant: "destructive",
            });
        } finally {
            setSaving(false);
        }
    }, [content, loadTree, selectedPath, t, toast]);

    const handleDelete = useCallback(async () => {
        if (!selectedPath) return;
        if (!window.confirm(t("components.memory.MemoryLogsPanel.deleteConfirm", { path: selectedPath }))) return;
        setDeleting(true);
        try {
            const response = await fetch(`/api/memory/logs/file?path=${encodeURIComponent(selectedPath)}`, { method: "DELETE" });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload?.error || `Failed to delete log: ${response.status}`);
            }
            toast({
                title: t("components.memory.MemoryLogsPanel.deleteSuccessTitle"),
                description: t("components.memory.MemoryLogsPanel.deleteSuccessDescription"),
            });
            setSelectedPath("");
            setContent("");
            setSavedContent("");
            setUpdatedAt("");
            await loadTree();
        } catch (error) {
            console.error("[MemoryLogsPanel] delete failed:", error);
            toast({
                title: t("components.memory.MemoryLogsPanel.deleteFailedTitle"),
                description: t("components.memory.MemoryLogsPanel.deleteFailedDescription"),
                variant: "destructive",
            });
        } finally {
            setDeleting(false);
        }
    }, [loadTree, selectedPath, t, toast]);

    return (
        <Card className="overflow-hidden border-border/60">
            <CardHeader className="border-b border-border/50 pb-4">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div className="space-y-1">
                        <CardTitle className="text-xl">{t("components.memory.MemoryLogsPanel.title")}</CardTitle>
                        <CardDescription>{t("components.memory.MemoryLogsPanel.description")}</CardDescription>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button variant="outline" size="sm" onClick={() => void loadTree(selectedPath)} disabled={loadingTree || loadingFile}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            {t("components.memory.MemoryLogsPanel.refresh")}
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => void handleDelete()} disabled={!selectedPath || deleting || loadingTree}>
                            {deleting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                            {t("components.memory.MemoryLogsPanel.delete")}
                        </Button>
                        <Button size="sm" onClick={() => void handleSave()} disabled={!selectedPath || saving || !dirty}>
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            {t("components.memory.MemoryLogsPanel.save")}
                        </Button>
                    </div>
                </div>
            </CardHeader>
            <CardContent className="p-0">
                <div className="grid min-h-[720px] grid-cols-1 lg:grid-cols-[320px_minmax(0,1fr)]">
                    <div className="border-b border-r border-border/50 bg-muted/10 lg:border-b-0">
                        <div className="h-[720px] overflow-auto p-4">
                            {loadingTree ? (
                                <div className="flex h-full items-center justify-center text-muted-foreground">
                                    <Loader2 className="h-5 w-5 animate-spin" />
                                </div>
                            ) : tree.length === 0 ? (
                                <div className="rounded-xl border border-dashed border-border/80 bg-background/70 p-4 text-sm text-muted-foreground">
                                    {t("components.memory.MemoryLogsPanel.emptyTree")}
                                </div>
                            ) : (
                                <div className="min-w-max space-y-1">
                                    {tree.map((node) => (
                                        <TreeNode
                                            key={node.id}
                                            node={node}
                                            depth={0}
                                            expanded={expanded}
                                            selectedPath={selectedPath}
                                            onToggle={toggleExpanded}
                                            onSelect={(path) => void loadFile(path)}
                                        />
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                    <div className="min-w-0 bg-background">
                        <div className="flex h-[720px] flex-col">
                            <div className="border-b border-border/50 px-5 py-4">
                                <div className="text-sm font-medium text-foreground">
                                    {selectedPath || t("components.memory.MemoryLogsPanel.unselected")}
                                </div>
                                <div className="mt-1 text-xs text-muted-foreground">
                                    {updatedAt
                                        ? t("components.memory.MemoryLogsPanel.updatedAt", { updatedAt })
                                        : t("components.memory.MemoryLogsPanel.updatedUnknown")}
                                </div>
                            </div>
                            <div className="flex-1 overflow-hidden px-5 py-4">
                                {loadingFile ? (
                                    <div className="flex h-full items-center justify-center text-muted-foreground">
                                        <Loader2 className="h-5 w-5 animate-spin" />
                                    </div>
                                ) : selectedPath ? (
                                    <div className="mx-auto h-full max-w-5xl overflow-auto">
                                        <Textarea
                                            value={content}
                                            onChange={(event) => setContent(event.target.value)}
                                            className="h-full min-h-[580px] resize-none overflow-auto font-mono text-xs leading-6"
                                        />
                                    </div>
                                ) : (
                                    <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-border/80 bg-muted/10 text-sm text-muted-foreground">
                                        {t("components.memory.MemoryLogsPanel.selectPrompt")}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}

function TreeNode({
    node,
    depth,
    expanded,
    selectedPath,
    onToggle,
    onSelect,
}: {
    node: MemoryLogTreeNode;
    depth: number;
    expanded: Set<string>;
    selectedPath: string;
    onToggle: (relativePath: string) => void;
    onSelect: (relativePath: string) => void;
}) {
    const isDirectory = node.kind === "directory";
    const isExpanded = isDirectory && expanded.has(node.relativePath);
    const isSelected = !isDirectory && selectedPath === node.relativePath;
    return (
        <div className="space-y-1">
            <button
                type="button"
                onClick={() => {
                    if (isDirectory) onToggle(node.relativePath);
                    else onSelect(node.relativePath);
                }}
                className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                    isSelected ? "bg-primary/10 text-primary" : "hover:bg-muted/60",
                )}
                style={{ paddingLeft: `${depth * 16 + 8}px` }}
            >
                {isDirectory ? (
                    <>
                        {isExpanded ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
                        {isExpanded ? <FolderOpen className="h-4 w-4 shrink-0" /> : <Folder className="h-4 w-4 shrink-0" />}
                    </>
                ) : (
                    <>
                        <span className="w-4 shrink-0" />
                        <FileText className="h-4 w-4 shrink-0" />
                    </>
                )}
                <span className="whitespace-nowrap">{node.name}</span>
            </button>
            {isDirectory && isExpanded && node.children?.length ? (
                <div className="space-y-1">
                    {node.children.map((child) => (
                        <TreeNode
                            key={child.id}
                            node={child}
                            depth={depth + 1}
                            expanded={expanded}
                            selectedPath={selectedPath}
                            onToggle={onToggle}
                            onSelect={onSelect}
                        />
                    ))}
                </div>
            ) : null}
        </div>
    );
}
