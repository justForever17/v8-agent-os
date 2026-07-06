"use client";

import { AdminSurfaceCard } from "@/components/admin-shell/AdminSurfaceCard";
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
        <AdminSurfaceCard className="border-dashed bg-white/80 px-6 py-10 text-center dark:bg-card">
            <div className="text-sm font-medium text-slate-900 dark:text-slate-100">{resolveText(title)}</div>
            <div className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">{resolveText(description)}</div>
        </AdminSurfaceCard>
    );
}
