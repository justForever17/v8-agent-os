"use client";

import { cn } from "@/lib/utils";
import { CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AdminHoverTitle } from "@/components/admin-shell/AdminHoverInfo";
import { AdminSurfaceCard } from "@/components/admin-shell/AdminSurfaceCard";
import { useResolveText } from "@/components/providers/LocaleProvider";

export function ConfigCard({
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
}: {
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
}) {
    const resolveText = useResolveText();

    const resolvedHeightClass =
        bodyHeight === "auto"
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
            : bodyScroll === "auto" || variant !== "summary"
            ? "overflow-y-auto pr-1"
            : "";

    return (
        <AdminSurfaceCard className={cn("min-h-0", allowOverflow ? "overflow-visible" : "", className)}>
            <CardHeader className="space-y-2">
                <CardTitle className="text-lg text-slate-900 dark:text-slate-100">
                    <AdminHoverTitle title={resolveText(title)} description={description ? resolveText(description) : undefined} />
                </CardTitle>
            </CardHeader>
            <CardContent className={cn("min-h-0 space-y-4", allowOverflow ? "overflow-visible" : "overflow-hidden")}>
                <div className={cn("min-h-0", resolvedHeightClass, resolvedScrollClass, contentClassName)}>
                    {children}
                </div>
                {footer}
            </CardContent>
        </AdminSurfaceCard>
    );
}
