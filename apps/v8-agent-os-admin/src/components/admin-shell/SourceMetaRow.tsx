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
        <AdminSurfaceCard className="bg-muted/45 p-4">
            <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="border-border bg-card text-muted-foreground">
                    {t("components.admin.shell.SourceMetaRow.k155ede7a")}
                </Badge>
                <span className="text-sm text-foreground">{source}</span>
                <Badge variant={reloadRequired ? "secondary" : "outline"} className="ml-auto">
                    {reloadRequired ? t("components.admin.shell.SourceMetaRow.kc33ab52a") : t("components.admin.shell.SourceMetaRow.k0df8cfdd")}
                </Badge>
            </div>
            <div className="mt-3 space-y-1">
                {paths.map((path) => (
                    <div key={path} className="font-mono text-xs text-muted-foreground">
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
