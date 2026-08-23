"use client";

import { ArrowDown, Bot } from "lucide-react";
import { Button } from "@/components/ui/button";
// import { LoadingBubble } from "./LoadingBubble";
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { Message } from "@/store/chat-types";
import { ChatMessage } from "./ChatMessage";
import { ChatTurnIndexEntry, TurnNavigator } from "./TurnNavigator";
import { ContextReferencesHUD } from "./ContextReferencesHUD";
import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";
import type { AdminProcessRef, ContextReferenceItem } from "@v8/session-realtime";
import type { RuntimeStageActivity } from "@/lib/runtime-stage";

import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";

const EMPTY_RUNTIME_ACTIVITIES: RuntimeStageActivity[] = [];

interface ChatWindowProps {
    messages: Message[];
    processes: AdminProcessRef[];
    contextReferences: ContextReferenceItem[];
    conversationId?: string | null;
    onDeleteMessage?: (id: string) => void;
    isLoading?: boolean;
    userAvatar?: string | null;
    userName?: string | null;
    supervisorProfile?: { name: string; roleLabel: string; avatar: string } | null;
    shellClassName?: string;
    runtimeActivities?: RuntimeStageActivity[];
    sessionRunning?: boolean;
    hasOlderTurns?: boolean;
    isLoadingOlderTurns?: boolean;
    onReachTop?: () => void;
    turnIndex?: ChatTurnIndexEntry[];
    totalTurnCount?: number;
    focusedTurnId?: string | null;
    onSelectTurnPosition?: (position: number) => void;
    onActiveTurnChange?: (turnId: string) => void;
}

