"use client";

import { useT } from "@/components/providers/LocaleProvider";
import { LocalizedText } from "@/lib/locale";

export function EmptyState({
    title,
    description,
}: {
    title: LocalizedText | string;
    description: LocalizedText | string;
}) {
    const t = useT();

    return (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white/80 px-6 py-10 text-center shadow-sm">
            <div className="text-sm font-medium text-slate-900">{t(title)}</div>
            <div className="mt-2 text-sm leading-6 text-slate-500">{t(description)}</div>
        </div>
    );
}
