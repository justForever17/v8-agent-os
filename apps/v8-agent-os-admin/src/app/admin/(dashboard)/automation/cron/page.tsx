"use client";

import { useEffect, useMemo, useState } from "react";
import { BookOpen, Clock3, Loader2, Play, Plus, RefreshCw, Trash2 } from "lucide-react";

import { AdminPageHeader } from "@/components/admin-shell/AdminPageHeader";
import { AdminPageShell } from "@/components/admin-shell/AdminPageShell";
import { AdvancedSection } from "@/components/admin-shell/AdvancedSection";
import { ConfigCard } from "@/components/admin-shell/ConfigCard";
import { DocumentationGuideDialog } from "@/components/admin-shell/DocumentationGuideDialog";
import { DomainSummaryStrip } from "@/components/admin-shell/DomainSummaryStrip";
import { EmptyState } from "@/components/admin-shell/EmptyState";
import { InlineSaveState } from "@/components/admin-shell/InlineSaveState";
import { SourceMetaRow } from "@/components/admin-shell/SourceMetaRow";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";
import { lt } from "@/lib/locale";
import { fetchConfigDomain, saveConfigDomain, type ConfigRegistryEnvelope } from "@/lib/config-registry";

type CronJob = {
    id: string;
    name: string;
    cron_expression: string;
    action_type: "command" | "python" | "agent";
    action_target: string;
    payload?: Record<string, unknown>;
    enabled: boolean;
    triggerKind?: "nudge" | "wake" | "recovery_wake";
    targetBinding?: Record<string, unknown>;
    recoveryAnchor?: Record<string, unknown>;
    attachPolicy?: "new_session" | "attach_session" | "attach_run" | "resume_run";
    wakeReason?: string;
    message?: string;
    sourceMetadata?: Record<string, unknown>;
};

type CronData = {
    jobs: CronJob[];
};

type ScheduleMode = "daily" | "weekdays" | "weekly" | "monthly" | "every-hours" | "custom";

type ScheduleDraft = {
    mode: ScheduleMode;
    rawExpression: string;
    time: string;
    weekday: string;
    dayOfMonth: string;
    intervalHours: string;
    intervalMinute: string;
};

const SYSTEM_MEMORY_MAINTENANCE_JOB_ID = "system-memory-maintenance";

const EMPTY_JOB: CronJob = {
    id: "",
    name: "",
    cron_expression: "0 9 * * *",
    action_type: "agent",
    action_target: "",
    payload: {},
    enabled: true,
    triggerKind: "nudge",
    attachPolicy: "new_session",
};

function formatJsonField(value: Record<string, unknown> | undefined) {
    return value && Object.keys(value).length ? JSON.stringify(value, null, 2) : "{}";
}

function parseJsonField(label: string, value: string) {
    const raw = String(value || "").trim();
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error(`${label} 需要是合法 JSON 对象`);
    }
    return parsed as Record<string, unknown>;
}

const WEEKDAY_OPTIONS = [
    { value: "1", label: lt("周一", "Mon") },
    { value: "2", label: lt("周二", "Tue") },
    { value: "3", label: lt("周三", "Wed") },
    { value: "4", label: lt("周四", "Thu") },
    { value: "5", label: lt("周五", "Fri") },
    { value: "6", label: lt("周六", "Sat") },
    { value: "0", label: lt("周日", "Sun") },
];

const SCHEDULE_MODE_OPTIONS: Array<{ key: ScheduleMode; title: string; description: string }> = [
    { key: "daily", title: "每天", description: "每天固定时间执行。" },
    { key: "weekdays", title: "工作日", description: "周一到周五固定时间执行。" },
    { key: "weekly", title: "每周", description: "每周指定星期和时间执行。" },
    { key: "monthly", title: "每月", description: "每月指定日期和时间执行。" },
    { key: "every-hours", title: "每隔几小时", description: "按固定小时间隔循环执行。" },
    { key: "custom", title: "自定义", description: "手动填写原始 Cron 表达式。" },
];

function normalizeWeekday(value: string) {
    return value === "7" ? "0" : value;
}

function clampCronNumber(value: string, min: number, max: number, fallback: number) {
    const parsed = Number.parseInt(String(value || "").trim(), 10);
    if (Number.isNaN(parsed)) return fallback;
    return Math.min(max, Math.max(min, parsed));
}

function normalizeTimeInput(value: string | undefined, fallback = "09:00") {
    const raw = String(value || "").trim();
    if (!raw) return fallback;
    const match = raw.match(/^(\d{1,2}):(\d{1,2})$/);
    if (!match) return fallback;
    const hour = clampCronNumber(match[1], 0, 23, 9);
    const minute = clampCronNumber(match[2], 0, 59, 0);
    return `${hour.toString().padStart(2, "0")}:${minute.toString().padStart(2, "0")}`;
}

