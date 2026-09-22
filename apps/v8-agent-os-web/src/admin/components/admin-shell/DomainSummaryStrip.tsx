"use client";

import { AdminHoverInfo } from "@admin/components/admin-shell/AdminHoverInfo";
import { useResolveText } from "@admin/components/providers/LocaleProvider";

export function DomainSummaryStrip({
    items,
}: {
    items: Array<{
        label: string;
        value: React.ReactNode;
        description?: string;
    }>;
}) {
    const resolveText = useResolveText();

    return (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-border pb-3">
            {items.map((item, index) => (
                <div key={`${item.label}-${index}`} className="flex min-w-0 items-center gap-2">
                        <div className="text-xs text-muted-foreground">
                            <AdminHoverInfo content={item.description ? resolveText(item.description) : undefined} panelClassName="normal-case tracking-normal">
                                <span>{resolveText(item.label)}</span>
                            </AdminHoverInfo>
                        </div>
                        <div
                            className="min-w-0 break-words text-sm font-medium tabular-nums text-foreground"
                            title={typeof item.value === "string" ? item.value : undefined}
                        >
                            {item.value}
                        </div>
                </div>
            ))}
        </div>
    );
}
