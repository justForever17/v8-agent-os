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
        <AdminSurfaceCard className="border-dashed bg-card/80 px-6 py-10 text-center">
            <div className="text-sm font-medium text-foreground">{resolveText(title)}</div>
            <div className="mt-2 text-sm leading-6 text-muted-foreground">{resolveText(description)}</div>
        </AdminSurfaceCard>
    );
}
