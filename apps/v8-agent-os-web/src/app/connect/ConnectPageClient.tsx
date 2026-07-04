"use client";

import { useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { PlugZap } from "lucide-react";

import { AdminConnectionManager } from "@/components/connection/AdminConnectionManager";
import { useT } from "@/components/providers/LocaleProvider";

export function ConnectPageClient() {
    const t = useT();
    const searchParams = useSearchParams();
    const nextPath = useMemo(() => searchParams.get("next") || "/chat", [searchParams]);

    return (
        <div className="flex min-h-0 w-full flex-1 items-center justify-center overflow-y-auto bg-[radial-gradient(circle_at_top,_rgba(124,58,237,0.10),_transparent_42%),linear-gradient(180deg,_#f8fafc_0%,_#f1f5f9_100%)] px-4 py-6 [scrollbar-gutter:stable_both-edges] dark:bg-[radial-gradient(circle_at_top,_rgba(124,58,237,0.16),_transparent_38%),linear-gradient(180deg,_#020617_0%,_#0f172a_100%)] sm:px-6 sm:py-10">
            <div className="mx-auto w-full max-w-lg rounded-[2rem] border border-slate-200/80 bg-white/95 p-5 shadow-[0_24px_80px_-36px_rgba(15,23,42,0.35)] backdrop-blur-sm dark:border-slate-800/80 dark:bg-slate-950/92 sm:p-7">
                <div className="flex items-start gap-4">
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                        <PlugZap className="h-6 w-6" />
                    </div>
                    <div className="space-y-1.5">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500 dark:text-slate-400">
                            V8 OS
                        </div>
                        <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                            {t("web.generated.32941c6421")}
                        </h1>
                        <p className="text-sm leading-6 text-slate-600 dark:text-slate-400">
                            {t("web.generated.68c1aba53e")}
                        </p>
                    </div>
                </div>
                <div className="mt-5">
                    <AdminConnectionManager nextPath={nextPath} variant="page" autoRestore />
                </div>
            </div>
        </div>
    );
}
