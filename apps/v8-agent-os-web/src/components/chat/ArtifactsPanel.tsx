"use client";

import React, { useEffect, useMemo, useState } from "react";
import { X, Code, FileAudio, FileText, FileVideo, ImageIcon, Link2, Maximize2, Minimize2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useDebouncedValue } from "@/hooks/use-debounce";
import { useChatStore } from "@/store/chat-store";
import { inferArtifactCardType, normalizeRuntimeArtifact, normalizeRuntimeArtifacts, resolveRuntimeArtifactUrl, RuntimeArtifact } from "@/lib/artifacts";
import { parseContentToBlocks } from "@/lib/chat/content-detector";

type LegacyArtifact = {
    id: string;
    title: string;
    type: "code" | "markdown" | "html";
    content: string;
    language?: string;
    messageId: string;
};

type ArtifactView = RuntimeArtifact | LegacyArtifact;

function isRuntimeArtifact(artifact: ArtifactView | null | undefined): artifact is RuntimeArtifact {
    return Boolean(artifact && "artifactId" in artifact);
}

function isLegacyArtifact(artifact: ArtifactView | null | undefined): artifact is LegacyArtifact {
    return Boolean(artifact && "content" in artifact && !("artifactId" in artifact));
}

function getLegacyArtifactsFromMessages(activeArtifactId: string | null, messages: ReturnType<typeof useChatStore.getState>["messages"]): {
    artifacts: LegacyArtifact[];
    activeArtifact?: LegacyArtifact;
} {
    if (!activeArtifactId) {
        return { artifacts: [], activeArtifact: undefined };
    }

    for (const msg of [...messages].reverse()) {
        if (!msg.content || !msg.content.includes(activeArtifactId)) {
            continue;
        }
        const blocks = parseContentToBlocks(msg.content, false, 0);
        const artifacts: LegacyArtifact[] = blocks
            .filter((block) => block.type === "artifact")
            .map((block) => ({
                id: block.id,
                title: block.data?.title || "Untitled",
                type: (block.data?.type as LegacyArtifact["type"]) || "code",
                content: block.content,
                language: block.data?.language,
                messageId: msg.id,
            }));
        const activeArtifact = artifacts.find((artifact) => artifact.id === activeArtifactId);
        if (activeArtifact) {
            return { artifacts, activeArtifact };
        }
    }

    return { artifacts: [], activeArtifact: undefined };
}

function getRuntimeArtifactTitle(artifact: RuntimeArtifact) {
    return artifact.displayLabel || artifact.title || artifact.id;
}

function getRuntimeArtifactSubtitle(artifact: RuntimeArtifact) {
    return artifact.displaySubtitle || artifact.canonicalPath || artifact.workspaceRelativePath || artifact.previewUrl || "暂无路径信息";
}

function renderRuntimeArtifactIcon(kind: string) {
    switch (kind) {
        case "image":
            return <ImageIcon className="h-5 w-5" />;
        case "video":
            return <FileVideo className="h-5 w-5" />;
        case "audio":
            return <FileAudio className="h-5 w-5" />;
        case "document":
            return <FileText className="h-5 w-5" />;
        default:
            return <Link2 className="h-5 w-5" />;
    }
}