export function ChatWindow({
    messages,
    processes,
    contextReferences,
    conversationId,
    onDeleteMessage,
    isLoading,
    userAvatar,
    userName,
    supervisorProfile,
    shellClassName,
    runtimeActivities = EMPTY_RUNTIME_ACTIVITIES,
    sessionRunning,
    hasOlderTurns = false,
    isLoadingOlderTurns = false,
    onReachTop,
    turnIndex = [],
    totalTurnCount = 0,
    focusedTurnId,
    onSelectTurnPosition,
    onActiveTurnChange,
}: ChatWindowProps) {
    const t = useT();
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const contentRef = useRef<HTMLDivElement>(null);
    const scrollCommitRef = useRef<number | null>(null);
    const olderLoadAnchorRef = useRef<{ scrollHeight: number; scrollTop: number } | null>(null);
    const lastLoadTriggerAtRef = useRef(0);
    const visibleTurnIdRef = useRef("");
    const scrollStateRef = useRef<{
        messageCount: number;
        lastMessageId: string;
        isLoading: boolean;
    }>({
        messageCount: 0,
        lastMessageId: "",
        isLoading: false,
    });

    // Scroll state
    const [isAtBottom, setIsAtBottom] = useState(true);
    const [showScrollButton, setShowScrollButton] = useState(false);
    const [activeVisibleTurnId, setActiveVisibleTurnId] = useState("");

    // Delete state
    const [deleteId, setDeleteId] = useState<string | null>(null);
    const liveRuntimeMessageIndex = useMemo(() => {
        for (let index = messages.length - 1; index >= 0; index -= 1) {
            if (messages[index]?.role === "assistant") return index;
        }
        return -1;
    }, [messages]);

    // Smart Auto-scroll
    const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
        const container = scrollContainerRef.current;
        if (!container) return;
        container.scrollTo({
            top: container.scrollHeight,
            behavior,
        });
        setIsAtBottom(true);
        setShowScrollButton(false);
    }, []);

    const lastMessageId = messages[messages.length - 1]?.id || "";

    useEffect(() => {
        const previous = scrollStateRef.current;
        const nextState = {
            messageCount: messages.length,
            lastMessageId,
            isLoading: Boolean(isLoading),
        };
        scrollStateRef.current = nextState;

        if (!isAtBottom) {
            return;
        }

        const hasNewMessage = previous.messageCount !== nextState.messageCount || previous.lastMessageId !== nextState.lastMessageId;
        const loadingFinished = previous.isLoading && !nextState.isLoading;

        if (!hasNewMessage && !loadingFinished) {
            return;
        }

        if (scrollCommitRef.current !== null && typeof window !== "undefined") {
            window.cancelAnimationFrame(scrollCommitRef.current);
        }

        const commit = () => {
            scrollCommitRef.current = null;
            scrollToBottom(loadingFinished ? "smooth" : "auto");
        };

        if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
            scrollCommitRef.current = window.requestAnimationFrame(commit);
            return () => {
                if (scrollCommitRef.current !== null) {
                    window.cancelAnimationFrame(scrollCommitRef.current);
                    scrollCommitRef.current = null;
                }
            };
        }

        commit();
    }, [isAtBottom, isLoading, lastMessageId, messages.length, scrollToBottom]);

    useEffect(() => {
        if (!focusedTurnId || typeof window === "undefined") {
            return;
        }
        const container = scrollContainerRef.current;
        if (!container) return;
        const target = Array.from(container.querySelectorAll<HTMLElement>("[data-turn-id]"))
            .find((element) => element.dataset.turnId === focusedTurnId);
        if (!target) return;
        visibleTurnIdRef.current = focusedTurnId;
        setActiveVisibleTurnId(focusedTurnId);
        window.requestAnimationFrame(() => target.scrollIntoView({ block: "center", behavior: "smooth" }));
    }, [focusedTurnId]);

    useEffect(() => {
        if (isLoadingOlderTurns) {
            return;
        }
        const anchor = olderLoadAnchorRef.current;
        const container = scrollContainerRef.current;
        if (!anchor || !container) {
            return;
        }
        olderLoadAnchorRef.current = null;
        const delta = container.scrollHeight - anchor.scrollHeight;
        if (delta > 0) {
            container.scrollTop = anchor.scrollTop + delta;
        }
    }, [isLoadingOlderTurns, messages.length]);

    useEffect(() => {
        if (!isLoading || !isAtBottom || typeof window === "undefined" || typeof ResizeObserver === "undefined") {
            return;
        }

        const content = contentRef.current;
        if (!content) {
            return;
        }

        let frameId: number | null = null;
        const scheduleStickToBottom = () => {
            if (frameId !== null) {
                window.cancelAnimationFrame(frameId);
            }
            frameId = window.requestAnimationFrame(() => {
                frameId = null;
                scrollToBottom("auto");
            });
        };

        const observer = new ResizeObserver(() => {
            scheduleStickToBottom();
        });
        observer.observe(content);

        return () => {
            observer.disconnect();
            if (frameId !== null) {
                window.cancelAnimationFrame(frameId);
            }
        };
    }, [isAtBottom, isLoading, scrollToBottom]);

    const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
        const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
        const distanceToBottom = scrollHeight - scrollTop - clientHeight;
        const isBottom = distanceToBottom < 96;

        setIsAtBottom(isBottom);
        setShowScrollButton(!isBottom);

        if (turnIndex.length > 0) {
            const containerRect = e.currentTarget.getBoundingClientRect();
            const turnElements = Array.from(e.currentTarget.querySelectorAll<HTMLElement>("[data-turn-id]"));
            const visible = turnElements.find((element) => element.getBoundingClientRect().bottom >= containerRect.top + 48);
            const nextTurnId = String(visible?.dataset.turnId || "").trim();
            if (nextTurnId && nextTurnId !== visibleTurnIdRef.current) {
                visibleTurnIdRef.current = nextTurnId;
                setActiveVisibleTurnId(nextTurnId);
                onActiveTurnChange?.(nextTurnId);
            }
        }

        if (scrollTop < 96 && hasOlderTurns && !isLoadingOlderTurns && onReachTop) {
            const now = Date.now();
            if (now - lastLoadTriggerAtRef.current < 500) {
                return;
            }
            lastLoadTriggerAtRef.current = now;
            olderLoadAnchorRef.current = { scrollHeight, scrollTop };
            onReachTop();
        }
    }, [hasOlderTurns, isLoadingOlderTurns, onActiveTurnChange, onReachTop, turnIndex.length]);

    const confirmDelete = async () => {
        if (!deleteId) return;

        try {
            const suffix = conversationId ? `?session_id=${encodeURIComponent(conversationId)}` : "";
            const res = await fetch(`/api/messages/${encodeURIComponent(deleteId)}${suffix}`, { method: 'DELETE' });
            if (res.ok) {
                onDeleteMessage?.(deleteId);
                // No reload needed if onDeleteMessage handles state update
                if (!onDeleteMessage) {
                    // Fallback if no callback provided, but ideally we avoid this
                    window.location.reload();
                }
            }
        } catch (error) {
            console.error("Failed to delete message", error);
        } finally {
            setDeleteId(null);
        }
    };

    return (
        <div className="relative flex h-full min-h-0">
            {onSelectTurnPosition ? (
                <TurnNavigator
                    turns={turnIndex}
                    totalTurnCount={totalTurnCount}
                    activeTurnId={activeVisibleTurnId || focusedTurnId}
                    onSelectPosition={onSelectTurnPosition}
                />
            ) : null}
            <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden">
                <div className="flex min-h-0 w-full flex-1 flex-col overflow-hidden pt-1 sm:pt-1.5">
                    <div className="shrink-0">
                        <ContextReferencesHUD contextReferences={contextReferences} />
                    </div>
                    <div
                        className={cn(
                            "v8-chat-viewport-surface custom-scrollbar relative min-h-0 w-full flex-1 overflow-y-auto overscroll-contain bg-transparent px-3 pb-6 sm:px-4 sm:pb-8 lg:px-5 lg:pb-10 md:rounded-[30px] md:border md:border-border/50 md:bg-slate-50/65 md:shadow-sm md:backdrop-blur-sm md:dark:bg-zinc-900/50",
                            shellClassName ?? "max-w-4xl"
                        )}
                        ref={scrollContainerRef}
                        onScroll={handleScroll}
                    >
                        <div ref={contentRef} className="flex min-h-full flex-col">
                            {isLoadingOlderTurns && messages.length > 0 ? (
                                <div className="flex justify-center py-2 text-[11px] text-muted-foreground">
                                    {t("web.generated.49b930d439")}
                                </div>
                            ) : null}
                            {messages.length === 0 ? (
                                <div className="flex flex-1 flex-col items-center justify-center text-muted-foreground opacity-60">
                                    <Bot className="mb-4 h-12 w-12 animate-pulse opacity-50" />
                                    <p className="text-sm">{t("web.generated.d8eed84190")}</p>
                                    <p className="mt-2 text-xs">{t("web.generated.6e73c6c2f4")}</p>
                                </div>
                            ) : (
                                messages.map((m, index) => (
                                    <div
                                        key={m.renderKey || (m.role === "assistant" && m.runId ? `assistant:${m.runId}` : m.id)}
                                        data-turn-id={m.turnId || undefined}
                                    >
                                        <ChatMessage
                                            message={m}
                                            processes={processes}
                                            isLoading={(isLoading || false) && index === messages.length - 1}
                                            onDelete={setDeleteId}
                                            isLast={index === messages.length - 1}
                                            userAvatar={userAvatar}
                                            userName={userName}
                                            supervisorProfile={supervisorProfile}
                                            runtimeActivities={index === liveRuntimeMessageIndex ? runtimeActivities : EMPTY_RUNTIME_ACTIVITIES}
                                            executionActive={index === liveRuntimeMessageIndex && (sessionRunning ?? Boolean(isLoading))}
                                            animateEntrance={Boolean(isLoading && index >= messages.length - 2)}
                                        />
                                    </div>
                                ))
                            )}
                            <div ref={messagesEndRef} />
                        </div>
                    </div>
                </div>

                {/* Scroll to bottom button */}
                {showScrollButton && (
                    <Button
                        variant="secondary"
                        size="icon"
                        className="animate-in fade-in zoom-in absolute bottom-3.5 right-3.5 z-10 rounded-full shadow-lg duration-200"
                        onClick={() => scrollToBottom('smooth')}
                    >
                        <ArrowDown className="w-4 h-4" />
                    </Button>
                )}
            </div>

            {/* Delete Confirmation Dialog */}
            <Dialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{t("web.generated.2f98d36496")}</DialogTitle>
                        <DialogDescription>
                            {t("web.generated.463cc0d6cb")}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDeleteId(null)}>{t("web.generated.d94a8eaf28")}</Button>
                        <Button variant="destructive" onClick={confirmDelete}>{t("web.generated.6cba6a2c08")}</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
