"use client";

import { AlertCircle, FolderTree } from "lucide-react";

import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { lt } from "@/lib/locale";

interface ExtractionRun {
    runId?: string;
    sessionId?: string;
    status?: string;
    startedAt?: string;
    finishedAt?: string;
    extractorModel?: string;
    extractionFailureStage?: string | null;
    extractionFailureReason?: string | null;
    skipReason?: string | null;
    extractionMode?: string | null;
    transcriptSource?: string | null;
    rawOutputPreview?: string | null;
    parserErrorPreview?: string | null;
    summary?: string | null;
    resolvedScope?: string | null;
    effectiveMemoryScope?: string | null;
    memoryPolicy?: string | null;
    noPersistedMemoryReason?: string | null;
    extractedPreferenceCount?: number;
    extractedKnowledgeCount?: number;
    persistedPreferenceCount?: number;
    persistedKnowledgeCount?: number;
    persistedRelationCount?: number;
    invocationError?: string | null;
}

interface MaintenanceRun {
    runId?: string;
    status?: string;
    startedAt?: string;
    finishedAt?: string;
    summaryMissingCountBefore?: number;
    summaryMissingCountAfter?: number;
    summaryBackfilledCount?: number;
    summaryStaleCountBefore?: number;
    summaryStaleCountAfter?: number;
    touchedRefs?: string[];
}