function timeToCronParts(time: string) {
    const normalized = normalizeTimeInput(time);
    const [hour, minute] = normalized.split(":");
    return {
        minute: clampCronNumber(minute, 0, 59, 0),
        hour: clampCronNumber(hour, 0, 23, 9),
    };
}

function parseCronExpression(expression: string): ScheduleDraft {
    const rawExpression = String(expression || "").trim() || EMPTY_JOB.cron_expression;
    const daily = rawExpression.match(/^(\d{1,2}) (\d{1,2}) \* \* \*$/);
    if (daily) {
        return {
            mode: "daily",
            rawExpression,
            time: `${daily[2].padStart(2, "0")}:${daily[1].padStart(2, "0")}`,
            weekday: "1",
            dayOfMonth: "1",
            intervalHours: "4",
            intervalMinute: "0",
        };
    }

    const weekdays = rawExpression.match(/^(\d{1,2}) (\d{1,2}) \* \* 1-5$/);
    if (weekdays) {
        return {
            mode: "weekdays",
            rawExpression,
            time: `${weekdays[2].padStart(2, "0")}:${weekdays[1].padStart(2, "0")}`,
            weekday: "1",
            dayOfMonth: "1",
            intervalHours: "4",
            intervalMinute: "0",
        };
    }

    const weekly = rawExpression.match(/^(\d{1,2}) (\d{1,2}) \* \* ([0-7])$/);
    if (weekly) {
        return {
            mode: "weekly",
            rawExpression,
            time: `${weekly[2].padStart(2, "0")}:${weekly[1].padStart(2, "0")}`,
            weekday: normalizeWeekday(weekly[3]),
            dayOfMonth: "1",
            intervalHours: "4",
            intervalMinute: "0",
        };
    }

    const monthly = rawExpression.match(/^(\d{1,2}) (\d{1,2}) (\d{1,2}) \* \*$/);
    if (monthly) {
        return {
            mode: "monthly",
            rawExpression,
            time: `${monthly[2].padStart(2, "0")}:${monthly[1].padStart(2, "0")}`,
            weekday: "1",
            dayOfMonth: String(clampCronNumber(monthly[3], 1, 31, 1)),
            intervalHours: "4",
            intervalMinute: "0",
        };
    }

    const everyHours = rawExpression.match(/^(\d{1,2}) (\*\/|0\/)(\d{1,2}) \* \* \*$/);
    if (everyHours) {
        return {
            mode: "every-hours",
            rawExpression,
            time: "09:00",
            weekday: "1",
            dayOfMonth: "1",
            intervalHours: String(clampCronNumber(everyHours[3], 1, 23, 4)),
            intervalMinute: String(clampCronNumber(everyHours[1], 0, 59, 0)),
        };
    }

    return {
        mode: "custom",
        rawExpression,
        time: "09:00",
        weekday: "1",
        dayOfMonth: "1",
        intervalHours: "4",
        intervalMinute: "0",
    };
}

function buildCronExpression(schedule: ScheduleDraft) {
    if (schedule.mode === "custom") {
        return String(schedule.rawExpression || "").trim() || EMPTY_JOB.cron_expression;
    }

    if (schedule.mode === "every-hours") {
        const intervalHours = clampCronNumber(schedule.intervalHours, 1, 23, 4);
        const minute = clampCronNumber(schedule.intervalMinute, 0, 59, 0);
        return `${minute} */${intervalHours} * * *`;
    }

    const { minute, hour } = timeToCronParts(schedule.time);
    if (schedule.mode === "daily") {
        return `${minute} ${hour} * * *`;
    }
    if (schedule.mode === "weekdays") {
        return `${minute} ${hour} * * 1-5`;
    }
    if (schedule.mode === "weekly") {
        const weekday = normalizeWeekday(schedule.weekday || "1");
        return `${minute} ${hour} * * ${weekday}`;
    }
    if (schedule.mode === "monthly") {
        const dayOfMonth = clampCronNumber(schedule.dayOfMonth, 1, 31, 1);
        return `${minute} ${hour} ${dayOfMonth} * *`;
    }
    return EMPTY_JOB.cron_expression;
}

