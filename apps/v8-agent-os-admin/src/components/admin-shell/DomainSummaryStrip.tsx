"use client";

import { Card, CardContent } from "@/components/ui/card";
import { useT } from "@/components/providers/LocaleProvider";
import { LocalizedText } from "@/lib/locale";

export function DomainSummaryStrip({
    items,
}: {
    items: Array<{
        label: LocalizedText | string;
        value: React.ReactNode;
        description?: LocalizedText | string;
    }>;
}) {
    const t = useT();

    return (
        <div className="grid auto-rows-fr gap-4 md:grid-cols-2 xl:grid-cols-4">
            {items.map((item, index) => (
                <Card key={typeof item.label === "string" ? item.label : `${item.label.en}-${index}`} className="h-full min-w-0 overflow-hidden rounded-2xl border-slate-200 bg-white shadow-sm">
                    <CardContent className="flex h-full min-w-0 flex-col space-y-2 overflow-hidden p-5">
                        <div className="text-xs font-medium uppercase tracking-[0.24em] text-slate-500">{t(item.label)}</div>
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
