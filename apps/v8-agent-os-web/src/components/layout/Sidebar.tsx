"use client";

import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
    Plus,
    MessageSquare,
    Trash2,
    PanelLeftClose,
    PanelLeftOpen,
    MessageCircle,
    ChevronDown,
    ChevronRight,
    Globe,
    Clock,
    Zap,
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

import { useConversationContext, Conversation } from "@/context/ConversationContext";
import { useRouter, useSearchParams } from "next/navigation";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";

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

    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({
        channels: true,
        cron: false,
        hooks: false,
        web: true,
    });

    const toggleGroup = (key: string) => {
        setOpenGroups((prev) => ({ ...prev, [key]: !prev[key] }));
    };

    const groupedConvs = useMemo(() => {
        const groups = { channels: [], cron: [], hooks: [], web: [] } as Record<string, Conversation[]>;
        conversations.forEach((conv) => {
            if (conv.sourceGroup === "cron") groups.cron.push(conv);
            else if (conv.sourceGroup === "hooks") groups.hooks.push(conv);
            else if (conv.sourceGroup === "channels") groups.channels.push(conv);
            else groups.web.push(conv);
        });
        return groups;
    }, [conversations]);

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

    const renderGroup = (key: string, title: string, titleEn: string, iconNode: React.ReactNode, collapsed: boolean) => {
        const items = groupedConvs[key];
        const isOpen = openGroups[key];

        return (
            <div className="mb-4">
                {!collapsed && (
                    <div
                        className="group/header mb-1 flex cursor-pointer items-center rounded-md px-3 py-1.5 transition-colors hover:bg-accent/40"
                        onClick={() => toggleGroup(key)}
                    >
                        {isOpen ? (
                            <ChevronDown className="mr-1.5 h-3.5 w-3.5 text-muted-foreground" />
                        ) : (
                            <ChevronRight className="mr-1.5 h-3.5 w-3.5 text-muted-foreground" />
                        )}
                        <div className="mr-1.5 flex items-center text-muted-foreground [&>svg]:h-3.5 [&>svg]:w-3.5">{iconNode}</div>
                        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t(lt(title, titleEn))}</span>
                        <span className="ml-auto rounded-full bg-accent/50 px-1.5 py-0.5 text-[10px] text-muted-foreground">{items.length}</span>
                    </div>
                )}

                {(isOpen || collapsed) && (
                    <div className="space-y-0.5">
                        {items.map((conv) => {
                            const canonicalSessionId = conv.sessionId || conv.id;
                            const scopeTags = Array.isArray(conv.scopeTags) ? conv.scopeTags : [];
                            const ownerRuntime = typeof conv.ownerRuntime === "string" ? conv.ownerRuntime : null;
                            const workflowStatus = typeof conv.workflowStatus === "string" ? conv.workflowStatus : null;
                            const statusLabel = typeof conv.statusLabel === "string" ? conv.statusLabel : workflowStatus;
                            const showWorkflowBadge = workflowStatus && workflowStatus !== "completed";
                            const showRecoverableBadge = Boolean(conv.recoverable) && workflowStatus && workflowStatus !== "completed";
                            const pendingApprovalCount = Number(conv.pendingApprovalCount || 0);
                            const previewExcerpt = typeof conv.previewExcerpt === "string" ? conv.previewExcerpt : "";
                            const currentStepTitle = typeof conv.currentStepTitle === "string" ? conv.currentStepTitle : "";
                            const secondaryText = (showWorkflowBadge && currentStepTitle) || previewExcerpt;

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
                                                    <span className="block truncate text-sm">{conv.title || t(lt("新对话", "New chat"))}</span>
                                                    {scopeTags.length > 0 && (
                                                        <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                                                            {scopeTags.map((tag) => (
                                                                <span
                                                                    key={`${conv.id}-${tag}`}
                                                                    className="rounded-full bg-accent/70 px-1.5 py-0.5 text-foreground/80"
                                                                >
                                                                    {tag}
                                                                </span>
                                                            ))}
                                                        </div>
                                                    )}
                                                    {(ownerRuntime || showWorkflowBadge) && (
                                                        <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                                                            {ownerRuntime && (
                                                                <span className="rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-emerald-700">
                                                                    {ownerRuntime}
                                                                </span>
                                                            )}
                                                            {showWorkflowBadge && (
                                                                <span className="rounded-full bg-amber-500/10 px-1.5 py-0.5 text-amber-700">
                                                                    {statusLabel}
                                                                </span>
                                                            )}
                                                            {showRecoverableBadge && (
                                                                <span className="rounded-full bg-sky-500/10 px-1.5 py-0.5 text-sky-700">
                                                                    {t(lt("可恢复", "Recoverable"))}
                                                                </span>
                                                            )}
                                                            {pendingApprovalCount > 0 && (
                                                                <span className="rounded-full bg-fuchsia-500/10 px-1.5 py-0.5 text-fuchsia-700">
                                                                    {t(lt(`审批 ${pendingApprovalCount}`, `Approval ${pendingApprovalCount}`))}
                                                                </span>
                                                            )}
                                                        </div>
                                                    )}
                                                    {secondaryText && (
                                                        <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground/80">
                                                            {secondaryText}
                                                        </p>
                                                    )}
                                                    {(conv.controls?.canRetry || conv.controls?.canResume) && (
                                                        <div className="mt-1 flex flex-wrap gap-1 text-[10px]">
                                                            {conv.controls?.canResume && (
                                                                <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-primary">
                                                                    {t(lt("可继续", "Resume"))}
                                                                </span>
                                                            )}
                                                            {conv.controls?.canRetry && (
                                                                <span className="rounded-full bg-rose-500/10 px-1.5 py-0.5 text-rose-700">
                                                                    {t(lt("可重试", "Retry"))}
                                                                </span>
                                                            )}
                                                        </div>
                                                    )}
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
                            {groupedConvs.channels.length > 0 && renderGroup("channels", "第三方渠道", "Channels", <Globe />, collapsed)}
                            {groupedConvs.cron.length > 0 && renderGroup("cron", "定时任务", "Cron", <Clock />, collapsed)}
                            {groupedConvs.hooks.length > 0 && renderGroup("hooks", "触发器与钩子", "Hooks", <Zap />, collapsed)}
                            {groupedConvs.web.length > 0 && renderGroup("web", "网页对话", "Web chat", <MessageSquare />, collapsed)}
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
