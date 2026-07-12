"use client";

import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";

type TechnicalReferenceItem = {
    label: string;
    value?: string | null;
};

export function TechnicalReferenceDetails({
    items,
    className,
}: {
    items: TechnicalReferenceItem[];
    className?: string;
}) {
    const t = useT();
    const visibleItems = items.filter((item) => String(item.value || "").trim());
    if (visibleItems.length === 0) return null;

    return (
        <details className={cn("group rounded-xl border border-border/50 bg-muted/15 px-3 py-2", className)}>
            <summary className="cursor-pointer select-none text-xs font-medium text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary/50">
                {t("components.common.technicalDetails")}
            </summary>
            <dl className="mt-2 space-y-2 border-t border-border/40 pt-2 text-xs">
                {visibleItems.map((item) => (
                    <div key={`${item.label}:${item.value}`} className="grid gap-1 sm:grid-cols-[7rem_minmax(0,1fr)]">
                        <dt className="text-muted-foreground">{item.label}</dt>
                        <dd className="break-all font-mono text-foreground/80">{item.value}</dd>
                    </div>
                ))}
            </dl>
        </details>
    );
}