function localizeScheduleModeTitle(key: ScheduleMode) {
    const map: Record<ScheduleMode, ReturnType<typeof lt>> = {
        daily: lt("每天", "Daily"),
        weekdays: lt("工作日", "Weekdays"),
        weekly: lt("每周", "Weekly"),
        monthly: lt("每月", "Monthly"),
        "every-hours": lt("每隔几小时", "Every few hours"),
        custom: lt("自定义", "Custom"),
    };
    return map[key];
}

function localizeScheduleModeDescription(key: ScheduleMode) {
    const map: Record<ScheduleMode, ReturnType<typeof lt>> = {
        daily: lt("每天固定时间执行。", "Run at the same time every day."),
        weekdays: lt("周一到周五固定时间执行。", "Run on weekdays at a fixed time."),
        weekly: lt("每周指定星期和时间执行。", "Run weekly on a chosen day and time."),
        monthly: lt("每月指定日期和时间执行。", "Run monthly on a chosen day and time."),
        "every-hours": lt("按固定小时间隔循环执行。", "Run on a fixed hourly interval."),
        custom: lt("手动填写原始 Cron 表达式。", "Enter a raw cron expression manually."),
    };
    return map[key];
}

function describeCronExpression(expression: string, locale: "zh-CN" | "en") {
    const schedule = parseCronExpression(expression);
    if (schedule.mode === "custom") {
        return locale === "en" ? "Custom schedule" : "自定义计划";
    }
    if (schedule.mode === "every-hours") {
        const hours = clampCronNumber(schedule.intervalHours, 1, 23, 4);
        const minute = clampCronNumber(schedule.intervalMinute, 0, 59, 0);
        return locale === "en"
            ? minute === 0 ? `Every ${hours}h` : `Every ${hours}h at :${String(minute).padStart(2, "0")}`
            : minute === 0 ? `每隔 ${hours} 小时` : `每隔 ${hours} 小时的第 ${minute} 分`;
    }
    if (schedule.mode === "daily") {
        return locale === "en" ? `Daily ${normalizeTimeInput(schedule.time)}` : `每天 ${normalizeTimeInput(schedule.time)}`;
    }
    if (schedule.mode === "weekdays") {
        return locale === "en" ? `Weekdays ${normalizeTimeInput(schedule.time)}` : `工作日 ${normalizeTimeInput(schedule.time)}`;
    }
    if (schedule.mode === "weekly") {
        const weekday = WEEKDAY_OPTIONS.find((item) => item.value === normalizeWeekday(schedule.weekday))?.label[locale] || (locale === "en" ? "Weekly" : "每周");
        return `${weekday} ${normalizeTimeInput(schedule.time)}`;
    }
    if (schedule.mode === "monthly") {
        return locale === "en"
            ? `Monthly ${clampCronNumber(schedule.dayOfMonth, 1, 31, 1)} @ ${normalizeTimeInput(schedule.time)}`
            : `每月 ${clampCronNumber(schedule.dayOfMonth, 1, 31, 1)} 日 ${normalizeTimeInput(schedule.time)}`;
    }
    return expression;
}

function buildDefaultScheduleDraft() {
    return parseCronExpression(EMPTY_JOB.cron_expression);
}

