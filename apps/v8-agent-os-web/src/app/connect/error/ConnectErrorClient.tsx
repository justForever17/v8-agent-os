"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { useT } from "@/components/providers/LocaleProvider";

export function ConnectErrorClient() {
    const t = useT();

    return (
        <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10 dark:bg-slate-950">
            <div className="w-full max-w-lg rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                    {t("web.generated.b1cf9fe253")}
                </h1>
                <p className="mt-3 text-sm leading-6 text-slate-600 dark:text-slate-400">
                    {t("web.generated.636f9343b0")}
                </p>
                <div className="mt-6 flex justify-center gap-3">
                    <Link href="/connect">
                        <Button>{t("web.generated.70101843c9")}</Button>
                    </Link>
                    <Link href="/">
                        <Button variant="outline">{t("web.generated.2452ed6d5c")}</Button>
                    </Link>
                </div>
            </div>
        </div>
    );
}
