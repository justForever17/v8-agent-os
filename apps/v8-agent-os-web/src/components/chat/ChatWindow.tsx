"use client";

import { ArrowDown, Bot } from "lucide-react";
import { Button } from "@/components/ui/button";
// import { LoadingBubble } from "./LoadingBubble";
import { useState, useEffect, useRef, useCallback } from "react";
import { Message } from "@/store/chat-types";
import { ChatMessage } from "./ChatMessage";
import { ContextReferencesHUD } from "./ContextReferencesHUD";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
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

interface ChatWindowProps {
    messages: Message[];
    processes: AdminProcessRef[];
    contextReferences: ContextReferenceItem[];
    conversationId?: string | null;
    onDeleteMessage?: (id: string) => void;
    isLoading?: boolean;
    userAvatar?: string | null;
    userName?: string | null;
    shellClassName?: string;
    runtimeActivities?: RuntimeStageActivity[];
}

export function ChatWindow({ messages, processes, contextReferences, conversationId, onDeleteMessage, isLoading, userAvatar, userName, shellClassName, runtimeActivities = [] }: ChatWindowProps) {
    const t = useT();
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const contentRef = useRef<HTMLDivElement>(null);
    const scrollCommitRef = useRef<number | null>(null);
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

    // Delete state
    const [deleteId, setDeleteId] = useState<string | null>(null);

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
    }, []);

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
            <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden">
                <div className="flex min-h-0 w-full flex-1 flex-col overflow-hidden pt-1 sm:pt-1.5">
                    <div className="shrink-0">
                        <ContextReferencesHUD contextReferences={contextReferences} />
                    </div>
                    <div
                        className={cn(
                            "custom-scrollbar relative min-h-0 w-full flex-1 overflow-y-auto overscroll-contain bg-transparent px-3 pb-6 sm:px-4 sm:pb-8 lg:px-5 lg:pb-10 md:rounded-[30px] md:border md:border-border/50 md:bg-slate-50/65 md:shadow-sm md:backdrop-blur-sm md:dark:bg-zinc-900/50",
                            shellClassName ?? "max-w-4xl"
                        )}
                        ref={scrollContainerRef}
                        onScroll={handleScroll}
                    >
                        <div ref={contentRef} className="flex min-h-full flex-col">
                            {messages.length === 0 ? (
                                <div className="flex flex-1 flex-col items-center justify-center text-muted-foreground opacity-60">
                                    <Bot className="mb-4 h-12 w-12 animate-pulse opacity-50" />
                                    <p className="text-sm">{t(lt("没有消息历史", "No messages yet"))}</p>
                                    <p className="mt-2 text-xs">{t(lt("打个招呼吧", "Start the conversation"))}</p>
                                </div>
                            ) : (
                                messages.map((m, index) => (
                                    <ChatMessage
                                        key={m.id}
                                        message={m}
                                        processes={processes}
                                        isLoading={(isLoading || false) && index === messages.length - 1}
                                        onDelete={setDeleteId}
                                        isLast={index === messages.length - 1}
                                        userAvatar={userAvatar}
                                        userName={userName}
                                        runtimeActivities={runtimeActivities}
                                    />
                                ))
                            )}
                            <div ref={messagesEndRef} />
                        </div>
                        <div className="pointer-events-none absolute inset-x-0 bottom-0 hidden h-12 bg-gradient-to-t from-background via-background/88 to-transparent md:block md:from-slate-50/90 md:via-slate-50/70 dark:md:from-zinc-900/90 dark:md:via-zinc-900/72" />
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
                        <DialogTitle>{t(lt("删除消息", "Delete message"))}</DialogTitle>
                        <DialogDescription>
                            {t(lt("确定要删除这条消息吗？此操作无法撤销。", "Delete this message? This action cannot be undone."))}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDeleteId(null)}>{t(lt("取消", "Cancel"))}</Button>
                        <Button variant="destructive" onClick={confirmDelete}>{t(lt("删除", "Delete"))}</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
