"use client";

import { AdminSurfaceCard } from "@/components/admin-shell/AdminSurfaceCard";
import { Badge } from "@/components/ui/badge";
import { useT } from "@/components/providers/LocaleProvider";

function normalizePaths(value: string | string[]) {
    return Array.isArray(value) ? value : [value];
}

export function SourceMetaRow({
    source,
    savePath,
    reloadRequired,
    warnings = [],
}: {
    source: string;
    savePath: string | string[];
    reloadRequired: boolean;
    warnings?: string[];
}) {
    const paths = normalizePaths(savePath);
    const t = useT();

    return (
        <AdminSurfaceCard className="bg-slate-50/80 p-4 dark:bg-card">
            <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="border-slate-200 bg-white text-slate-600 dark:border-white/10 dark:bg-white/[0.04] dark:text-slate-300">
                    {t("components.admin.shell.SourceMetaRow.k155ede7a")}
                </Badge>
                <span className="text-sm text-slate-700 dark:text-slate-300">{source}</span>
                <Badge variant={reloadRequired ? "secondary" : "outline"} className="ml-auto">
                    {reloadRequired ? t("components.admin.shell.SourceMetaRow.kc33ab52a") : t("components.admin.shell.SourceMetaRow.k0df8cfdd")}
                </Badge>
            </div>
            <div className="mt-3 space-y-1">
                {paths.map((path) => (
                    <div key={path} className="font-mono text-xs text-slate-500 dark:text-slate-400">
                        {path}
                    </div>
                ))}
            </div>
            {warnings.length > 0 ? (
                <div className="mt-3 space-y-1 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-500/30 dark:bg-amber-500/10">
                    {warnings.map((warning) => (
                        <div key={warning} className="text-xs text-amber-700 dark:text-amber-200">
                            {warning}
                        </div>
                    ))}
                </div>
            ) : null}
        </AdminSurfaceCard>
    );
}