function formatRelativeTimestamp(value: string | null | undefined, locale: "zh-CN" | "en-US") {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString(locale, {
        hour12: false,
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function formatExtractionOutcome(run: ExtractionRun, t: ReturnType<typeof useT>) {
    if ((run.status || "").toLowerCase() === "skipped" || run.skipReason) {
        const labels: Record<string, string> = {
            duplicate_transcript: t(lt("重复 transcript，已跳过", "Duplicate transcript skipped")),
            duplicate_increment: t(lt("重复增量，已跳过", "Duplicate increment skipped")),
            no_semantic_content: t(lt("语义内容过短，已跳过", "Short semantic content skipped")),
            no_messages: t(lt("无可用消息，已跳过", "Skipped because no messages were available")),
            no_user_message: t(lt("缺少用户消息，已跳过", "Skipped because no user message was found")),
        };
        const skipKey = run.skipReason || run.extractionMode || "";
        return {
            title: labels[skipKey] || t(lt("已跳过", "Skipped")),
            tone: "bg-slate-500/10 text-slate-700 border-slate-500/20",
            detail: t(lt("这不是 durable policy 太严，而是本轮没有新的可抽取增量，或者内容本身不满足抽取前置条件。", "This is not a strict durable policy issue. It means there was no new extractable increment or the content did not satisfy extraction preconditions.")),
        };
    }
    if (run.extractionFailureStage) {
        const labels: Record<string, string> = {
            extractor_config_missing: t(lt("抽取器配置缺失", "Extractor config missing")),
            llm_response_empty: t(lt("模型空响应", "Empty model response")),
            parser_failed: t(lt("结构解析失败", "Structured parsing failed")),
            repair_parser_failed: t(lt("修复解析失败", "Repair parsing failed")),
            llm_invoke_failed: t(lt("模型调用失败", "Model invocation failed")),
        };
        return {
            title: labels[run.extractionFailureStage] || run.extractionFailureStage,
            tone: "bg-red-500/10 text-red-600 border-red-500/20",
            detail: run.extractionFailureReason || run.invocationError || t(lt("本轮抽取未成功完成。", "This extraction run did not complete successfully.")),
        };
    }
    if (run.noPersistedMemoryReason === "policy_filtered") {
        return {
            title: t(lt("已抽取，但被策略过滤", "Extracted but filtered by policy")),
            tone: "bg-amber-500/10 text-amber-700 border-amber-500/20",
            detail: t(lt("当前会话抽取到了候选项，但 durable policy 没有允许它们落库。", "This session produced candidates, but the durable policy did not allow them to be persisted.")),
        };
    }
    if ((run.persistedKnowledgeCount || 0) > 0 || (run.persistedPreferenceCount || 0) > 0) {
        return {
            title: t(lt("已持久化", "Persisted")),
            tone: "bg-emerald-500/10 text-emerald-700 border-emerald-500/20",
            detail: t(lt("本轮 memory extraction 已经成功写入 durable memory。", "This memory extraction run successfully wrote into durable memory.")),
        };
    }
    if ((run.extractedKnowledgeCount || 0) > 0 || (run.extractedPreferenceCount || 0) > 0) {
        return {
            title: t(lt("已抽取，等待进一步判断", "Extracted and awaiting further judgment")),
            tone: "bg-sky-500/10 text-sky-700 border-sky-500/20",
            detail: t(lt("当前已有候选项，但未形成可持久化写入。", "Candidates were extracted, but they have not become persistable durable writes yet.")),
        };
    }
    return {
        title: t(lt("无有效抽取", "No effective extraction")),
        tone: "bg-muted text-muted-foreground border-border/60",
        detail: t(lt("当前会话没有产生可供 durable memory 使用的结构化结果。", "This session did not produce structured output usable by durable memory.")),
    };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export default function MemoryRuntimeDiagnosticsPanel({ data }: { data: any }) {
    const { locale } = useLocale();
    const t = useT();
    const tr = (zh: string, en: string) => t(lt(zh, en));
    const uiLocale = locale === "en" ? "en-US" : "zh-CN";
    const extractionSummary = data?.extractions?.summary || {};
    const recentExtractions = (data?.extractions?.recent || []) as ExtractionRun[];
    const memoryMapHealth = data?.memoryMap || {};
    const maintenanceSummary = data?.maintenance?.summary || {};
    const recentMaintenanceRuns = (data?.maintenance?.recent || []) as MaintenanceRun[];

    return (
        <div className="space-y-6">
            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                        <AlertCircle className="h-5 w-5 text-primary" />
                        {tr("记忆抽取诊断", "Memory extraction diagnostics")}
                    </CardTitle>
                    <CardDescription>
                        {tr(
                            "明确区分抽取失败、模型空响应、解析失败和策略过滤，避免把所有问题都误判成阈值设置。",
                            "Separate extraction failures, empty model responses, parse failures, and policy filtering so threshold tuning is not blamed for every issue.",
                        )}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
                        {[
                            [tr("已完成", "Completed"), extractionSummary.completed || 0],
                            [tr("已跳过", "Skipped"), extractionSummary.skipped || 0],
                            [tr("已持久化", "Persisted"), extractionSummary.persisted || 0],
                            [tr("策略过滤", "Policy filtered"), extractionSummary.policyFiltered || 0],
                            [tr("模型空响应", "Empty model responses"), extractionSummary.llmResponseEmpty || 0],
                            [tr("解析失败", "Parse failures"), (extractionSummary.parserFailed || 0) + (extractionSummary.repairParserFailed || 0)],
                            [tr("模型调用失败", "Model invoke failures"), extractionSummary.llmInvokeFailed || 0],
                            [tr("重复 transcript", "Duplicate transcripts"), extractionSummary.duplicateTranscript || 0],
                            [tr("配置缺失", "Missing extractor config"), extractionSummary.extractorConfigMissing || 0],
                            [tr("短内容跳过", "Short semantic skips"), extractionSummary.noSemanticContent || 0],
                        ].map(([label, value]) => (
                            <div key={String(label)} className="rounded-xl border bg-muted/20 p-3">
                                <p className="text-xs text-muted-foreground">{label}</p>
                                <p className="mt-2 text-2xl font-semibold">{value}</p>
                            </div>
                        ))}
                    </div>

                    {recentExtractions.length > 0 ? (
                        <div className="max-h-[560px] space-y-3 overflow-y-auto pr-1">
                            {recentExtractions.map((run) => {
                                const outcome = formatExtractionOutcome(run, t);
                                return (
                                    <div key={run.runId || `${run.sessionId}-${run.startedAt}`} className="rounded-xl border bg-background p-4">
                                        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                                            <div className="min-w-0 flex-1 space-y-2">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${outcome.tone}`}>
                                                        {outcome.title}
                                                    </span>
                                                    <span className="font-mono text-xs text-muted-foreground">
                                                        {tr("会话", "Session")} {run.sessionId || "—"}
                                                    </span>
                                                    <span className="font-mono text-xs text-muted-foreground">
                                                        {tr("运行", "Run")} {run.runId || "—"}
                                                    </span>
                                                </div>
                                                <p className="text-sm text-foreground">{outcome.detail}</p>
                                                {run.summary ? (
                                                    <p className="rounded-lg bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
                                                        {run.summary}
                                                    </p>
                                                ) : null}
                                                <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                                                    <span>{tr("开始", "Started")}：{formatRelativeTimestamp(run.startedAt, uiLocale)}</span>
                                                    <span>{tr("完成", "Finished")}：{formatRelativeTimestamp(run.finishedAt, uiLocale)}</span>
                                                    <span>{tr("模型", "Model")}：{run.extractorModel || "—"}</span>
                                                    <span>{t("scope")}：{run.effectiveMemoryScope || run.resolvedScope || "—"}</span>
                                                    <span>{t("policy")}：{run.memoryPolicy || "—"}</span>
                                                    <span>{tr("模式", "Mode")}：{run.extractionMode || "—"}</span>
                                                    <span>{t("transcript")}：{run.transcriptSource || "—"}</span>
                                                </div>
                                                <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                                                    <span>{tr("抽取偏好", "Extracted preferences")}：{run.extractedPreferenceCount || 0}</span>
                                                    <span>{tr("抽取知识", "Extracted knowledge")}：{run.extractedKnowledgeCount || 0}</span>
                                                    <span>{tr("持久化偏好", "Persisted preferences")}：{run.persistedPreferenceCount || 0}</span>
                                                    <span>{tr("持久化知识", "Persisted knowledge")}：{run.persistedKnowledgeCount || 0}</span>
                                                    <span>{tr("图谱关系", "Graph relations")}：{run.persistedRelationCount || 0}</span>
                                                </div>
                                                {(run.rawOutputPreview || run.parserErrorPreview || run.invocationError) ? (
                                                    <div className="grid gap-2 xl:grid-cols-3">
                                                        {run.rawOutputPreview ? (
                                                            <div className="rounded-lg border bg-muted/20 p-3">
                                                                <p className="mb-2 text-xs font-medium text-muted-foreground">{tr("模型原始输出预览", "Raw model output preview")}</p>
                                                                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed">{run.rawOutputPreview}</pre>
                                                            </div>
                                                        ) : null}
                                                        {run.parserErrorPreview ? (
                                                            <div className="rounded-lg border bg-muted/20 p-3">
                                                                <p className="mb-2 text-xs font-medium text-muted-foreground">{tr("解析错误预览", "Parser error preview")}</p>
                                                                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed">{run.parserErrorPreview}</pre>
                                                            </div>
                                                        ) : null}
                                                        {run.invocationError ? (
                                                            <div className="rounded-lg border bg-muted/20 p-3">
                                                                <p className="mb-2 text-xs font-medium text-muted-foreground">{tr("模型调用错误", "Model invocation error")}</p>
                                                                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-relaxed">{run.invocationError}</pre>
                                                            </div>
                                                        ) : null}
                                                    </div>
                                                ) : null}
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    ) : (
                        <div className="rounded-xl border border-dashed bg-muted/20 p-6 text-sm text-muted-foreground">
                            {tr("近期还没有可展示的 memory extraction 运行样本。", "There are no recent memory extraction samples to show yet.")}
                        </div>
                    )}
                </CardContent>
            </Card>

            <Card className="border-border/60">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                        <FolderTree className="h-5 w-5 text-primary" />
                        {tr("记忆地图与维护状态", "Memory map and maintenance status")}
                    </CardTitle>
                    <CardDescription>
                        {tr(
                            "这里展示 brokered memory map 的摘要健康度，以及最近一次 Memory Maintenance 是否已经补齐缺失的周/月/年摘要。",
                            "This view shows brokered memory map health and whether the latest Memory Maintenance run has backfilled missing weekly, monthly, or yearly summaries.",
                        )}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
                        {[
                            [tr("年节点", "Year nodes"), memoryMapHealth?.counts?.year || 0],
                            [tr("月节点", "Month nodes"), memoryMapHealth?.counts?.month || 0],
                            [tr("周节点", "Week nodes"), memoryMapHealth?.counts?.week || 0],
                            [tr("天节点", "Day nodes"), memoryMapHealth?.counts?.day || 0],
                            [tr("缺摘要", "Missing summaries"), memoryMapHealth?.counts?.missing || 0],
                            [tr("摘要陈旧", "Stale summaries"), memoryMapHealth?.counts?.stale || 0],
                            [tr("已补齐", "Backfilled"), maintenanceSummary.summaryBackfilled || 0],
                        ].map(([label, value]) => (
                            <div key={String(label)} className="rounded-xl border bg-muted/20 p-3">
                                <p className="text-xs text-muted-foreground">{label}</p>
                                <p className="mt-2 text-2xl font-semibold">{value}</p>
                            </div>
                        ))}
                    </div>

                    <div className="grid gap-4 xl:grid-cols-2">
                        <div className="rounded-xl border bg-background p-4">
                            <div className="mb-3 text-sm font-medium">{tr("缺失的摘要节点", "Missing summary refs")}</div>
                            {(memoryMapHealth?.missingRefs || []).length > 0 ? (
                                <div className="max-h-[260px] space-y-2 overflow-y-auto pr-1">
                                    {(memoryMapHealth.missingRefs || []).map((ref: string) => (
                                        <div key={ref} className="rounded-lg bg-muted/30 px-3 py-2 font-mono text-xs text-muted-foreground">
                                            {ref}
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="text-sm text-muted-foreground">{tr("当前没有缺失的 week/month/year summary。", "There are no missing weekly, monthly, or yearly summaries right now.")}</div>
                            )}
                        </div>

                        <div className="rounded-xl border bg-background p-4">
                            <div className="mb-3 text-sm font-medium">{tr("陈旧的摘要节点", "Stale summary refs")}</div>
                            {(memoryMapHealth?.staleRefs || []).length > 0 ? (
                                <div className="max-h-[260px] space-y-2 overflow-y-auto pr-1">
                                    {(memoryMapHealth.staleRefs || []).map((ref: string) => (
                                        <div key={ref} className="rounded-lg bg-muted/30 px-3 py-2 font-mono text-xs text-muted-foreground">
                                            {ref}
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="text-sm text-muted-foreground">{tr("当前没有需要重刷的陈旧摘要。", "There are no stale summaries that need regeneration right now.")}</div>
                            )}
                        </div>
                    </div>

                    {recentMaintenanceRuns.length > 0 ? (
                        <div className="max-h-[420px] space-y-3 overflow-y-auto pr-1">
                            {recentMaintenanceRuns.map((run) => (
                                <div key={run.runId || `${run.startedAt}`} className="rounded-xl border bg-background p-4">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="rounded-full border border-border/60 px-2.5 py-1 text-xs font-medium">
                                            {run.status || tr("未知", "Unknown")}
                                        </span>
                                        <span className="font-mono text-xs text-muted-foreground">{tr("运行", "Run")} {run.runId || "—"}</span>
                                        <span className="text-xs text-muted-foreground">{tr("开始", "Started")}：{formatRelativeTimestamp(run.startedAt, uiLocale)}</span>
                                        <span className="text-xs text-muted-foreground">{tr("完成", "Finished")}：{formatRelativeTimestamp(run.finishedAt, uiLocale)}</span>
                                    </div>
                                    <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground">
                                        <span>{tr("缺摘要前", "Missing before")}：{run.summaryMissingCountBefore || 0}</span>
                                        <span>{tr("缺摘要后", "Missing after")}：{run.summaryMissingCountAfter || 0}</span>
                                        <span>{tr("陈旧前", "Stale before")}：{run.summaryStaleCountBefore || 0}</span>
                                        <span>{tr("陈旧后", "Stale after")}：{run.summaryStaleCountAfter || 0}</span>
                                        <span>{tr("补齐数量", "Backfilled count")}：{run.summaryBackfilledCount || 0}</span>
                                    </div>
                                    {(run.touchedRefs || []).length > 0 ? (
                                        <div className="mt-3 space-y-2">
                                            {(run.touchedRefs || []).map((ref) => (
                                                <div key={ref} className="rounded-lg bg-muted/30 px-3 py-2 font-mono text-xs text-muted-foreground">
                                                    {ref}
                                                </div>
                                            ))}
                                        </div>
                                    ) : null}
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="text-sm text-muted-foreground">{tr("还没有 Memory Maintenance 运行记录。", "There are no Memory Maintenance runs yet.")}</div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
