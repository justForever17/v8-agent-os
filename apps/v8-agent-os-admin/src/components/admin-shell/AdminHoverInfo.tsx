"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CircleHelp } from "lucide-react";
import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";

/** One help surface for pointer, keyboard and touch. Mounted only while read. */
export function AdminHoverInfo({ children, content, lines, align = "left", className, triggerClassName, panelClassName }: {
    children: ReactNode; content?: ReactNode; lines?: ReactNode[]; align?: "left" | "right";
    className?: string; triggerClassName?: string; panelClassName?: string;
}) {
    const t = useT();
    const id = useId();
    const trigger = useRef<HTMLButtonElement>(null);
    const panel = useRef<HTMLSpanElement>(null);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [open, setOpen] = useState(false);
    const [position, setPosition] = useState({ left: 8, top: 8 });
    const hasContent = Boolean(content) || Boolean(lines?.length);
    const clearTimer = () => { if (timer.current) clearTimeout(timer.current); timer.current = null; };
    const show = (delay = 0) => { clearTimer(); if (delay) timer.current = setTimeout(() => setOpen(true), delay); else setOpen(true); };
    const leave = () => { clearTimer(); timer.current = setTimeout(() => { if (!panel.current?.contains(document.activeElement) && document.activeElement !== trigger.current) setOpen(false); }, 140); };
    useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
    useLayoutEffect(() => {
        if (!open) return;
        const place = () => {
            const anchor = trigger.current?.getBoundingClientRect();
            const box = panel.current?.getBoundingClientRect();
            if (!anchor || !box) return;
            const left = Math.max(8, Math.min(align === "right" ? anchor.right - box.width : anchor.left, window.innerWidth - box.width - 8));
            const top = anchor.bottom + box.height + 12 > window.innerHeight ? Math.max(8, anchor.top - box.height - 8) : anchor.bottom + 8;
            setPosition({ left, top });
        };
        const key = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); setOpen(false); } };
        const outside = (event: PointerEvent) => { if (!trigger.current?.contains(event.target as Node) && !panel.current?.contains(event.target as Node)) setOpen(false); };
        place();
        window.addEventListener("resize", place);
        window.addEventListener("scroll", place, true);
        document.addEventListener("keydown", key, true);
        document.addEventListener("pointerdown", outside);
        return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); document.removeEventListener("keydown", key, true); document.removeEventListener("pointerdown", outside); };
    }, [open, align]);
    if (!hasContent) return <>{children}</>;
    return <span className={cn("inline-flex max-w-full items-center gap-1", className)}>
        <span className={cn("inline-flex min-w-0 max-w-full items-center", triggerClassName)} onPointerEnter={(event) => { if (event.pointerType === "mouse") show(350); }} onPointerLeave={leave}>{children}</span>
        <button ref={trigger} type="button" className="admin-help-trigger inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={t("admin.experience.help")} aria-expanded={open} aria-controls={open ? id : undefined} aria-describedby={open ? id : undefined} onFocus={() => show()} onBlur={leave} onPointerEnter={(event) => { if (event.pointerType === "mouse") show(350); }} onPointerLeave={leave} onClick={(event) => { event.stopPropagation(); clearTimer(); setOpen(value => !value); }}><CircleHelp size={14} /></button>
        {open && createPortal(<span ref={panel} id={id} role="tooltip" style={position} onPointerEnter={clearTimer} onPointerLeave={leave} className={cn("admin-help-panel fixed z-[90] block w-[300px] max-w-[calc(100vw-16px)] max-h-[min(400px,70dvh)] overflow-auto rounded-lg border border-border bg-popover p-3 text-left text-[12px] font-normal leading-[18px] text-popover-foreground shadow-lg", panelClassName)}>{lines?.length ? lines.map((line, index) => <span key={index} className="block break-words">{line}</span>) : content}</span>, document.body)}
    </span>;
}

export function AdminHoverTitle({ title, description, icon, className, titleClassName }: { title: ReactNode; description?: ReactNode; icon?: ReactNode; className?: string; titleClassName?: string }) {
    return <AdminHoverInfo content={description} className={className} triggerClassName={cn("gap-2", titleClassName)}>{icon}<span className="min-w-0">{title}</span></AdminHoverInfo>;
}
