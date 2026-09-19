"use client";

import { ChevronDown, CornerDownRight, Edit3, GripVertical, Loader2, MoreHorizontal, Trash2 } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import type { QueuedChatMessage } from "@/lib/chat-queue";

export type QueueUiLabels = {
    title: string;
    hint: string;
    pending: string;
    promoted: string;
    empty: string;
    guide: string;
    edit: string;
    closeQueue: string;
    collapse: string;
    expand: string;
    editTitle: string;
    editHint: string;
    editPlaceholder: string;
    cancel: string;
    save: string;
};

export function QueuedMessagesStrip({
    messages,
    collapsed,
    menuOpenId,
    busyId,
    labels,
    onToggleCollapsed,
    onOpenMenu,
    onPromote,
    onCancel,
    onEdit,
}: {
    messages: QueuedChatMessage[];
    collapsed: boolean;
    menuOpenId: string | null;
    busyId: string;
    labels: QueueUiLabels;
    onToggleCollapsed: () => void;
    onOpenMenu: (id: string | null) => void;
    onPromote: (item: QueuedChatMessage) => void;
    onCancel: (item: QueuedChatMessage) => void;
    onEdit: (item: QueuedChatMessage) => void;
}) {
    if (messages.length === 0) {
        return null;
    }

    return (
        <section className="mx-auto w-full max-w-4xl overflow-hidden rounded-[1.15rem] border border-border/60 bg-background/82 shadow-[0_12px_32px_rgba(15,23,42,0.08)] backdrop-blur-xl dark:bg-zinc-900/72 dark:shadow-[0_18px_48px_rgba(0,0,0,0.26)]">
            <button
                type="button"
                className="flex h-9 w-full items-center gap-2 border-b border-border/45 px-3 text-left text-xs text-muted-foreground transition hover:bg-muted/35"
                onClick={onToggleCollapsed}
                aria-expanded={!collapsed}
            >
                <CornerDownRight className="h-3.5 w-3.5 shrink-0" />
                <span className="font-medium text-foreground">{labels.title}</span>
                <span className="rounded-full border border-border/60 bg-muted/45 px-1.5 py-0.5 text-[10px] leading-none text-muted-foreground">
                    {messages.length}
                </span>
                <span className="min-w-0 flex-1 truncate">{labels.hint}</span>
                <ChevronDown
                    className={cn(
                        "h-3.5 w-3.5 shrink-0 transition-transform",
                        collapsed && "-rotate-90",
                    )}
                    aria-label={collapsed ? labels.expand : labels.collapse}
                />
            </button>
            {!collapsed ? (
                <div className="max-h-36 overflow-y-auto px-2 py-1.5">
                    {messages.map((item, index) => {
                        const state = String(item.state || "pending").trim().toLowerCase();
                        const promoted = state === "promoted";
                        const itemBusy = busyId === item.id;
                        return (
                            <div
                                key={item.id}
                                className={cn(
                                    "group relative flex min-h-9 items-center gap-2 rounded-xl px-2 py-1.5 text-sm transition",
                                    "hover:bg-muted/40",
                                    promoted && "border border-primary/25 bg-primary/5",
                                )}
                            >
                                <GripVertical className="h-3.5 w-3.5 shrink-0 text-muted-foreground/45" />
                                <CornerDownRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
                                <span className="w-5 shrink-0 text-xs font-semibold tabular-nums text-muted-foreground">
                                    {item.ordinal || index + 1}
                                </span>
                                <div className="min-w-0 flex-1">
                                    <div className="truncate font-medium text-foreground">
                                        {item.content || labels.empty}
                                    </div>
                                    <div className={cn("text-[11px] leading-4", promoted ? "text-primary" : "text-muted-foreground")}>
                                        {promoted ? labels.promoted : labels.pending}
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    className={cn(
                                        "inline-flex h-7 shrink-0 items-center gap-1 rounded-lg px-2 text-xs font-medium text-muted-foreground transition hover:bg-muted hover:text-foreground",
                                        promoted && "pointer-events-none opacity-45",
                                    )}
                                    disabled={promoted || itemBusy}
                                    onClick={() => onPromote(item)}
                                >
                                    {itemBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CornerDownRight className="h-3.5 w-3.5" />}
                                    <span>{labels.guide}</span>
                                </button>
                                <button
                                    type="button"
                                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-destructive/10 hover:text-destructive"
                                    disabled={itemBusy}
                                    onClick={() => onCancel(item)}
                                    aria-label={labels.closeQueue}
                                    title={labels.closeQueue}
                                >
                                    {itemBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                                </button>
                                <DropdownMenu
                                    open={menuOpenId === item.id}
                                    onOpenChange={(open) => onOpenMenu(open ? item.id : null)}
                                >
                                    <DropdownMenuTrigger asChild>
                                        <button
                                            type="button"
                                            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
                                            aria-label={labels.edit}
                                            title={labels.edit}
                                        >
                                            <MoreHorizontal className="h-3.5 w-3.5" />
                                        </button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent
                                        align="end"
                                        side="top"
                                        sideOffset={4}
                                        collisionPadding={12}
                                        className="z-[120] w-36"
                                    >
                                        <DropdownMenuItem
                                            className="gap-2 text-xs"
                                            onSelect={() => onEdit(item)}
                                            disabled={promoted}
                                        >
                                            <Edit3 className="h-3.5 w-3.5" />
                                            <span>{labels.edit}</span>
                                        </DropdownMenuItem>
                                        <DropdownMenuItem
                                            className="gap-2 text-xs text-destructive focus:bg-destructive/10 focus:text-destructive"
                                            onSelect={() => onCancel(item)}
                                        >
                                            <Trash2 className="h-3.5 w-3.5" />
                                            <span>{labels.closeQueue}</span>
                                        </DropdownMenuItem>
                                    </DropdownMenuContent>
                                </DropdownMenu>
                            </div>
                        );
                    })}
                </div>
            ) : null}
        </section>
    );
}

export function QueuedMessageEditDialog({
    item,
    value,
    busy,
    labels,
    onChange,
    onCancel,
    onSave,
}: {
    item: QueuedChatMessage | null;
    value: string;
    busy: boolean;
    labels: QueueUiLabels;
    onChange: (value: string) => void;
    onCancel: () => void;
    onSave: () => void;
}) {
    if (!item) {
        return null;
    }

    return (
        <div className="fixed inset-0 z-[120] flex items-end justify-center bg-black/30 px-4 pb-6 backdrop-blur-sm sm:items-center sm:pb-0">
            <div className="w-full max-w-lg rounded-2xl border border-border/70 bg-background p-4 shadow-[0_24px_80px_rgba(15,23,42,0.24)] dark:shadow-[0_24px_80px_rgba(0,0,0,0.42)]">
                <div className="mb-3">
                    <h2 className="text-base font-semibold text-foreground">{labels.editTitle}</h2>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">{labels.editHint}</p>
                </div>
                <textarea
                    value={value}
                    onChange={(event) => onChange(event.target.value)}
                    placeholder={labels.editPlaceholder}
                    className="min-h-28 w-full resize-none rounded-xl border border-border/70 bg-muted/25 px-3 py-2 text-sm text-foreground outline-none transition placeholder:text-muted-foreground/55 focus:border-primary/45 focus:ring-2 focus:ring-primary/15"
                />
                <div className="mt-4 flex justify-end gap-2">
                    <button
                        type="button"
                        className="rounded-xl border border-border/70 bg-background px-3 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground"
                        onClick={onCancel}
                    >
                        {labels.cancel}
                    </button>
                    <button
                        type="button"
                        className="inline-flex items-center gap-2 rounded-xl bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-55"
                        disabled={busy || !value.trim()}
                        onClick={onSave}
                    >
                        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                        {labels.save}
                    </button>
                </div>
            </div>
        </div>
    );
}
