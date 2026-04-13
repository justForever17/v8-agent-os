"use client";

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
        <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
            <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="border-slate-200 bg-white text-slate-600">
                    {t("配置来源")}
                </Badge>
                <span className="text-sm text-slate-700">{source}</span>
                <Badge variant={reloadRequired ? "secondary" : "outline"} className="ml-auto">
                    {reloadRequired ? t("保存后建议重载") : t("保存后立即生效")}
                </Badge>
            </div>
            <div className="mt-3 space-y-1">
                {paths.map((path) => (
                    <div key={path} className="font-mono text-xs text-slate-500">
                        {path}
                    </div>
                ))}
            </div>
            {warnings.length > 0 ? (
                <div className="mt-3 space-y-1 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2">
                    {warnings.map((warning) => (
                        <div key={warning} className="text-xs text-amber-700">
                            {warning}
                        </div>
                    ))}
                </div>
            ) : null}
        </div>
    );
}
