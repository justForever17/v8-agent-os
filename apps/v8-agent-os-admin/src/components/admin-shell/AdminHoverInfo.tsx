"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function AdminHoverInfo({
    children,
    content,
    lines,
    align = "left",
    className,
    triggerClassName,
    panelClassName,
}: {
    children: ReactNode;
    content?: ReactNode;
    lines?: Array<ReactNode>;
    align?: "left" | "right";
    className?: string;
    triggerClassName?: string;
    panelClassName?: string;
}) {
    const hasContent = Boolean(content) || Boolean(lines?.length);
    if (!hasContent) {
        return <>{children}</>;
    }

    return (
        <span className={cn("group/admin-hover relative inline-flex max-w-full overflow-visible", className)}>
            <span className={cn("inline-flex max-w-full cursor-help items-center", triggerClassName)}>
                {children}
            </span>
            <span
                className={cn(
                    "pointer-events-none absolute top-full z-50 mt-2 hidden w-80 max-w-[calc(100vw-2rem)] rounded-xl bg-slate-950 p-3 text-left text-[11px] font-normal leading-5 text-white shadow-2xl ring-1 ring-white/10 group-hover/admin-hover:block",
                    align === "right" ? "right-0" : "left-0",
                    panelClassName,
                )}
            >
                {lines?.length ? lines.map((line, index) => (
                    <span key={index} className="block truncate">{line}</span>
                )) : content}
            </span>
        </span>
    );
}

export function AdminHoverTitle({
    title,
    description,
    icon,
    className,
    titleClassName,
}: {
    title: ReactNode;
    description?: ReactNode;
    icon?: ReactNode;
    className?: string;
    titleClassName?: string;
}) {
    return (
        <AdminHoverInfo
            content={description}
            className={className}
            triggerClassName={cn("gap-2", titleClassName)}
            panelClassName="text-sm leading-6"
        >
            {icon}
            <span className="truncate">{title}</span>
        </AdminHoverInfo>
    );
}
