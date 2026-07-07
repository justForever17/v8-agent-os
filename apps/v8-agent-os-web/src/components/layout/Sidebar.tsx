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

        return (
            <div key={group.key} className="mb-3">
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
                                                        <span className="block min-w-0 flex-1 truncate text-sm">{conv.title || t("web.generated.fca06b0605")}</span>
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
                        <div className="animate-in fade-in mb-3 flex items-center justify-between px-3 duration-500">
                            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">{t("web.generated.59a644a102")}</p>
                            {conversations.length > 0 && (
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-5 w-5 text-muted-foreground transition-colors hover:text-destructive"
                                    onClick={() => setIsClearing(true)}
                                    title={t("web.generated.2d356aee49")}
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

            <Dialog open={isClearing} onOpenChange={setIsClearing}>
                <DialogContent className="glass-card border-none sm:max-w-[425px]">
                    <DialogHeader>
                        <DialogTitle>{t("web.generated.cb547f1f49")}</DialogTitle>
                        <DialogDescription>{t("web.generated.5b99063251")}</DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setIsClearing(false)}>{t("web.generated.d94a8eaf28")}</Button>
                        <Button variant="destructive" onClick={confirmClear}>{t("web.generated.22fdb44dd7")}</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}
