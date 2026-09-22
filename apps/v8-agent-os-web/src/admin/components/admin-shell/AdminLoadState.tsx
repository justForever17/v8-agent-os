"use client";
import { Loader2 } from "lucide-react";
import { AdminPageHeader } from "./AdminPageHeader";
import { AdminPageShell } from "./AdminPageShell";
import { Button } from "@admin/components/ui/button";
import { useT } from "@admin/components/providers/LocaleProvider";

export function AdminLoadState({ title, error, onRetry }: { title: string; error?: string; onRetry: () => void }) {
    const t = useT();
    return <AdminPageShell><AdminPageHeader title={title}/><div role={error ? "alert" : "status"} className="flex min-h-[140px] flex-wrap items-center justify-center gap-3 rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">
        {error ? <><span>{t("admin.experience.loadFailed")}</span><Button variant="outline" onClick={onRetry}>{t("admin.experience.retry")}</Button><details className="basis-full text-xs"><summary>{t("admin.experience.details")}</summary><p className="mt-2 break-words">{error}</p></details></> : <><Loader2 size={18} className="animate-spin"/>{t("admin.galaxy.loading")}</>}
    </div></AdminPageShell>;
}
