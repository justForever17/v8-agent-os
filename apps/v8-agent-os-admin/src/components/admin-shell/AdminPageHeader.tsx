"use client";

import { Badge } from "@/components/ui/badge";
import { useResolveText } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";

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
        <div className={cn("flex flex-wrap items-start justify-between gap-4", className)}>
            <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                    <h1 className="text-3xl font-semibold tracking-tight text-slate-900">{resolveText(title)}</h1>
                    {badges.map((badge, index) => (
                        <Badge key={`${badge}-${index}`} variant="outline" className="rounded-full border-slate-200 bg-white/80 text-slate-600">
                            {resolveText(badge)}
                        </Badge>
                    ))}
                </div>
                {description ? <p className="max-w-3xl text-sm leading-6 text-slate-600">{resolveText(description)}</p> : null}
            </div>
            {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
    );
}