export default function ScheduledTasksPage() {
    const t = useT();
    const { locale } = useLocale();
    const [envelope, setEnvelope] = useState<ConfigRegistryEnvelope<CronData> | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [guideOpen, setGuideOpen] = useState(false);
    const [editingJobId, setEditingJobId] = useState<string | null>(null);
    const [draftJob, setDraftJob] = useState<CronJob>(EMPTY_JOB);
    const [payloadText, setPayloadText] = useState("{}");
    const [docContent, setDocContent] = useState("");
    const [scheduleDraft, setScheduleDraft] = useState<ScheduleDraft>(buildDefaultScheduleDraft());
    const [scheduleError, setScheduleError] = useState("");
    const [targetBindingText, setTargetBindingText] = useState("{}");
    const [recoveryAnchorText, setRecoveryAnchorText] = useState("{}");
    const [sourceMetadataText, setSourceMetadataText] = useState("{}");

    const loadDocumentation = async () => {
        try {
            const response = await fetch(locale === "en" ? "/CRON.en.md" : "/CRON.zh-CN.md");
            const fallback = await fetch("/CRON.zh-CN.md");
            const text = response.ok ? await response.text() : await fallback.text();
            setDocContent(text);
            setGuideOpen(true);
        } catch (error) {
            console.error("Failed to load cron documentation:", error);
        }
    };

    const loadData = async () => {
        setLoading(true);
        try {
            const next = await fetchConfigDomain<CronData>("cron");
            setEnvelope(next);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadData();
    }, []);

    const jobs = useMemo(() => envelope?.data.jobs ?? [], [envelope?.data.jobs]);
    const systemMemoryJob = useMemo(() => jobs.find((job) => job.id === SYSTEM_MEMORY_MAINTENANCE_JOB_ID) ?? null, [jobs]);
    const userJobs = useMemo(() => jobs.filter((job) => job.id !== SYSTEM_MEMORY_MAINTENANCE_JOB_ID), [jobs]);
    const enabledCount = useMemo(() => jobs.filter((job) => job.enabled).length, [jobs]);
    const runByType = useMemo(
        () => ({
            agent: jobs.filter((job) => job.action_type === "agent").length,
            command: jobs.filter((job) => job.action_type === "command").length,
            python: jobs.filter((job) => job.action_type === "python").length,
        }),
        [jobs],
    );

    const saveJobs = async (nextJobs: CronJob[]) => {
        if (!envelope) return;
        setSaving(true);
        try {
            const next = await saveConfigDomain<CronData>("cron", {
                data: {
                    ...envelope.data,
                    jobs: nextJobs,
                },
            });
            setEnvelope(next);
            setSaved(true);
            window.setTimeout(() => setSaved(false), 1800);
        } finally {
            setSaving(false);
        }
    };

    const openNewDialog = () => {
        setEditingJobId(null);
        setDraftJob({
            ...EMPTY_JOB,
            id: crypto.randomUUID(),
        });
        setPayloadText("{}");
        setScheduleDraft(buildDefaultScheduleDraft());
        setScheduleError("");
        setTargetBindingText("{}");
        setRecoveryAnchorText("{}");
        setSourceMetadataText("{}");
        setDialogOpen(true);
    };

    const openEditDialog = (job: CronJob) => {
        setEditingJobId(job.id);
        setDraftJob(job);
        setPayloadText(JSON.stringify(job.payload || {}, null, 2));
        setScheduleDraft(parseCronExpression(job.cron_expression));
        setScheduleError("");
        setTargetBindingText(formatJsonField(job.targetBinding));
        setRecoveryAnchorText(formatJsonField(job.recoveryAnchor));
        setSourceMetadataText(formatJsonField(job.sourceMetadata));
        setDialogOpen(true);
    };

    const editingSystemJob = draftJob.id === SYSTEM_MEMORY_MAINTENANCE_JOB_ID;

    const updateSchedule = (patch: Partial<ScheduleDraft>) => {
        setScheduleDraft((current) => {
            const next = { ...current, ...patch };
            if (next.mode !== "custom") {
                next.rawExpression = buildCronExpression(next);
            }
            return next;
        });
        setScheduleError("");
    };

    const handleSaveDialog = async () => {
        let payload: Record<string, unknown> = {};
        let targetBinding: Record<string, unknown> = {};
        let recoveryAnchor: Record<string, unknown> = {};
        let sourceMetadata: Record<string, unknown> = {};
        try {
            payload = JSON.parse(payloadText || "{}");
            targetBinding = parseJsonField("targetBinding", targetBindingText);
            recoveryAnchor = parseJsonField("recoveryAnchor", recoveryAnchorText);
            sourceMetadata = parseJsonField("sourceMetadata", sourceMetadataText);
        } catch {
            setScheduleError(t(lt("附加参数与 Wake ingress 字段都需要是合法的 JSON 对象。", "Payload and wake ingress fields must be valid JSON objects.")));
            return;
        }

        const nextCronExpression = buildCronExpression(scheduleDraft);
        if (!nextCronExpression) {
            setScheduleError(t(lt("请先填写有效的执行计划。", "Enter a valid schedule first.")));
            return;
        }

        const nextJob = {
            ...draftJob,
            cron_expression: nextCronExpression,
            payload,
            triggerKind: draftJob.triggerKind || "nudge",
            attachPolicy: draftJob.attachPolicy || "new_session",
            targetBinding,
            recoveryAnchor,
            sourceMetadata,
        };
        const nextJobs = editingJobId ? jobs.map((job) => (job.id === editingJobId ? nextJob : job)) : [...jobs, nextJob];
        await saveJobs(nextJobs);
        setDialogOpen(false);
    };

    const handleRunNow = async (jobId: string) => {
        await fetch("/api/cron/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_id: jobId }),
        });
    };

    if (loading || !envelope) {
        return (
            <div className="flex min-h-[320px] items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            </div>
        );
    }

    return (
        <AdminPageShell>
            <AdminPageHeader
                title={lt("定时任务", "Cron")}
                description={lt("管理定时计划、执行目标和启用状态。", "Manage recurring schedules, targets, and enablement state.")}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button variant="secondary" onClick={() => void loadDocumentation()}>
                            <BookOpen className="mr-2 h-4 w-4" />
                            {t(lt("配置教学", "Guide"))}
                        </Button>
                        <Button variant="outline" onClick={() => void loadData()} disabled={saving}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            {t(lt("重新读取", "Reload"))}
                        </Button>
                        <Button onClick={openNewDialog}>
                            <Plus className="mr-2 h-4 w-4" />
                            {t(lt("新建任务", "New task"))}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip
                items={[
                    { label: lt("任务总数", "Tasks"), value: jobs.length, description: lt("当前已保存的定时任务数量。", "Saved cron jobs.") },
                    { label: lt("已启用", "Enabled"), value: enabledCount, description: lt("会按计划自动执行的任务数量。", "Jobs that will run automatically.") },
                    { label: lt("AutomationRuntime 任务", "Automation runs"), value: runByType.agent, description: lt("会进入 AutomationRuntime 执行链的任务数。", "Jobs that execute through AutomationRuntime.") },
                    { label: lt("脚本任务", "Script jobs"), value: runByType.command + runByType.python, description: lt("命令或脚本执行任务数。", "Command and Python jobs.") },
                ]}
            />

            {systemMemoryJob ? (
                <ConfigCard
                    title={lt("内建维护任务", "Built-in maintenance")}
                    description={lt("Memory Maintenance 是正式内建能力，负责日记整理、周/月/年摘要补齐与 durable memory 维护。", "Memory Maintenance is a built-in capability for log compaction, summary backfill, and durable memory maintenance.")}
                >
                    <div className="rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                            <div className="space-y-2">
                                <div className="flex flex-wrap items-center gap-2">
                                    <div className="text-sm font-medium text-slate-900">{systemMemoryJob.name}</div>
                                    <Badge variant="outline">{t(lt("系统内建", "System"))}</Badge>
                                    <Badge variant={systemMemoryJob.enabled ? "default" : "secondary"}>
                                        {systemMemoryJob.enabled ? t(lt("已启用", "Enabled")) : t(lt("已停用", "Paused"))}
                                    </Badge>
                                </div>
                                <div className="flex items-center gap-2 text-xs text-slate-700">
                                    <Clock3 className="h-3.5 w-3.5 text-amber-600" />
                                    <span>{describeCronExpression(systemMemoryJob.cron_expression, locale)}</span>
                                </div>
                                <div className="text-xs text-slate-500">{t(lt("原始计划：", "Cron:"))}{systemMemoryJob.cron_expression}</div>
                                <div className="text-xs text-slate-500">
                                    {t(lt("这张卡不可删除，也不可修改目标/类型，只允许改时间和启停。", "This card cannot be deleted and only exposes enablement and schedule controls."))}
                                </div>
                            </div>
                            <div className="flex flex-wrap items-center gap-2">
                                <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2">
                                    <span className="text-xs text-slate-500">{t(lt("启用", "Enabled"))}</span>
                                    <Switch
                                        checked={systemMemoryJob.enabled}
                                        onCheckedChange={(checked) =>
                                            void saveJobs(jobs.map((item) => (item.id === systemMemoryJob.id ? { ...item, enabled: checked } : item)))
                                        }
                                    />
                                </div>
                                <Button variant="outline" size="sm" onClick={() => void handleRunNow(systemMemoryJob.id)}>
                                    <Play className="mr-2 h-4 w-4" />
                                    {t(lt("立即运行", "Run now"))}
                                </Button>
                                <Button variant="outline" size="sm" onClick={() => openEditDialog(systemMemoryJob)}>
                                    {t(lt("调整时间", "Adjust schedule"))}
                                </Button>
                            </div>
                        </div>
                    </div>
                </ConfigCard>
            ) : null}

            <ConfigCard title={lt("任务列表", "Task list")} description={lt("查看用户自定义任务的计划和启用状态。", "Review user-defined schedules and enablement state.")} variant="list" bodyHeight={420} bodyScroll="auto">
                <div className="space-y-3">
                    {userJobs.length === 0 ? (
                        <EmptyState title={lt("还没有定时任务", "No cron jobs yet")} description={lt("如果你需要自动执行固定任务，可以从这里开始新建。", "Create your first recurring task here.")} />
                    ) : (
                        userJobs.map((job) => (
                            <div key={job.id} className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                                <div className="flex flex-wrap items-start justify-between gap-4">
                                    <div className="min-w-0 space-y-2">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <div className="text-sm font-medium text-slate-900">{job.name}</div>
                                            <Badge variant="outline">{job.action_type}</Badge>
                                            <Badge variant="outline">{job.triggerKind || "nudge"}</Badge>
                                            <Badge variant={job.enabled ? "default" : "secondary"}>{job.enabled ? t(lt("已启用", "Enabled")) : t(lt("已停用", "Paused"))}</Badge>
                                        </div>
                                        <div className="flex items-center gap-2 text-xs text-slate-700">
                                            <Clock3 className="h-3.5 w-3.5 text-sky-600" />
                                            <span>{describeCronExpression(job.cron_expression, locale)}</span>
                                        </div>
                                        <div className="text-xs text-slate-500">{t(lt("原始计划：", "Cron:"))}{job.cron_expression}</div>
                                        <div className="break-all text-xs text-slate-500">{t(lt("执行目标：", "Target:"))}{job.action_target}</div>
                                        <div className="text-xs text-slate-500">
                                            {job.targetBinding || job.recoveryAnchor
                                                ? t(lt("已配置显式 targetBinding / recoveryAnchor。", "Explicit targetBinding / recoveryAnchor configured."))
                                                : t(lt("未提供 binding，运行时会自动降级为 nudge。", "No binding provided; runtime will degrade this trigger to nudge."))}
                                        </div>
                                    </div>
                                    <div className="flex flex-wrap items-center gap-2">
                                        <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2">
                                            <span className="text-xs text-slate-500">{t(lt("启用", "Enabled"))}</span>
                                            <Switch checked={job.enabled} onCheckedChange={(checked) => void saveJobs(jobs.map((item) => (item.id === job.id ? { ...item, enabled: checked } : item)))} />
                                        </div>
                                        <Button variant="outline" size="sm" onClick={() => void handleRunNow(job.id)}>
                                            <Play className="mr-2 h-4 w-4" />
                                            {t(lt("立即运行", "Run now"))}
                                        </Button>
                                        <Button variant="outline" size="sm" onClick={() => openEditDialog(job)}>
                                            {t(lt("编辑", "Edit"))}
                                        </Button>
                                        <Button variant="ghost" size="icon" className="text-slate-500 hover:text-rose-600" onClick={() => void saveJobs(jobs.filter((item) => item.id !== job.id))}>
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </ConfigCard>

            <SourceMetaRow source={envelope.source} savePath={envelope.savePath} reloadRequired={envelope.reloadRequired} />

            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
                    <DialogHeader>
                        <DialogTitle>{editingSystemJob ? t(lt("调整 Memory Maintenance 时间", "Adjust Memory Maintenance")) : editingJobId ? t(lt("编辑定时任务", "Edit cron job")) : t(lt("新建定时任务", "New cron job"))}</DialogTitle>
                        <DialogDescription>{editingSystemJob ? t(lt("这是系统内建维护任务。这里只允许调整计划时间和启用状态。", "This is a built-in system task. Only schedule and enablement can be changed here.")) : t(lt("先选择你能看懂的执行计划，再设置执行方式和目标。只有自定义模式才需要直接填写原始 Cron 表达式。", "Pick a readable schedule first, then set the target and action. Only Custom mode needs a raw cron expression."))}</DialogDescription>
                    </DialogHeader>

                    {!editingSystemJob ? (
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t(lt("任务名称", "Task name"))}</Label>
                                <Input value={draftJob.name} onChange={(event) => setDraftJob((current) => ({ ...current, name: event.target.value }))} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("执行方式", "Action type"))}</Label>
                                <Select value={draftJob.action_type} onValueChange={(value: CronJob["action_type"]) => setDraftJob((current) => ({ ...current, action_type: value }))}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="agent">{t(lt("AutomationRuntime 任务", "Automation task"))}</SelectItem>
                                        <SelectItem value="command">{t(lt("系统命令", "Command"))}</SelectItem>
                                        <SelectItem value="python">{t(lt("Python 脚本", "Python"))}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2 md:col-span-2">
                                <Label>{t(lt("执行目标", "Target"))}</Label>
                                <Input value={draftJob.action_target} onChange={(event) => setDraftJob((current) => ({ ...current, action_target: event.target.value }))} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("Wake 类型", "Wake trigger kind"))}</Label>
                                <Select value={draftJob.triggerKind || "nudge"} onValueChange={(value) => setDraftJob((current) => ({ ...current, triggerKind: value as NonNullable<CronJob["triggerKind"]> }))}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="nudge">nudge</SelectItem>
                                        <SelectItem value="wake">wake</SelectItem>
                                        <SelectItem value="recovery_wake">recovery_wake</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("附着策略", "Attach policy"))}</Label>
                                <Select value={draftJob.attachPolicy || "new_session"} onValueChange={(value) => setDraftJob((current) => ({ ...current, attachPolicy: value as NonNullable<CronJob["attachPolicy"]> }))}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="new_session">new_session</SelectItem>
                                        <SelectItem value="attach_session">attach_session</SelectItem>
                                        <SelectItem value="attach_run">attach_run</SelectItem>
                                        <SelectItem value="resume_run">resume_run</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2 md:col-span-2">
                                <Label>{t(lt("唤醒原因", "Wake reason"))}</Label>
                                <Input value={draftJob.wakeReason || ""} onChange={(event) => setDraftJob((current) => ({ ...current, wakeReason: event.target.value }))} placeholder={t(lt("例如：scheduled_project_checkin", "e.g. scheduled_project_checkin"))} />
                            </div>
                            <div className="space-y-2 md:col-span-2">
                                <Label>{t(lt("消息模板", "Message"))}</Label>
                                <Textarea value={draftJob.message || ""} onChange={(event) => setDraftJob((current) => ({ ...current, message: event.target.value }))} className="min-h-[96px]" placeholder={t(lt("没有 binding / anchor 时，这段文本只会作为 nudge。", "Without binding / anchor this text only becomes a nudge."))} />
                            </div>
                        </div>
                    ) : (
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-sm text-slate-600">
                            {t(lt("Memory Maintenance 的执行目标、类型和参数由系统固定维护。这里仅开放计划时间与启停。", "The built-in Memory Maintenance target, type, and payload are system-managed. Only schedule and enablement are editable here."))}
                        </div>
                    )}

                    <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                        <div className="space-y-1">
                            <div className="text-sm font-medium text-slate-900">{t(lt("执行计划", "Schedule"))}</div>
                            <div className="text-xs leading-5 text-slate-500">{t(lt("选择最接近你需求的方式，系统会自动生成对应的 Cron 表达式。", "Choose the schedule style that matches your intent. The cron expression is generated automatically."))}</div>
                        </div>

                        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                            {SCHEDULE_MODE_OPTIONS.map((option) => (
                                <button
                                    key={option.key}
                                    type="button"
                                    className={cn(
                                        "rounded-2xl border px-4 py-4 text-left shadow-sm transition-colors",
                                        scheduleDraft.mode === option.key
                                            ? "border-sky-200 bg-sky-50 text-sky-900"
                                            : "border-slate-200 bg-white text-slate-700 hover:border-slate-300",
                                    )}
                                    onClick={() => updateSchedule({ mode: option.key, rawExpression: option.key === "custom" ? scheduleDraft.rawExpression : scheduleDraft.rawExpression })}
                                >
                                    <div className="text-sm font-semibold">{t(localizeScheduleModeTitle(option.key))}</div>
                                    <div className="mt-2 text-xs leading-5 text-slate-500">{t(localizeScheduleModeDescription(option.key))}</div>
                                </button>
                            ))}
                        </div>

                        {scheduleDraft.mode === "daily" || scheduleDraft.mode === "weekdays" ? (
                            <div className="space-y-2">
                                <Label>{t(lt("执行时间", "Time"))}</Label>
                                <Input type="time" value={normalizeTimeInput(scheduleDraft.time)} onChange={(event) => updateSchedule({ time: event.target.value })} />
                            </div>
                        ) : null}

                        {scheduleDraft.mode === "weekly" ? (
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t(lt("星期", "Weekday"))}</Label>
                                    <Select value={normalizeWeekday(scheduleDraft.weekday)} onValueChange={(value) => updateSchedule({ weekday: value })}>
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {WEEKDAY_OPTIONS.map((option) => (
                                                <SelectItem key={option.value} value={option.value}>
                                                    {t(option.label)}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label>{t(lt("执行时间", "Time"))}</Label>
                                    <Input type="time" value={normalizeTimeInput(scheduleDraft.time)} onChange={(event) => updateSchedule({ time: event.target.value })} />
                                </div>
                            </div>
                        ) : null}

                        {scheduleDraft.mode === "monthly" ? (
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t(lt("每月日期", "Day of month"))}</Label>
                                    <Input
                                        type="number"
                                        min={1}
                                        max={31}
                                        value={scheduleDraft.dayOfMonth}
                                        onChange={(event) => updateSchedule({ dayOfMonth: event.target.value })}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{t(lt("执行时间", "Time"))}</Label>
                                    <Input type="time" value={normalizeTimeInput(scheduleDraft.time)} onChange={(event) => updateSchedule({ time: event.target.value })} />
                                </div>
                            </div>
                        ) : null}

                        {scheduleDraft.mode === "every-hours" ? (
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t(lt("间隔小时数", "Hour interval"))}</Label>
                                    <Input
                                        type="number"
                                        min={1}
                                        max={23}
                                        value={scheduleDraft.intervalHours}
                                        onChange={(event) => updateSchedule({ intervalHours: event.target.value })}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{t(lt("执行分钟", "Minute offset"))}</Label>
                                    <Input
                                        type="number"
                                        min={0}
                                        max={59}
                                        value={scheduleDraft.intervalMinute}
                                        onChange={(event) => updateSchedule({ intervalMinute: event.target.value })}
                                    />
                                </div>
                            </div>
                        ) : null}

                        {scheduleDraft.mode === "custom" ? (
                            <div className="space-y-2">
                                <Label>{t(lt("原始 Cron 表达式", "Raw cron expression"))}</Label>
                                <Input value={scheduleDraft.rawExpression} onChange={(event) => updateSchedule({ rawExpression: event.target.value })} placeholder={t(lt("例如：0 9 * * *", "e.g. 0 9 * * *"))} />
                            </div>
                        ) : (
                            <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
                                {t(lt("当前计划：", "Current schedule:"))}<span className="font-medium text-slate-900">{describeCronExpression(buildCronExpression(scheduleDraft), locale)}</span>
                            </div>
                        )}
                    </div>

                    {!editingSystemJob ? (
                    <AdvancedSection title={lt("更多选项", "More options")} description={lt("需要手动检查或调整原始 Cron 表达式时，再展开这里。", "Expand only when you need to inspect or override the raw cron expression.")}>
                        <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4">
                            <div className="space-y-2">
                                <Label>{t(lt("原始 Cron 表达式", "Raw cron expression"))}</Label>
                                <Input
                                    value={scheduleDraft.mode === "custom" ? scheduleDraft.rawExpression : buildCronExpression(scheduleDraft)}
                                    onChange={(event) =>
                                        setScheduleDraft((current) => ({
                                            ...current,
                                            mode: "custom",
                                            rawExpression: event.target.value,
                                        }))
                                    }
                                    placeholder={t(lt("例如：0 9 * * *", "e.g. 0 9 * * *"))}
                                />
                                <div className="text-xs leading-5 text-slate-500">{t(lt("如果你在这里直接修改，系统会自动切换到“自定义”模式。", "Direct edits here will switch the schedule to Custom mode."))}</div>
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("附加参数（JSON）", "Payload (JSON)"))}</Label>
                                <Textarea value={payloadText} onChange={(event) => setPayloadText(event.target.value)} className="min-h-[140px] font-mono text-xs" />
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("targetBinding（JSON）", "targetBinding (JSON)"))}</Label>
                                <Textarea value={targetBindingText} onChange={(event) => setTargetBindingText(event.target.value)} className="min-h-[120px] font-mono text-xs" />
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("recoveryAnchor（JSON）", "recoveryAnchor (JSON)"))}</Label>
                                <Textarea value={recoveryAnchorText} onChange={(event) => setRecoveryAnchorText(event.target.value)} className="min-h-[120px] font-mono text-xs" />
                            </div>
                            <div className="space-y-2">
                                <Label>{t(lt("sourceMetadata（JSON）", "sourceMetadata (JSON)"))}</Label>
                                <Textarea value={sourceMetadataText} onChange={(event) => setSourceMetadataText(event.target.value)} className="min-h-[120px] font-mono text-xs" />
                            </div>
                        </div>
                    </AdvancedSection>
                    ) : null}

                    <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3">
                        <div className="flex items-center justify-between gap-4">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t(lt("启用状态", "Enablement"))}</div>
                                <div className="text-xs leading-5 text-slate-500">{t(lt("打开后，这个任务会按计划自动触发。", "When enabled, this task runs on schedule."))}</div>
                            </div>
                            <Switch checked={draftJob.enabled} onCheckedChange={(checked) => setDraftJob((current) => ({ ...current, enabled: checked }))} />
                        </div>
                    </div>

                    {scheduleError ? <div className="text-sm text-rose-600">{scheduleError}</div> : null}

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialogOpen(false)}>
                            {t(lt("取消", "Cancel"))}
                        </Button>
                        <Button onClick={() => void handleSaveDialog()}>{t(lt("保存", "Save"))}</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <DocumentationGuideDialog
                open={guideOpen}
                onOpenChange={setGuideOpen}
                title={t(lt("定时任务使用说明", "Cron guide"))}
                description={t(lt("这里说明如何设置计划、目标和立即运行方式。", "This guide explains scheduling, targets, and manual runs."))}
                content={docContent}
            />
        </AdminPageShell>
    );
}