export function ArtifactsPanel({ sessionId }: { sessionId?: string | null }) {
    const [isExpanded, setIsExpanded] = useState(false);
    const [runtimeArtifacts, setRuntimeArtifacts] = useState<RuntimeArtifact[]>([]);
    const [runtimeArtifactDetail, setRuntimeArtifactDetail] = useState<RuntimeArtifact | null>(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const { activeArtifactId, setActiveArtifactId, messages } = useChatStore();

    useEffect(() => {
        if (!sessionId) {
            setRuntimeArtifacts([]);
            setRuntimeArtifactDetail(null);
            return;
        }

        let cancelled = false;
        const loadArtifacts = async () => {
            try {
                const response = await fetch(`/api/artifacts?sessionId=${encodeURIComponent(sessionId)}&limit=200`, {
                    cache: "no-store",
                });
                if (!response.ok) {
                    return;
                }
                const data = await response.json().catch(() => ({}));
                const artifacts = normalizeRuntimeArtifacts(data?.artifacts);
                if (!cancelled) {
                    setRuntimeArtifacts(artifacts);
                }
            } catch (error) {
                console.warn("[ArtifactsPanel] Failed to load runtime artifacts:", error);
            }
        };

        void loadArtifacts();
        return () => {
            cancelled = true;
        };
    }, [sessionId]);

    const runtimeArtifactMap = useMemo(() => {
        return new Map(runtimeArtifacts.map((artifact) => [artifact.id, artifact]));
    }, [runtimeArtifacts]);

    const legacyState = useMemo(() => getLegacyArtifactsFromMessages(activeArtifactId, messages), [activeArtifactId, messages]);

    useEffect(() => {
        if (!activeArtifactId) {
            setRuntimeArtifactDetail(null);
            return;
        }

        const cached = runtimeArtifactMap.get(activeArtifactId);
        if (cached) {
            setRuntimeArtifactDetail(cached);
            return;
        }

        let cancelled = false;
        const loadDetail = async () => {
            setDetailLoading(true);
            try {
                const response = await fetch(`/api/artifacts/${encodeURIComponent(activeArtifactId)}`, { cache: "no-store" });
                if (!response.ok) {
                    return;
                }
                const data = await response.json().catch(() => ({}));
                const artifact = normalizeRuntimeArtifact(data);
                if (!cancelled) {
                    setRuntimeArtifactDetail(artifact);
                }
            } catch (error) {
                console.warn("[ArtifactsPanel] Failed to load artifact detail:", error);
            } finally {
                if (!cancelled) {
                    setDetailLoading(false);
                }
            }
        };

        void loadDetail();
        return () => {
            cancelled = true;
        };
    }, [activeArtifactId, runtimeArtifactMap]);

    const activeArtifact: ArtifactView | null = runtimeArtifactDetail || runtimeArtifactMap.get(activeArtifactId || "") || legacyState.activeArtifact || null;

    const visibleArtifacts: ArtifactView[] = useMemo(() => {
        if (runtimeArtifacts.length > 0) {
            if (isRuntimeArtifact(activeArtifact) && activeArtifact.runId) {
                const sameRun = runtimeArtifacts.filter((artifact) => artifact.runId === activeArtifact.runId);
                if (sameRun.length > 0) {
                    return sameRun;
                }
            }
            return runtimeArtifacts;
        }
        return legacyState.artifacts;
    }, [activeArtifact, legacyState.artifacts, runtimeArtifacts]);

    const debouncedHtmlContent = useDebouncedValue(
        isLegacyArtifact(activeArtifact) && activeArtifact.type === "html" ? activeArtifact.content : "",
        500,
    );
    const activeArtifactUrl = useMemo(
        () => (isRuntimeArtifact(activeArtifact) ? resolveRuntimeArtifactUrl(activeArtifact) : undefined),
        [activeArtifact],
    );

    const iframeKey = useMemo(() => {
        return `iframe-${activeArtifact?.id}-${debouncedHtmlContent.length}`;
    }, [activeArtifact?.id, debouncedHtmlContent.length]);

    if (!activeArtifact) {
        return null;
    }

    const runtimeArtifactType = isRuntimeArtifact(activeArtifact) ? inferArtifactCardType(activeArtifact) : activeArtifact.type;

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200">
            <div
                className={cn(
                    "bg-background border shadow-2xl rounded-xl flex flex-col transition-all duration-300 overflow-hidden ring-1 ring-black/10",
                    isExpanded ? "w-[95vw] h-[95vh]" : "w-[90vw] md:w-[880px] h-[82vh]",
                )}
            >
                <div className="flex items-center justify-between p-4 border-b bg-muted/30">
                    <div className="flex items-center gap-2 overflow-hidden">
                        {isRuntimeArtifact(activeArtifact)
                            ? renderRuntimeArtifactIcon(activeArtifact.kind)
                            : runtimeArtifactType === "code"
                                ? <Code className="h-5 w-5 text-blue-500" />
                                : runtimeArtifactType === "html"
                                    ? <Link2 className="h-5 w-5 text-orange-500" />
                                    : <FileText className="h-5 w-5 text-green-500" />}
                        <div className="min-w-0">
                            <h3 className="font-semibold truncate">
                                {isRuntimeArtifact(activeArtifact) ? getRuntimeArtifactTitle(activeArtifact) : activeArtifact.title}
                            </h3>
                            {isRuntimeArtifact(activeArtifact) ? (
                                <p className="text-xs text-muted-foreground truncate">
                                    {getRuntimeArtifactSubtitle(activeArtifact)}
                                </p>
                            ) : null}
                        </div>
                    </div>
                    <div className="flex items-center gap-1">
                        {detailLoading ? <span className="text-xs text-muted-foreground px-2">加载中...</span> : null}
                        <Button variant="ghost" size="icon" onClick={() => setIsExpanded(!isExpanded)}>
                            {isExpanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
                        </Button>
                        <Button variant="ghost" size="icon" onClick={() => setActiveArtifactId(null)}>
                            <X className="w-4 h-4" />
                        </Button>
                    </div>
                </div>

                {visibleArtifacts.length > 1 && (
                    <div className="flex gap-1 p-2 bg-muted/20 overflow-x-auto border-b">
                        {visibleArtifacts.map((artifact) => {
                            const artifactId = isRuntimeArtifact(artifact) ? artifact.id : artifact.id;
                            const title = isRuntimeArtifact(artifact) ? getRuntimeArtifactTitle(artifact) : artifact.title;
                            return (
                                <button
                                    key={artifactId}
                                    onClick={() => setActiveArtifactId(artifactId)}
                                    className={cn(
                                        "px-3 py-1.5 text-sm rounded-md whitespace-nowrap transition-colors",
                                        activeArtifactId === artifactId
                                            ? "bg-background shadow text-foreground font-medium"
                                            : "text-muted-foreground hover:bg-muted/50",
                                    )}
                                >
                                    {title}
                                </button>
                            );
                        })}
                    </div>
                )}

                <div className="flex-1 overflow-auto bg-muted/10">
                    {isRuntimeArtifact(activeArtifact) ? (
                        <div className="grid h-full gap-4 p-5 lg:grid-cols-[minmax(0,1.1fr)_340px]">
                            <div className="rounded-2xl border bg-card/70 p-4 overflow-auto">
                                {activeArtifactUrl && runtimeArtifactType === "image" ? (
                                    // eslint-disable-next-line @next/next/no-img-element
                                    <img src={activeArtifactUrl} alt={getRuntimeArtifactTitle(activeArtifact)} className="max-h-[60vh] w-full rounded-xl object-contain bg-black/5" />
                                ) : null}
                                {activeArtifactUrl && runtimeArtifactType === "video" ? (
                                    <video controls className="max-h-[60vh] w-full rounded-xl bg-black/5" src={activeArtifactUrl} />
                                ) : null}
                                {activeArtifactUrl && runtimeArtifactType === "audio" ? (
                                    <div className="rounded-xl border bg-background p-5">
                                        <audio controls className="w-full" src={activeArtifactUrl} />
                                    </div>
                                ) : null}
                                {!activeArtifactUrl || !["image", "video", "audio"].includes(runtimeArtifactType) ? (
                                    <div className="rounded-xl border bg-background p-5 space-y-4">
                                        <div>
                                            <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Artifact Type</div>
                                            <div className="mt-2 text-sm font-medium">{runtimeArtifactType}</div>
                                        </div>
                                        <div>
                                            <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Preview</div>
                                            {activeArtifactUrl ? (
                                                <a
                                                    href={activeArtifactUrl}
                                                    target="_blank"
                                                    rel="noreferrer"
                                                    className="mt-2 block break-all text-sm text-primary underline-offset-4 hover:underline"
                                                >
                                                    {activeArtifactUrl}
                                                </a>
                                            ) : (
                                                <div className="mt-2 text-sm text-muted-foreground">该产物没有可直接内联预览的链接。</div>
                                            )}
                                        </div>
                                        <div>
                                            <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Metadata</div>
                                            <pre className="mt-2 whitespace-pre-wrap break-all rounded-xl border bg-muted/20 p-4 text-xs leading-6">
                                                {JSON.stringify(activeArtifact.metadata || {}, null, 2)}
                                            </pre>
                                        </div>
                                    </div>
                                ) : null}
                            </div>

                            <div className="rounded-2xl border bg-card/70 p-4 overflow-auto">
                                <div className="space-y-4">
                                    <div>
                                        <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Artifact ID</div>
                                        <div className="mt-2 break-all font-mono text-sm">{activeArtifact.id}</div>
                                    </div>
                                    <div>
                                        <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Session / Run / Message</div>
                                        <div className="mt-2 space-y-2 text-sm">
                                            <div className="break-all font-mono">{activeArtifact.sessionId || "—"}</div>
                                            <div className="break-all font-mono">{activeArtifact.runId || "—"}</div>
                                            <div className="break-all font-mono">{activeArtifact.messageId || "—"}</div>
                                        </div>
                                    </div>
                                    <div>
                                        <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Paths</div>
                                        <div className="mt-2 space-y-2 text-sm">
                                            <div className="break-all">{activeArtifact.canonicalPath || activeArtifact.workspaceRelativePath || "—"}</div>
                                            <div className="break-all text-muted-foreground">{activeArtifact.pathPlane || activeArtifact.kind || "—"}</div>
                                        </div>
                                    </div>
                                    <div>
                                        <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Created At</div>
                                        <div className="mt-2 text-sm">{activeArtifact.createdAt || "—"}</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    ) : activeArtifact.type === "code" ? (
                        <div className="h-full overflow-auto">
                            <SyntaxHighlighter
                                language={activeArtifact.language || "javascript"}
                                style={vscDarkPlus}
                                customStyle={{ margin: 0, padding: "1.5rem", minHeight: "100%" }}
                                showLineNumbers
                            >
                                {activeArtifact.content}
                            </SyntaxHighlighter>
                        </div>
                    ) : activeArtifact.type === "html" ? (
                        <div className="h-full flex flex-col bg-white relative rounded-b-xl overflow-hidden">
                            <iframe
                                key={iframeKey}
                                srcDoc={debouncedHtmlContent}
                                className="w-full h-full border-0"
                                title="Preview"
                                sandbox="allow-scripts"
                            />
                        </div>
                    ) : (
                        <div className="prose dark:prose-invert max-w-none p-6 mx-auto">
                            <ReactMarkdown>{activeArtifact.content}</ReactMarkdown>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
