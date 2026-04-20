"use client";

import { useResolveText } from "@/components/providers/LocaleProvider";

export function EmptyState({
    title,
    description,
}: {
    title: string;
    description: string;
}) {
    const resolveText = useResolveText();

    return (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white/80 px-6 py-10 text-center shadow-sm">
            <div className="text-sm font-medium text-slate-900">{resolveText(title)}</div>
            <div className="mt-2 text-sm leading-6 text-slate-500">{resolveText(description)}</div>
        </div>
    );
}
