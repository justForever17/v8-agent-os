"use client";

import { cn } from "@admin/lib/utils";
import { CardContent, CardHeader, CardTitle } from "@admin/components/ui/card";
import { AdminHoverTitle } from "@admin/components/admin-shell/AdminHoverInfo";
import { AdminSurfaceCard } from "@admin/components/admin-shell/AdminSurfaceCard";
import { useResolveText } from "@admin/components/providers/LocaleProvider";

export function ConfigCard({
    id,
    title,
    description,
    children,
    footer,
    variant = "summary",
    bodyHeight = "auto",
    bodyScroll = "none",
    allowOverflow = false,
    className,
    contentClassName,
    collapsible = false,
    defaultOpen = false,
}: {
    id?: string;
    title: string;
    description?: string;
    children: React.ReactNode;
    footer?: React.ReactNode;
    variant?: "summary" | "list" | "editor";
    bodyHeight?: "auto" | 360 | 420 | 520 | "clamp";
    bodyScroll?: "none" | "auto";
    allowOverflow?: boolean;
    className?: string;
    contentClassName?: string;
    collapsible?: boolean;
    defaultOpen?: boolean;
}) {
    const resolveText = useResolveText();

    const resolvedHeightClass =
        variant !== "list" || bodyHeight === "auto"
            ? ""
            : bodyHeight === 360
              ? "max-h-[360px]"
              : bodyHeight === 420
                ? "max-h-[420px]"
                : bodyHeight === 520
                  ? "max-h-[520px]"
                  : "h-[clamp(360px,52vh,640px)]";

    const resolvedScrollClass =
        allowOverflow
            ? "overflow-visible"
            : variant === "list" && bodyScroll === "auto"
            ? "overflow-y-auto pr-1"
            : "";

    const body = <CardContent className="min-h-0 space-y-4 overflow-visible px-4 pb-4">
        <div className={cn("min-h-0", resolvedHeightClass, resolvedScrollClass, contentClassName)}>{children}</div>
        {footer}
    </CardContent>;

    if (collapsible) return <AdminSurfaceCard id={id} className={cn("min-h-0", className)}>
        <details open={defaultOpen || undefined}>
            <summary className="cursor-pointer px-4 py-3 text-[14px] font-medium text-foreground marker:text-muted-foreground">{resolveText(title)}</summary>
            {description ? <div className="px-4 pb-2"><AdminHoverTitle title={resolveText(title)} description={resolveText(description)} /></div> : null}
            {body}
        </details>
    </AdminSurfaceCard>;

    return (
        <AdminSurfaceCard id={id} className={cn("min-h-0", allowOverflow ? "overflow-visible" : "", className)}>
            <CardHeader className="space-y-1 px-4 py-3">
                <CardTitle className="text-[14px] leading-[22px] text-foreground">
                    <AdminHoverTitle title={resolveText(title)} description={description ? resolveText(description) : undefined} />
                </CardTitle>
            </CardHeader>
            {body}
        </AdminSurfaceCard>
    );
}
