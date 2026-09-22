"use client";

import { Badge } from "@admin/components/ui/badge";
import { AdminHoverInfo } from "@admin/components/admin-shell/AdminHoverInfo";
import { useResolveText } from "@admin/components/providers/LocaleProvider";
import { cn } from "@admin/lib/utils";

export function AdminPageHeader({
    title,
    description,
    badges = [],
    actions,
    className,
}: {
    title: string;
    description?: string;
    badges?: string[];
    actions?: React.ReactNode;
    className?: string;
}) {
    const resolveText = useResolveText();

    return (
        <div className={cn("admin-page-header flex flex-wrap items-center justify-between gap-3", className)}>
            <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                    <h1 className="text-[24px] leading-8 font-semibold tracking-tight text-foreground">
                        <AdminHoverInfo content={description ? resolveText(description) : undefined} panelClassName="text-sm leading-6">
                            <span>{resolveText(title)}</span>
                        </AdminHoverInfo>
                    </h1>
                    {badges.map((badge, index) => (
                        <Badge key={`${badge}-${index}`} variant="outline" className="rounded-full border-border bg-card/80 text-muted-foreground">
                            {resolveText(badge)}
                        </Badge>
                    ))}
                </div>
            </div>
            {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
    );
}
