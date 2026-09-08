type Translate = (key: string, params?: Record<string, string | number>) => string;

export type ModelCacheUsageData = {
    readTokens?: number | null;
    writeTokens?: number | null;
    readRate?: number | null;
};

export type ProviderCacheWindow = {
    invocations?: number;
    cachedInputTokens?: number | null;
    cacheWriteInputTokens?: number | null;
    cacheReadReportedInvocations?: number;
    cacheWriteReportedInvocations?: number;
    cacheReadUnknownInvocations?: number;
    cachedInputTokenRate?: number | null;
};

function count(value: number | null | undefined, unavailable: string) {
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value.toLocaleString() : unavailable;
}

export function ModelCacheUsage({ usage, t }: { usage?: ModelCacheUsageData; t: Translate }) {
    const unavailable = t("app.admin.dashboard.page.cacheUsage.unreported");
    return (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" data-model-cache-usage>
            <span>{t("app.admin.dashboard.page.cacheUsage.read", { tokens: count(usage?.readTokens, unavailable) })}</span>
            <span>{t("app.admin.dashboard.page.cacheUsage.write", { tokens: count(usage?.writeTokens, unavailable) })}</span>
        </div>
    );
}

export function ModelCacheWindowSummary({ usage, t }: { usage?: ProviderCacheWindow; t: Translate }) {
    if (!usage) return null;
    const unavailable = t("app.admin.dashboard.page.cacheUsage.unreported");
    const rate = usage.cachedInputTokenRate;
    return (
        <div className="rounded-xl border border-border/70 bg-muted/20 px-3 py-2 text-xs text-muted-foreground" data-model-cache-window>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
                <span>{t("app.admin.dashboard.page.cacheUsage.read", { tokens: count(usage.cachedInputTokens, unavailable) })}</span>
                <span>{t("app.admin.dashboard.page.cacheUsage.write", { tokens: count(usage.cacheWriteInputTokens, unavailable) })}</span>
            </div>
            <div className="mt-1">{t("app.admin.dashboard.page.cacheUsage.coverage", {
                reported: usage.cacheReadReportedInvocations ?? 0,
                writesReported: usage.cacheWriteReportedInvocations ?? 0,
                total: usage.invocations ?? 0,
                rate: typeof rate === "number" && Number.isFinite(rate) ? `${(rate * 100).toFixed(1)}%` : unavailable,
            })}</div>
        </div>
    );
}
