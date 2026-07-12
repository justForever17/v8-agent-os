"use client";

import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
    Copy,
    MessagesSquare,
    Plus,
    Trash2,
    PanelLeftClose,
    PanelLeftOpen,
    MessageCircle,
    ChevronDown,
    ChevronRight,
    Loader2,
    AlertCircle,
    Pencil,
    Pin,
    PinOff,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { cn } from "@/lib/utils";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";

import { useConversationContext } from "@/context/ConversationContext";
import { useRouter, useSearchParams } from "next/navigation";
import { useT } from "@/components/providers/LocaleProvider";
import { getConversationActivityState, groupConversationsByWorkspace, type ConversationWorkspaceGroup } from "@/lib/conversation-groups";

export function Sidebar() {
    const {
        conversations,
        createConversation,
        deleteConversation,
        patchConversationSummary,
        refreshConversations,
        updateConversationPresentation,
    } = useConversationContext();
    const router = useRouter();
    const searchParams = useSearchParams();
    const currentId = searchParams.get("id");
    const t = useT();

    const [isCollapsed, setIsCollapsed] = useState(false);
    const [isMobileOpen, setIsMobileOpen] = useState(false);
    const [deleteId, setDeleteId] = useState<string | null>(null);
    const [creatingGroupKey, setCreatingGroupKey] = useState<string | null>(null);
    const [createError, setCreateError] = useState("");
    const [contextMenu, setContextMenu] = useState<{ sessionId: string; x: number; y: number } | null>(null);
    const [groupContextMenu, setGroupContextMenu] = useState<{ groupKey: string; x: number; y: number } | null>(null);
    const [copiedSessionId, setCopiedSessionId] = useState<string | null>(null);
    const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
    const [sessionTitleDraft, setSessionTitleDraft] = useState("");
    const [editingGroupKey, setEditingGroupKey] = useState<string | null>(null);
    const [groupNameDraft, setGroupNameDraft] = useState("");
    const [presentationBusyKey, setPresentationBusyKey] = useState<string | null>(null);
    const sessionTitleInputRef = useRef<HTMLInputElement | null>(null);
    const groupNameInputRef = useRef<HTMLInputElement | null>(null);

    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

    useEffect(() => {
        if (!contextMenu && !groupContextMenu) return;
        const close = () => {
            setContextMenu(null);
            setGroupContextMenu(null);
        };
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                close();
            }
        };
        window.addEventListener("click", close);
        window.addEventListener("scroll", close, true);
        window.addEventListener("keydown", handleKeyDown);
        return () => {
            window.removeEventListener("click", close);
            window.removeEventListener("scroll", close, true);
            window.removeEventListener("keydown", handleKeyDown);
        };
    }, [contextMenu, groupContextMenu]);

    useEffect(() => {
        if (editingSessionId) {
            sessionTitleInputRef.current?.focus();
            sessionTitleInputRef.current?.select();
        }
    }, [editingSessionId]);

    useEffect(() => {
        if (editingGroupKey) {
            groupNameInputRef.current?.focus();
            groupNameInputRef.current?.select();
        }
    }, [editingGroupKey]);

    useEffect(() => {
        if (!copiedSessionId) return;
        const timer = window.setTimeout(() => setCopiedSessionId(null), 1600);
        return () => window.clearTimeout(timer);
    }, [copiedSessionId]);

    const toggleGroup = (key: string) => {
        setOpenGroups((prev) => ({ ...prev, [key]: !prev[key] }));
    };

    const groupedConvs = useMemo(() => groupConversationsByWorkspace(conversations, {
        mainWorkspace: t("web.generated.291993dda9"),
        externalWorkspace: t("web.generated.7273393e9c"),
        unbound: t("web.generated.cee27fba31"),
        workspace: t("web.generated.7f1bc76123"),
    }), [conversations, t]);

    const handleNewChat = () => {
        setIsMobileOpen(false);
        router.push("/chat?new=1");
    };

    const handleNavigation = (id: string) => {
        setContextMenu(null);
        setIsMobileOpen(false);
        router.push(`/chat?id=${id}`);
    };

    const openConversationMenu = (event: MouseEvent<HTMLDivElement>, sessionId: string) => {
        event.preventDefault();
        event.stopPropagation();
        const width = 184;
        const height = 220;
        const x = Math.min(event.clientX, Math.max(8, window.innerWidth - width - 8));
        const y = Math.min(event.clientY, Math.max(8, window.innerHeight - height - 8));
        setGroupContextMenu(null);
        setContextMenu({ sessionId, x, y });
    };

    const openGroupMenu = (event: MouseEvent<HTMLDivElement>, group: ConversationWorkspaceGroup) => {
        if (!group.workspacePath) return;
        event.preventDefault();
        event.stopPropagation();
        const width = 192;
        const height = 104;
        const x = Math.min(event.clientX, Math.max(8, window.innerWidth - width - 8));
        const y = Math.min(event.clientY, Math.max(8, window.innerHeight - height - 8));
        setContextMenu(null);
        setGroupContextMenu({ groupKey: group.key, x, y });
    };

    const copySessionId = async (sessionId: string) => {
        if (!sessionId) return;
        await navigator.clipboard?.writeText(sessionId);
        setCopiedSessionId(sessionId);
        window.setTimeout(() => {
            setContextMenu((current) => (current?.sessionId === sessionId ? null : current));
        }, 700);
    };

    const continueInNewConversation = (sessionId: string) => {
        setContextMenu(null);
        setIsMobileOpen(false);
        router.push(`/chat?new=1&contextSessionId=${encodeURIComponent(sessionId)}`);
    };

    const beginSessionRename = (sessionId: string) => {
        const conversation = conversations.find((item) => (item.sessionId || item.id) === sessionId);
        if (!conversation) return;
        setContextMenu(null);
        setSessionTitleDraft(conversation.title || "");
        setEditingSessionId(sessionId);
    };

    const saveSessionRename = async (sessionId: string, requestedTitle: string) => {
        const title = requestedTitle.trim();
        const current = conversations.find((item) => (item.sessionId || item.id) === sessionId);
        setEditingSessionId(null);
        if (!title || !current || title === current.title || presentationBusyKey) return;
        const previousTitle = current.title;
        patchConversationSummary(sessionId, { title });
        setPresentationBusyKey(`session:${sessionId}`);
        const updated = await updateConversationPresentation(sessionId, { title });
        setPresentationBusyKey(null);
        if (!updated) {
            patchConversationSummary(sessionId, { title: previousTitle });
            setCreateError(t("web.sidebar.taskUpdateFailed"));
        }
    };

    const toggleSessionPin = async (sessionId: string) => {
        const current = conversations.find((item) => (item.sessionId || item.id) === sessionId);
        if (!current || presentationBusyKey) return;
        setContextMenu(null);
        setPresentationBusyKey(`session:${sessionId}`);
        const updated = await updateConversationPresentation(sessionId, { pinned: !current.pinned });
        setPresentationBusyKey(null);
        if (!updated) {
            setCreateError(t("web.sidebar.taskUpdateFailed"));
        }
    };

    const beginGroupRename = (group: ConversationWorkspaceGroup) => {
        if (!group.workspacePath) return;
        setGroupContextMenu(null);
        setGroupNameDraft(group.label);
        setEditingGroupKey(group.key);
    };

    const updateGroupPresentation = async (group: ConversationWorkspaceGroup, patch: { displayName?: string; pinned?: boolean }) => {
        if (!group.workspacePath || presentationBusyKey) return false;
        setPresentationBusyKey(`group:${group.key}`);
        try {
            const response = await fetch("/api/workspace-presentations", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ workspacePath: group.workspacePath, ...patch }),
            });
            if (!response.ok) {
                throw new Error(`workspace presentation update failed: ${response.status}`);
            }
            await refreshConversations();
            return true;
        } catch (error) {
            console.error("Failed to update workspace presentation", error);
            setCreateError(t("web.sidebar.projectUpdateFailed"));
            return false;
        } finally {
            setPresentationBusyKey(null);
        }
    };

    const saveGroupRename = async (group: ConversationWorkspaceGroup, requestedName: string) => {
        const displayName = requestedName.trim();
        if (!displayName || displayName === group.label) {
            setEditingGroupKey(null);
            return;
        }
        if (await updateGroupPresentation(group, { displayName })) {
            setEditingGroupKey(null);
        }
    };

    const toggleGroupPin = async (event: MouseEvent<HTMLButtonElement> | null, group: ConversationWorkspaceGroup) => {
        event?.preventDefault();
        event?.stopPropagation();
        setGroupContextMenu(null);
        await updateGroupPresentation(group, { pinned: !group.pinned });
    };

    const confirmDelete = async () => {
        if (!deleteId) return;
        await deleteConversation(deleteId);
        if (currentId === deleteId) {
            router.push("/chat");
        }
        setDeleteId(null);
    };

    const createConversationInGroup = async (event: MouseEvent<HTMLButtonElement>, group: ConversationWorkspaceGroup) => {
        event.stopPropagation();
        if (!group.creationBinding || creatingGroupKey) return;
        setCreatingGroupKey(group.key);
        setCreateError("");
        const created = await createConversation(group.creationBinding);
        setCreatingGroupKey(null);
        if (!created) {
            setCreateError(t("web.sidebar.createConversationFailed"));
            return;
        }
        const sessionId = created.sessionId || created.id;
        setIsMobileOpen(false);
        router.push(`/chat?id=${encodeURIComponent(sessionId)}`);
    };

    const renderGroup = (group: ConversationWorkspaceGroup, collapsed: boolean, index: number) => {
        const items = group.items;
        const hasActiveConversation = items.some((item) => (item.sessionId || item.id) === currentId);
        const isOpen = openGroups[group.key] ?? (index === 0 || hasActiveConversation);

        return (
            <div key={group.key} className="mb-2">
                {!collapsed && (
                    <div
                        className="group/header mb-1 flex cursor-pointer items-center rounded-lg px-2.5 py-1.5 transition-colors duration-150 hover:bg-muted/35 focus-within:bg-muted/35"
                        onClick={() => toggleGroup(group.key)}
                        onContextMenu={(event) => openGroupMenu(event, group)}
                    >
                        {isOpen ? (
                            <ChevronDown className="mr-1.5 h-3.5 w-3.5 text-muted-foreground" />
                        ) : (
                            <ChevronRight className="mr-1.5 h-3.5 w-3.5 text-muted-foreground" />
                        )}
                        {editingGroupKey === group.key ? (
                            <input
                                ref={groupNameInputRef}
                                value={groupNameDraft}
                                maxLength={80}
                                className="h-7 min-w-0 flex-1 rounded-md border border-primary/40 bg-background px-2 text-xs font-semibold text-foreground outline-none ring-2 ring-primary/15"
                                onClick={(event) => event.stopPropagation()}
                                onChange={(event) => setGroupNameDraft(event.target.value)}
                                onBlur={() => setEditingGroupKey(null)}
                                onKeyDown={(event) => {
                                    event.stopPropagation();
                                    if (event.key === "Enter") {
                                        event.preventDefault();
                                        void saveGroupRename(group, groupNameDraft);
                                    }
                                    if (event.key === "Escape") {
                                        event.preventDefault();
                                        setEditingGroupKey(null);
                                    }
                                }}
                                aria-label={t("web.sidebar.renameProject")}
                            />
                        ) : (
                            <span className="min-w-0 truncate text-xs font-semibold uppercase tracking-wider text-muted-foreground">{group.label}</span>
                        )}
                        {group.workspacePath ? (
                            <div
                                className={cn(
                                    "pointer-events-none ml-auto flex shrink-0 translate-x-1 items-center opacity-0 transition-[opacity,transform] duration-150",
                                    "group-hover/header:pointer-events-auto group-hover/header:translate-x-0 group-hover/header:opacity-100",
                                    "group-focus-within/header:pointer-events-auto group-focus-within/header:translate-x-0 group-focus-within/header:opacity-100",
                                    presentationBusyKey === `group:${group.key}` && "pointer-events-auto translate-x-0 opacity-100",
                                )}
                            >
                                <button
                                    type="button"
                                    className={cn(
                                        "inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-wait disabled:opacity-60",
                                        group.pinned ? "text-primary hover:bg-primary/10" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                                    )}
                                    onClick={(event) => void toggleGroupPin(event, group)}
                                    disabled={Boolean(presentationBusyKey)}
                                    aria-pressed={group.pinned}
                                    aria-label={t(group.pinned ? "web.sidebar.unpinProject" : "web.sidebar.pinProject")}
                                    title={t(group.pinned ? "web.sidebar.unpinProject" : "web.sidebar.pinProject")}
                                >
                                    {presentationBusyKey === `group:${group.key}` ? (
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                    ) : (
                                        <Pin className={cn("h-4 w-4", group.pinned && "fill-current")} />
                                    )}
                                </button>
                                {group.creationBinding ? (
                                    <button
                                        type="button"
                                        className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-wait disabled:opacity-60"
                                        onClick={(event) => void createConversationInGroup(event, group)}
                                        disabled={Boolean(creatingGroupKey)}
                                        aria-label={t("web.sidebar.createInWorkspace", { value0: group.label })}
                                        title={t("web.sidebar.createInWorkspace", { value0: group.label })}
                                    >
                                        {creatingGroupKey === group.key ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                                    </button>
                                ) : null}
                            </div>
                        ) : null}
                    </div>
                )}

                {(isOpen || collapsed) && (
                    <div className="space-y-0.5">
                        {items.map((conv) => {
                            const canonicalSessionId = conv.sessionId || conv.id;
                            const activityState = getConversationActivityState(conv);

                            return (
                                <div
                                    key={canonicalSessionId}
                                    className={cn(
                                        "group/task relative flex cursor-pointer flex-col justify-center overflow-hidden rounded-lg transition-colors duration-150",
                                        collapsed ? "mx-auto h-10 w-10 items-center" : "w-full py-2 pl-3 pr-[4.25rem] hover:bg-muted/45",
                                        currentId === canonicalSessionId
                                            ? "bg-muted/60 font-medium text-foreground"
                                            : "text-muted-foreground hover:text-foreground",
                                    )}
                                    onClick={() => handleNavigation(canonicalSessionId)}
                                    onContextMenu={(event) => openConversationMenu(event, canonicalSessionId)}
                                    title={conv.title || t("web.generated.fca06b0605")}
                                >
                                    <div className="flex w-full items-center">
                                        {collapsed && (
                                            <div
                                                className={cn(
                                                    "shrink-0 text-muted-foreground/70 transition-colors [&>svg]:h-4 [&>svg]:w-4",
                                                    currentId === canonicalSessionId && "text-primary",
                                                )}
                                            >
                                                <MessageCircle className="h-4 w-4" />
                                            </div>
                                        )}
                                        {!collapsed && (
                                            <>
                                                <div className="min-w-0 flex-1">
                                                    <span className="flex min-w-0 items-center gap-2">
                                                        {editingSessionId === canonicalSessionId ? (
                                                            <input
                                                                ref={sessionTitleInputRef}
                                                                value={sessionTitleDraft}
                                                                maxLength={80}
                                                                className="h-7 min-w-0 flex-1 rounded-md border border-primary/40 bg-background px-2 text-sm text-foreground outline-none ring-2 ring-primary/15"
                                                                onClick={(event) => event.stopPropagation()}
                                                                onChange={(event) => setSessionTitleDraft(event.target.value)}
                                                                onBlur={() => setEditingSessionId(null)}
                                                                onKeyDown={(event) => {
                                                                    event.stopPropagation();
                                                                    if (event.key === "Enter") {
                                                                        event.preventDefault();
                                                                        void saveSessionRename(canonicalSessionId, sessionTitleDraft);
                                                                    }
                                                                    if (event.key === "Escape") {
                                                                        event.preventDefault();
                                                                        setEditingSessionId(null);
                                                                    }
                                                                }}
                                                                aria-label={t("web.sidebar.renameTask")}
                                                            />
                                                        ) : (
                                                            <span className="block min-w-0 flex-1 truncate text-sm">{conv.title || t("web.generated.fca06b0605")}</span>
                                                        )}
                                                        {conv.pinned && editingSessionId !== canonicalSessionId ? <Pin className="h-3.5 w-3.5 shrink-0 fill-current text-primary/75" /> : null}
                                                        {activityState === "active" && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />}
                                                        {activityState === "failed" && <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />}
                                                    </span>
                                                </div>

                                                <div
                                                    className={cn(
                                                        "pointer-events-none absolute right-1 top-1/2 flex -translate-y-1/2 translate-x-1 items-center opacity-0 transition-[opacity,transform] duration-150",
                                                        "group-hover/task:pointer-events-auto group-hover/task:translate-x-0 group-hover/task:opacity-100",
                                                        "group-focus-within/task:pointer-events-auto group-focus-within/task:translate-x-0 group-focus-within/task:opacity-100",
                                                        presentationBusyKey === `session:${canonicalSessionId}` && "pointer-events-auto translate-x-0 opacity-100",
                                                    )}
                                                >
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className={cn(
                                                            "h-7 w-7 rounded-md text-muted-foreground hover:bg-background/80 hover:text-foreground",
                                                            conv.pinned && "text-primary",
                                                        )}
                                                        onClick={(event) => {
                                                            event.stopPropagation();
                                                            void toggleSessionPin(canonicalSessionId);
                                                        }}
                                                        disabled={Boolean(presentationBusyKey)}
                                                        aria-pressed={Boolean(conv.pinned)}
                                                        aria-label={t(conv.pinned ? "web.sidebar.unpinTask" : "web.sidebar.pinTask")}
                                                        title={t(conv.pinned ? "web.sidebar.unpinTask" : "web.sidebar.pinTask")}
                                                    >
                                                        {presentationBusyKey === `session:${canonicalSessionId}` ? (
                                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                        ) : conv.pinned ? (
                                                            <PinOff className="h-3.5 w-3.5" />
                                                        ) : (
                                                            <Pin className="h-3.5 w-3.5" />
                                                        )}
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-7 w-7 rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                                                        onClick={(event) => {
                                                            event.stopPropagation();
                                                            setContextMenu(null);
                                                            setDeleteId(canonicalSessionId);
                                                        }}
                                                        aria-label={t("web.sidebar.deleteConversation")}
                                                        title={t("web.sidebar.deleteConversation")}
                                                    >
                                                        <Trash2 className="h-3.5 w-3.5" />
                                                    </Button>
                                                </div>
                                            </>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        );
    };

    const renderSidebarBody = (collapsed: boolean, mobile = false) => (
        <div className={cn("flex h-full w-full flex-col overflow-hidden transition-all duration-300", collapsed ? "items-center" : "")}>
            <div className={cn("transition-all duration-300", collapsed ? "px-2 py-4" : "p-4", mobile && "pb-3")}>
                <div className="flex items-center justify-between gap-2">
                    <Button
                        className={cn(
                            "shadow-lg shadow-primary/20 transition-all duration-300 hover:brightness-110 hover:shadow-primary/30",
                            "bg-gradient-to-r from-primary to-violet-600",
                            collapsed ? "h-10 w-10 rounded-xl p-0" : "h-11 flex-1 justify-start",
                        )}
                        onClick={handleNewChat}
                    >
                        <Plus className={cn("h-5 w-5", collapsed ? "" : "mr-2")} />
                        {!collapsed && <span className="font-medium">{t("web.generated.fca06b0605")}</span>}
                    </Button>

                    {mobile && (
                        <Button
                            variant="outline"
                            size="icon"
                            className="h-11 w-11 rounded-2xl border-border/60 bg-background/80 backdrop-blur"
                            onClick={() => setIsMobileOpen(false)}
                        >
                            <PanelLeftClose className="h-4 w-4" />
                        </Button>
                    )}
                </div>
            </div>

            <ScrollArea className="flex-1 w-full px-3 [&>[data-radix-scroll-area-viewport]>div]:!block [&_[data-radix-scroll-area-scrollbar]]:hidden">
                <div className="w-full max-w-full space-y-1 py-2">
                    {!collapsed && (
                        <div className="animate-in fade-in mb-3 flex items-center px-3 duration-500">
                            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">{t("web.generated.59a644a102")}</p>
                        </div>
                    )}

                    {!collapsed && createError ? (
                        <div role="alert" className="mx-3 mb-2 rounded-lg border border-destructive/25 bg-destructive/8 px-2.5 py-2 text-xs text-destructive">
                            {createError}
                        </div>
                    ) : null}

                    {conversations.length === 0 ? (
                        !collapsed && (
                            <div className="flex flex-col items-center justify-center py-10 text-center opacity-60">
                                <MessageCircle className="mb-2 h-8 w-8 text-muted-foreground" />
                                <p className="text-xs text-muted-foreground">{t("web.generated.8a30985d2c")}</p>
                            </div>
                        )
                    ) : (
                        <>
                            {groupedConvs.map((group, index) => renderGroup(group, collapsed, index))}
                        </>
                    )}
                </div>
            </ScrollArea>

        </div>
    );

    const contextConversation = contextMenu
        ? conversations.find((item) => (item.sessionId || item.id) === contextMenu.sessionId) || null
        : null;
    const contextGroup = groupContextMenu
        ? groupedConvs.find((group) => group.key === groupContextMenu.groupKey) || null
        : null;

    return (
        <>
            <div
                className={cn(
                    "group/sidebar relative hidden h-[calc(100vh-3.5rem)] flex-shrink-0 flex-col glass-panel transition-all duration-500 z-20 md:flex",
                    isCollapsed ? "w-[60px]" : "w-[280px]",
                )}
            >
                <div className="absolute -right-3 top-8 z-50 hidden transition-opacity duration-300 md:block">
                    <Button
                        variant="outline"
                        size="icon"
                        className="h-7 w-7 rounded-full border-border bg-background shadow-md transition-all duration-300 hover:bg-accent hover:text-accent-foreground"
                        onClick={() => setIsCollapsed(!isCollapsed)}
                        title={isCollapsed ? t("web.generated.431d44b6ca") : t("web.generated.99260f0e75")}
                    >
                        {isCollapsed ? <PanelLeftOpen className="h-3.5 w-3.5" /> : <PanelLeftClose className="h-3.5 w-3.5" />}
                    </Button>
                </div>

                {renderSidebarBody(isCollapsed)}
            </div>

            <Button
                type="button"
                variant="outline"
                size="icon"
                className="fixed left-3 top-[4.25rem] z-30 h-10 w-10 rounded-2xl border-border/60 bg-background/85 shadow-lg backdrop-blur md:hidden"
                onClick={() => setIsMobileOpen(true)}
                title={t("web.generated.7520df3a1c")}
            >
                <PanelLeftOpen className="h-4 w-4" />
            </Button>

            {isMobileOpen && (
                <>
                    <button
                        type="button"
                        className="fixed inset-0 z-40 bg-black/35 backdrop-blur-[2px] md:hidden"
                        onClick={() => setIsMobileOpen(false)}
                        aria-label={t("web.generated.8dcb42e508")}
                    />

                    <div className="fixed inset-y-14 left-0 z-50 w-[min(22rem,86vw)] border-r border-border/40 bg-zinc-50/95 shadow-2xl backdrop-blur-2xl dark:bg-zinc-950/95 md:hidden">
                        {renderSidebarBody(false, true)}
                    </div>
                </>
            )}

            {contextMenu && (
                <div
                    className="fixed z-[80] w-[184px] overflow-hidden rounded-xl border border-border/70 bg-background/95 p-1 text-sm shadow-2xl backdrop-blur-xl"
                    style={{ left: contextMenu.x, top: contextMenu.y }}
                    onClick={(event) => event.stopPropagation()}
                >
                    <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-foreground transition hover:bg-accent"
                        onClick={() => beginSessionRename(contextMenu.sessionId)}
                    >
                        <Pencil className="h-4 w-4 text-muted-foreground" />
                        <span>{t("web.sidebar.renameTask")}</span>
                    </button>
                    <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-foreground transition hover:bg-accent"
                        onClick={() => void toggleSessionPin(contextMenu.sessionId)}
                    >
                        {contextConversation?.pinned ? <PinOff className="h-4 w-4 text-muted-foreground" /> : <Pin className="h-4 w-4 text-muted-foreground" />}
                        <span>{t(contextConversation?.pinned ? "web.sidebar.unpinTask" : "web.sidebar.pinTask")}</span>
                    </button>
                    <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-foreground transition hover:bg-accent"
                        onClick={() => void copySessionId(contextMenu.sessionId)}
                    >
                        <Copy className="h-4 w-4 text-muted-foreground" />
                        <span>{copiedSessionId === contextMenu.sessionId ? t("web.sidebar.copiedSessionId") : t("web.sidebar.copySessionId")}</span>
                    </button>
                    <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-foreground transition hover:bg-accent"
                        onClick={() => continueInNewConversation(contextMenu.sessionId)}
                    >
                        <MessagesSquare className="h-4 w-4 text-muted-foreground" />
                        <span>{t("web.sidebar.continueInNewSession")}</span>
                    </button>
                    <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-destructive transition hover:bg-destructive/10"
                        onClick={() => {
                            setDeleteId(contextMenu.sessionId);
                            setContextMenu(null);
                        }}
                    >
                        <Trash2 className="h-4 w-4" />
                        <span>{t("web.sidebar.deleteConversation")}</span>
                    </button>
                </div>
            )}

            {groupContextMenu && contextGroup && (
                <div
                    className="fixed z-[80] w-[192px] overflow-hidden rounded-xl border border-border/70 bg-background/95 p-1 text-sm shadow-2xl backdrop-blur-xl"
                    style={{ left: groupContextMenu.x, top: groupContextMenu.y }}
                    onClick={(event) => event.stopPropagation()}
                >
                    <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-foreground transition hover:bg-accent"
                        onClick={() => beginGroupRename(contextGroup)}
                    >
                        <Pencil className="h-4 w-4 text-muted-foreground" />
                        <span>{t("web.sidebar.renameProject")}</span>
                    </button>
                    <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-foreground transition hover:bg-accent"
                        onClick={() => void toggleGroupPin(null, contextGroup)}
                    >
                        {contextGroup.pinned ? <PinOff className="h-4 w-4 text-muted-foreground" /> : <Pin className="h-4 w-4 text-muted-foreground" />}
                        <span>{t(contextGroup.pinned ? "web.sidebar.unpinProject" : "web.sidebar.pinProject")}</span>
                    </button>
                </div>
            )}

            <Dialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
                <DialogContent className="glass-card border-none sm:max-w-[425px]">
                    <DialogHeader>
                        <DialogTitle>{t("web.generated.75914e5f9d")}</DialogTitle>
                        <DialogDescription>{t("web.generated.613c74ac06")}</DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setDeleteId(null)}>{t("web.generated.d94a8eaf28")}</Button>
                        <Button variant="destructive" onClick={confirmDelete}>{t("web.generated.6cba6a2c08")}</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

        </>
    );
}
