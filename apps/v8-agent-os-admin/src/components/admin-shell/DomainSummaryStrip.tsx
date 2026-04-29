"use client";

import { Card, CardContent } from "@/components/ui/card";
import { AdminHoverInfo } from "@/components/admin-shell/AdminHoverInfo";
import { useResolveText } from "@/components/providers/LocaleProvider";

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
        <div className="grid auto-rows-fr gap-4 md:grid-cols-2 xl:grid-cols-4">
            {items.map((item, index) => (
                <Card key={`${item.label}-${index}`} className="h-full min-w-0 overflow-visible rounded-2xl border-slate-200 bg-white shadow-sm">
                    <CardContent className="flex h-full min-w-0 flex-col space-y-2 overflow-visible p-5">
                        <div className="text-xs font-medium uppercase tracking-[0.24em] text-slate-500">
                            <AdminHoverInfo content={item.description ? resolveText(item.description) : undefined} panelClassName="normal-case tracking-normal">
                                <span>{resolveText(item.label)}</span>
                            </AdminHoverInfo>
                        </div>
                        <div
                            className="min-h-[3.5rem] min-w-0 break-all text-lg font-semibold leading-tight text-slate-900 line-clamp-2 sm:text-xl"
                            title={typeof item.value === "string" ? item.value : undefined}
                        >
                            {item.value}
                        </div>
                    </CardContent>
                </Card>
            ))}
        </div>
    );
}
