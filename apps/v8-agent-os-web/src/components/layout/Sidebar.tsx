"use client";

import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
    Plus,
    Trash2,
    PanelLeftClose,
    PanelLeftOpen,
    MessageCircle,
    ChevronDown,
    ChevronRight,
    Folder,
    Loader2,
    AlertCircle,
} from "lucide-react";
import { useMemo, useState } from "react";
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
import { lt } from "@/lib/locale";
import { getConversationActivityState, groupConversationsByWorkspace, type ConversationWorkspaceGroup } from "@/lib/conversation-groups";

export function Sidebar() {
    const { conversations, deleteConversation, clearConversations } = useConversationContext();
    const router = useRouter();
    const searchParams = useSearchParams();
    const currentId = searchParams.get("id");
    const t = useT();

    const [isCollapsed, setIsCollapsed] = useState(false);
    const [isMobileOpen, setIsMobileOpen] = useState(false);
    const [deleteId, setDeleteId] = useState<string | null>(null);
    const [isClearing, setIsClearing] = useState(false);

    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

    const toggleGroup = (key: string) => {
        setOpenGroups((prev) => ({ ...prev, [key]: !prev[key] }));
    };

    const groupedConvs = useMemo(() => groupConversationsByWorkspace(conversations, {
        mainWorkspace: t(lt("主工作区", "Main workspace")),
        externalWorkspace: t(lt("外部路径", "External path")),
        unbound: t(lt("未绑定对话", "Unbound chats")),
        workspace: t(lt("工作区", "Workspace")),
    }), [conversations, t]);

    const handleNewChat = () => {
        setIsMobileOpen(false);
        router.push("/chat?new=1");
    };

    const handleNavigation = (id: string) => {
        setIsMobileOpen(false);
        router.push(`/chat?id=${id}`);
    };

    const confirmDelete = async () => {
        if (!deleteId) return;
        await deleteConversation(deleteId);
        if (currentId === deleteId) {
            router.push("/chat");
        }
        setDeleteId(null);
    };

    const confirmClear = async () => {
        await clearConversations();
        router.push("/chat");
        setIsClearing(false);
    };

    const renderGroup = (group: ConversationWorkspaceGroup, collapsed: boolean, index: number) => {
        const items = group.items;
        const hasActiveConversation = items.some((item) => (item.sessionId || item.id) === currentId);
        const isOpen = openGroups[group.key] ?? (index === 0 || hasActiveConversation);
        const iconNode = <Folder />;

        return (
            <div className="mb-3">
                {!collapsed && (
                    <div
                        className="group/header mb-1 flex cursor-pointer items-center rounded-md px-3 py-1.5 transition-colors hover:bg-accent/40"
                        onClick={() => toggleGroup(group.key)}
                    >
                        {isOpen ? (
                            <ChevronDown className="mr-1.5 h-3.5 w-3.5 text-muted-foreground" />
                        ) : (
                            <ChevronRight className="mr-1.5 h-3.5 w-3.5 text-muted-foreground" />
                        )}
                        <div className="mr-1.5 flex items-center text-muted-foreground [&>svg]:h-3.5 [&>svg]:w-3.5">{iconNode}</div>
                        <span className="min-w-0 truncate text-xs font-semibold uppercase tracking-wider text-muted-foreground">{group.label}</span>
                        <span className="ml-auto rounded-full bg-accent/50 px-1.5 py-0.5 text-[10px] text-muted-foreground">{items.length}</span>
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
                                        "group relative flex cursor-pointer flex-col justify-center overflow-hidden rounded-xl transition-all duration-200",
                                        collapsed ? "mx-auto h-10 w-10 items-center" : "w-full py-2.5 pl-3 pr-9 hover:bg-accent/60",
                                        currentId === canonicalSessionId
                                            ? "bg-accent/80 font-medium text-accent-foreground shadow-sm"
                                            : "text-muted-foreground hover:text-foreground",
                                    )}
                                    onClick={() => handleNavigation(canonicalSessionId)}
                                    title={conv.title || t(lt("新对话", "New chat"))}
                                >
                                    <div className="flex w-full items-center">
                                        <div
                                            className={cn(
                                                "shrink-0 text-muted-foreground/70 transition-colors [&>svg]:h-4 [&>svg]:w-4",
                                                currentId === canonicalSessionId && "text-primary",
                                                !collapsed && "mr-3",
                                            )}
                                        >
                                            {iconNode}
                                        </div>
                                        {!collapsed && (
                                            <>
                                                <div className="min-w-0 flex-1">
                                                    <span className="flex min-w-0 items-center gap-2">
                                                        <span className="block min-w-0 flex-1 truncate text-sm">{conv.title || t(lt("新对话", "New chat"))}</span>
                                                        {activityState === "active" && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />}
                                                        {activityState === "failed" && <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />}
                                                    </span>
                                                </div>

                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 scale-90 text-muted-foreground opacity-0 transition-all duration-200 hover:bg-destructive/10 hover:text-destructive group-hover:scale-100 group-hover:opacity-100"
                                                    onClick={(event) => {
                                                        event.stopPropagation();
                                                        setDeleteId(canonicalSessionId);
                                                    }}
                                                >
                                                    <Trash2 className="h-3.5 w-3.5" />
                                                </Button>
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
                        {!collapsed && <span className="font-medium">{t(lt("新对话", "New chat"))}</span>}
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
                        <div className="animate-in fade-in mb-3 flex items-center justify-between px-3 duration-500">
                            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">{t(lt("历史记录", "History"))}</p>
                            {conversations.length > 0 && (
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-5 w-5 text-muted-foreground transition-colors hover:text-destructive"
                                    onClick={() => setIsClearing(true)}
                                    title={t(lt("清空历史", "Clear history"))}
                                >
                                    <Trash2 className="h-3.5 w-3.5" />
                                </Button>
                            )}
                        </div>
                    )}

                    {conversations.length === 0 ? (
                        !collapsed && (
                            <div className="flex flex-col items-center justify-center py-10 text-center opacity-60">
                                <MessageCircle className="mb-2 h-8 w-8 text-muted-foreground" />
                                <p className="text-xs text-muted-foreground">{t(lt("暂无历史记录", "No history yet"))}</p>
                            </div>
                        )
                    ) : (
                        <>
                            {groupedConvs.map((group, index) => renderGroup(group, collapsed, index))}
                        </>
                    )}
                </div>
            </ScrollArea>

            <div className="mt-auto border-t border-border/40 bg-background/30 backdrop-blur-sm" />
        </div>
    );

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
                        title={isCollapsed ? t(lt("展开侧边栏", "Expand sidebar")) : t(lt("折叠侧边栏", "Collapse sidebar"))}
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
                title={t(lt("打开历史记录", "Open history"))}
            >
                <PanelLeftOpen className="h-4 w-4" />
            </Button>

            {isMobileOpen && (
                <>
                    <button
                        type="button"
                        className="fixed inset-0 z-40 bg-black/35 backdrop-blur-[2px] md:hidden"
                        onClick={() => setIsMobileOpen(false)}
                        aria-label={t(lt("关闭历史记录", "Close history"))}
                    />

                    <div className="fixed inset-y-14 left-0 z-50 w-[min(22rem,86vw)] border-r border-border/40 bg-zinc-50/95 shadow-2xl backdrop-blur-2xl dark:bg-zinc-950/95 md:hidden">
                        {renderSidebarBody(false, true)}
                    </div>
                </>
            )}

            <Dialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
                <DialogContent className="glass-card border-none sm:max-w-[425px]">
                    <DialogHeader>
                        <DialogTitle>{t(lt("删除对话", "Delete chat"))}</DialogTitle>
                        <DialogDescription>{t(lt("确定要删除这个对话吗？此操作无法撤销。", "Delete this chat? This action cannot be undone."))}</DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setDeleteId(null)}>{t(lt("取消", "Cancel"))}</Button>
                        <Button variant="destructive" onClick={confirmDelete}>{t(lt("删除", "Delete"))}</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={isClearing} onOpenChange={setIsClearing}>
                <DialogContent className="glass-card border-none sm:max-w-[425px]">
                    <DialogHeader>
                        <DialogTitle>{t(lt("清空历史记录", "Clear history"))}</DialogTitle>
                        <DialogDescription>{t(lt("确定要清空所有历史记录吗？这将无法恢复。", "Clear all history? This cannot be undone."))}</DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setIsClearing(false)}>{t(lt("取消", "Cancel"))}</Button>
                        <Button variant="destructive" onClick={confirmClear}>{t(lt("清空全部", "Clear all"))}</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}
