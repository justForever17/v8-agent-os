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
import { createTranslator, type TranslationKey } from "@/lib/locale";
import { cn } from "@/lib/utils";
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

function parseJsonField(label: string, value: string, locale: "zh-CN" | "en") {
    const raw = String(value || "").trim();
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error(createTranslator(locale)("app.admin.dashboard.automation.cron.page.jsonObjectError", { label }));
    }
    return parsed as Record<string, unknown>;
}

const WEEKDAY_OPTIONS: Array<{ value: string; label: TranslationKey }> = [
    { value: "1", label: "app.admin.dashboard.automation.cron.page.k0d8cba58" },
    { value: "2", label: "app.admin.dashboard.automation.cron.page.kd806530b" },
    { value: "3", label: "app.admin.dashboard.automation.cron.page.ke9e74b84" },
    { value: "4", label: "app.admin.dashboard.automation.cron.page.k6278c51a" },
    { value: "5", label: "app.admin.dashboard.automation.cron.page.k04f84132" },
    { value: "6", label: "app.admin.dashboard.automation.cron.page.k8d68250f" },
    { value: "0", label: "app.admin.dashboard.automation.cron.page.k49ab3e95" },
];

const SCHEDULE_MODE_OPTIONS: Array<{ key: ScheduleMode; title: string; description: string }> = [
    { key: "daily", title:"app.admin.dashboard.automation.cron.page.kac5b4596", description:"app.admin.dashboard.automation.cron.page.k5cc19d17" },
    { key: "weekdays", title:"app.admin.dashboard.automation.cron.page.k219c2e16", description:"app.admin.dashboard.automation.cron.page.kce6bf459" },
    { key: "weekly", title:"app.admin.dashboard.automation.cron.page.k09eb5821", description:"app.admin.dashboard.automation.cron.page.kf16717e0" },
    { key: "monthly", title:"app.admin.dashboard.automation.cron.page.kff901c39", description:"app.admin.dashboard.automation.cron.page.kbc363802" },
    { key: "every-hours", title:"app.admin.dashboard.automation.cron.page.k1729d794", description:"app.admin.dashboard.automation.cron.page.k403a2e40" },
    { key: "custom", title:"app.admin.dashboard.automation.cron.page.k96aba461", description:"app.admin.dashboard.automation.cron.page.k9d25db3f" },
];

const TRIGGER_KIND_OPTIONS = [
    { value: "nudge", label: "app.admin.dashboard.automation.wakeIngress.triggerKind.nudge" },
    { value: "wake", label: "app.admin.dashboard.automation.wakeIngress.triggerKind.wake" },
    { value: "recovery_wake", label: "app.admin.dashboard.automation.wakeIngress.triggerKind.recoveryWake" },
] as const;

const ATTACH_POLICY_OPTIONS = [
    { value: "new_session", label: "app.admin.dashboard.automation.wakeIngress.attachPolicy.newSession" },
    { value: "attach_session", label: "app.admin.dashboard.automation.wakeIngress.attachPolicy.attachSession" },
    { value: "attach_run", label: "app.admin.dashboard.automation.wakeIngress.attachPolicy.attachRun" },
    { value: "resume_run", label: "app.admin.dashboard.automation.wakeIngress.attachPolicy.resumeRun" },
] as const;

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
    const map: Record<ScheduleMode, TranslationKey> = {
        daily: "app.admin.dashboard.automation.cron.page.k93e62975",
        weekdays: "app.admin.dashboard.automation.cron.page.kecf22654",
        weekly: "app.admin.dashboard.automation.cron.page.k33d4a492",
        monthly: "app.admin.dashboard.automation.cron.page.k17d269ce",
        "every-hours": "app.admin.dashboard.automation.cron.page.k297d66b3",
        custom: "app.admin.dashboard.automation.cron.page.kf1007633",
    };
    return map[key];
}

function localizeScheduleModeDescription(key: ScheduleMode) {
    const map: Record<ScheduleMode, TranslationKey> = {
        daily: "app.admin.dashboard.automation.cron.page.kd5e45744",
        weekdays: "app.admin.dashboard.automation.cron.page.k8e4d0fe4",
        weekly: "app.admin.dashboard.automation.cron.page.k251ad1cd",
        monthly: "app.admin.dashboard.automation.cron.page.ka9b022a8",
        "every-hours": "app.admin.dashboard.automation.cron.page.k38462880",
        custom: "app.admin.dashboard.automation.cron.page.k43d4fe20",
    };
    return map[key];
}

function describeCronExpression(expression: string, locale: "zh-CN" | "en") {
    const t = createTranslator(locale);
    const schedule = parseCronExpression(expression);
    if (schedule.mode === "custom") {
        return t(localizeScheduleModeTitle("custom"));
    }
    if (schedule.mode === "every-hours") {
        const hours = clampCronNumber(schedule.intervalHours, 1, 23, 4);
        const minute = clampCronNumber(schedule.intervalMinute, 0, 59, 0);
        return minute === 0
            ? t("app.admin.dashboard.automation.cron.page.schedule.everyHours", { hours })
            : t("app.admin.dashboard.automation.cron.page.schedule.everyHoursAtMinute", {
                  hours,
                  minute: String(minute).padStart(2, "0"),
              });
    }
    if (schedule.mode === "daily") {
        return t("app.admin.dashboard.automation.cron.page.schedule.daily", {
            time: normalizeTimeInput(schedule.time),
        });
    }
    if (schedule.mode === "weekdays") {
        return t("app.admin.dashboard.automation.cron.page.schedule.weekdays", {
            time: normalizeTimeInput(schedule.time),
        });
    }
    if (schedule.mode === "weekly") {
        const weekdayLabel = WEEKDAY_OPTIONS.find((item) => item.value === normalizeWeekday(schedule.weekday))?.label;
        const weekday = weekdayLabel ? t(weekdayLabel) : t(localizeScheduleModeTitle("weekly"));
        return `${weekday} ${normalizeTimeInput(schedule.time)}`;
    }
    if (schedule.mode === "monthly") {
        return t("app.admin.dashboard.automation.cron.page.schedule.monthly", {
            day: clampCronNumber(schedule.dayOfMonth, 1, 31, 1),
            time: normalizeTimeInput(schedule.time),
        });
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
            const response = await fetch(locale.startsWith("en") ? "/CRON.en.md" : "/CRON.zh-CN.md");
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
            targetBinding = parseJsonField("targetBinding", targetBindingText, locale);
            recoveryAnchor = parseJsonField("recoveryAnchor", recoveryAnchorText, locale);
            sourceMetadata = parseJsonField("sourceMetadata", sourceMetadataText, locale);
        } catch {
            setScheduleError(t("app.admin.dashboard.automation.cron.page.kc59d91af"));
            return;
        }

        const nextCronExpression = buildCronExpression(scheduleDraft);
        if (!nextCronExpression) {
            setScheduleError(t("app.admin.dashboard.automation.cron.page.k401320bb"));
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
                title={"app.admin.dashboard.automation.cron.page.k8164146c"}
                description={"app.admin.dashboard.automation.cron.page.k4d4e7760"}
                actions={
                    <div className="flex items-center gap-3">
                        <InlineSaveState saving={saving} saved={saved} />
                        <Button variant="secondary" onClick={() => void loadDocumentation()}>
                            <BookOpen className="mr-2 h-4 w-4" />
                            {t("app.admin.dashboard.automation.cron.page.k2a0649a2")}
                        </Button>
                        <Button variant="outline" onClick={() => void loadData()} disabled={saving}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            {t("app.admin.dashboard.automation.cron.page.k286cb634")}
                        </Button>
                        <Button onClick={openNewDialog}>
                            <Plus className="mr-2 h-4 w-4" />
                            {t("app.admin.dashboard.automation.cron.page.ked33bd58")}
                        </Button>
                    </div>
                }
            />

            <DomainSummaryStrip
                items={[
                    { label: "app.admin.dashboard.automation.cron.page.k4aad49cf", value: jobs.length, description: "app.admin.dashboard.automation.cron.page.k940f50d5" },
                    { label: "app.admin.dashboard.automation.cron.page.kdb6c0cc1", value: enabledCount, description: "app.admin.dashboard.automation.cron.page.k0172371d" },
                    { label: "app.admin.dashboard.automation.cron.page.k32c6dff4", value: runByType.agent, description: "app.admin.dashboard.automation.cron.page.k95a43d4f" },
                    { label: "app.admin.dashboard.automation.cron.page.k0f0a35ef", value: runByType.command + runByType.python, description: "app.admin.dashboard.automation.cron.page.k5fdd739b" },
                ]}
            />

            {systemMemoryJob ? (
                <ConfigCard
                    title={"app.admin.dashboard.automation.cron.page.kd7925760"}
                    description={"app.admin.dashboard.automation.cron.page.k984a406e"}
                >
                    <div className="rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                            <div className="space-y-2">
                                <div className="flex flex-wrap items-center gap-2">
                                    <div className="text-sm font-medium text-slate-900">{systemMemoryJob.name}</div>
                                    <Badge variant="outline">{t("app.admin.dashboard.automation.cron.page.k06ce54e5")}</Badge>
                                    <Badge variant={systemMemoryJob.enabled ? "default" : "secondary"}>
                                        {systemMemoryJob.enabled ? t("app.admin.dashboard.automation.cron.page.kdb6c0cc1") : t("app.admin.dashboard.automation.cron.page.k6f76d7f7")}
                                    </Badge>
                                </div>
                                <div className="flex items-center gap-2 text-xs text-slate-700">
                                    <Clock3 className="h-3.5 w-3.5 text-amber-600" />
                                    <span>{describeCronExpression(systemMemoryJob.cron_expression, locale)}</span>
                                </div>
                                <div className="text-xs text-slate-500">{t("app.admin.dashboard.automation.cron.page.kbaf14523")}{systemMemoryJob.cron_expression}</div>
                                <div className="text-xs text-slate-500">
                                    {t("app.admin.dashboard.automation.cron.page.k99d6f83e")}
                                </div>
                            </div>
                            <div className="flex flex-wrap items-center gap-2">
                                <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2">
                                    <span className="text-xs text-slate-500">{t("app.admin.dashboard.automation.cron.page.k37f0aa42")}</span>
                                    <Switch
                                        checked={systemMemoryJob.enabled}
                                        onCheckedChange={(checked) =>
                                            void saveJobs(jobs.map((item) => (item.id === systemMemoryJob.id ? { ...item, enabled: checked } : item)))
                                        }
                                    />
                                </div>
                                <Button variant="outline" size="sm" onClick={() => void handleRunNow(systemMemoryJob.id)}>
                                    <Play className="mr-2 h-4 w-4" />
                                    {t("app.admin.dashboard.automation.cron.page.k95433853")}
                                </Button>
                                <Button variant="outline" size="sm" onClick={() => openEditDialog(systemMemoryJob)}>
                                    {t("app.admin.dashboard.automation.cron.page.kac76654b")}
                                </Button>
                            </div>
                        </div>
                    </div>
                </ConfigCard>
            ) : null}

            <ConfigCard title={"app.admin.dashboard.automation.cron.page.k6ba7f7f9"} description={"app.admin.dashboard.automation.cron.page.k635517f8"} variant="list" bodyHeight={420} bodyScroll="auto">
                <div className="space-y-3">
                    {userJobs.length === 0 ? (
                        <EmptyState title={"app.admin.dashboard.automation.cron.page.k6d0a6b60"} description={"app.admin.dashboard.automation.cron.page.k63b00f03"} />
                    ) : (
                        userJobs.map((job) => (
                            <div key={job.id} className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                                <div className="flex flex-wrap items-start justify-between gap-4">
                                    <div className="min-w-0 space-y-2">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <div className="text-sm font-medium text-slate-900">{job.name}</div>
                                            <Badge variant="outline">{job.action_type}</Badge>
                                            <Badge variant="outline">{job.triggerKind || "nudge"}</Badge>
                                            <Badge variant={job.enabled ? "default" : "secondary"}>{job.enabled ? t("app.admin.dashboard.automation.cron.page.kdb6c0cc1") : t("app.admin.dashboard.automation.cron.page.k6f76d7f7")}</Badge>
                                        </div>
                                        <div className="flex items-center gap-2 text-xs text-slate-700">
                                            <Clock3 className="h-3.5 w-3.5 text-sky-600" />
                                            <span>{describeCronExpression(job.cron_expression, locale)}</span>
                                        </div>
                                        <div className="text-xs text-slate-500">{t("app.admin.dashboard.automation.cron.page.kbaf14523")}{job.cron_expression}</div>
                                        <div className="break-all text-xs text-slate-500">{t("app.admin.dashboard.automation.cron.page.k09308472")}{job.action_target}</div>
                                        <div className="text-xs text-slate-500">
                                            {job.targetBinding || job.recoveryAnchor
                                                ? t("app.admin.dashboard.automation.cron.page.k8b8fc65a")
                                                : t("app.admin.dashboard.automation.cron.page.k4a2c0d63")}
                                        </div>
                                    </div>
                                    <div className="flex flex-wrap items-center gap-2">
                                        <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2">
                                            <span className="text-xs text-slate-500">{t("app.admin.dashboard.automation.cron.page.k37f0aa42")}</span>
                                            <Switch checked={job.enabled} onCheckedChange={(checked) => void saveJobs(jobs.map((item) => (item.id === job.id ? { ...item, enabled: checked } : item)))} />
                                        </div>
                                        <Button variant="outline" size="sm" onClick={() => void handleRunNow(job.id)}>
                                            <Play className="mr-2 h-4 w-4" />
                                            {t("app.admin.dashboard.automation.cron.page.k95433853")}
                                        </Button>
                                        <Button variant="outline" size="sm" onClick={() => openEditDialog(job)}>
                                            {t("app.admin.dashboard.automation.cron.page.k75997619")}
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
                        <DialogTitle>{editingSystemJob ? t("app.admin.dashboard.automation.cron.page.kaae8613d") : editingJobId ? t("app.admin.dashboard.automation.cron.page.kc3e9f297") : t("app.admin.dashboard.automation.cron.page.k366be36d")}</DialogTitle>
                        <DialogDescription>{editingSystemJob ? t("app.admin.dashboard.automation.cron.page.k7803dec3") : t("app.admin.dashboard.automation.cron.page.kcb323c39")}</DialogDescription>
                    </DialogHeader>

                    {!editingSystemJob ? (
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.ka1c166dc")}</Label>
                                <Input value={draftJob.name} onChange={(event) => setDraftJob((current) => ({ ...current, name: event.target.value }))} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.k89647d74")}</Label>
                                <Select value={draftJob.action_type} onValueChange={(value: CronJob["action_type"]) => setDraftJob((current) => ({ ...current, action_type: value }))}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="agent">{t("app.admin.dashboard.automation.cron.page.k7c6312bb")}</SelectItem>
                                        <SelectItem value="command">{t("app.admin.dashboard.automation.cron.page.k5924d6b5")}</SelectItem>
                                        <SelectItem value="python">{t("app.admin.dashboard.automation.cron.page.ke83fbd7a")}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2 md:col-span-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.kab44591a")}</Label>
                                <Input value={draftJob.action_target} onChange={(event) => setDraftJob((current) => ({ ...current, action_target: event.target.value }))} />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.k01313cef")}</Label>
                                <Select value={draftJob.triggerKind || "nudge"} onValueChange={(value) => setDraftJob((current) => ({ ...current, triggerKind: value as NonNullable<CronJob["triggerKind"]> }))}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {TRIGGER_KIND_OPTIONS.map((option) => (
                                            <SelectItem key={option.value} value={option.value}>{t(option.label)}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.ka1c2596c")}</Label>
                                <Select value={draftJob.attachPolicy || "new_session"} onValueChange={(value) => setDraftJob((current) => ({ ...current, attachPolicy: value as NonNullable<CronJob["attachPolicy"]> }))}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {ATTACH_POLICY_OPTIONS.map((option) => (
                                            <SelectItem key={option.value} value={option.value}>{t(option.label)}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2 md:col-span-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.k4a33217d")}</Label>
                                <Input value={draftJob.wakeReason || ""} onChange={(event) => setDraftJob((current) => ({ ...current, wakeReason: event.target.value }))} placeholder={t("app.admin.dashboard.automation.cron.page.ka6afa838")} />
                            </div>
                            <div className="space-y-2 md:col-span-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.kebf5a38b")}</Label>
                                <Textarea value={draftJob.message || ""} onChange={(event) => setDraftJob((current) => ({ ...current, message: event.target.value }))} className="min-h-[96px]" placeholder={t("app.admin.dashboard.automation.cron.page.k629dfe37")} />
                            </div>
                        </div>
                    ) : (
                        <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-sm text-slate-600">
                            {t("app.admin.dashboard.automation.cron.page.kc4bdc198")}
                        </div>
                    )}

                    <div className="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                        <div className="space-y-1">
                            <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.automation.cron.page.k630b85cb")}</div>
                            <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.automation.cron.page.k5b2a9659")}</div>
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
                                <Label>{t("app.admin.dashboard.automation.cron.page.k88be2169")}</Label>
                                <Input type="time" value={normalizeTimeInput(scheduleDraft.time)} onChange={(event) => updateSchedule({ time: event.target.value })} />
                            </div>
                        ) : null}

                        {scheduleDraft.mode === "weekly" ? (
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.automation.cron.page.kb8b3e2a4")}</Label>
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
                                    <Label>{t("app.admin.dashboard.automation.cron.page.k88be2169")}</Label>
                                    <Input type="time" value={normalizeTimeInput(scheduleDraft.time)} onChange={(event) => updateSchedule({ time: event.target.value })} />
                                </div>
                            </div>
                        ) : null}

                        {scheduleDraft.mode === "monthly" ? (
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.automation.cron.page.k2f689528")}</Label>
                                    <Input
                                        type="number"
                                        min={1}
                                        max={31}
                                        value={scheduleDraft.dayOfMonth}
                                        onChange={(event) => updateSchedule({ dayOfMonth: event.target.value })}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.automation.cron.page.k88be2169")}</Label>
                                    <Input type="time" value={normalizeTimeInput(scheduleDraft.time)} onChange={(event) => updateSchedule({ time: event.target.value })} />
                                </div>
                            </div>
                        ) : null}

                        {scheduleDraft.mode === "every-hours" ? (
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.automation.cron.page.k02b85aea")}</Label>
                                    <Input
                                        type="number"
                                        min={1}
                                        max={23}
                                        value={scheduleDraft.intervalHours}
                                        onChange={(event) => updateSchedule({ intervalHours: event.target.value })}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>{t("app.admin.dashboard.automation.cron.page.kc1cd9267")}</Label>
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
                                <Label>{t("app.admin.dashboard.automation.cron.page.k758ac620")}</Label>
                                <Input value={scheduleDraft.rawExpression} onChange={(event) => updateSchedule({ rawExpression: event.target.value })} placeholder={t("app.admin.dashboard.automation.cron.page.kb9fba662")} />
                            </div>
                        ) : (
                            <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
                                {t("app.admin.dashboard.automation.cron.page.k90cf7fa8")}<span className="font-medium text-slate-900">{describeCronExpression(buildCronExpression(scheduleDraft), locale)}</span>
                            </div>
                        )}
                    </div>

                    {!editingSystemJob ? (
                    <AdvancedSection title={"app.admin.dashboard.automation.cron.page.k5a31521e"} description={"app.admin.dashboard.automation.cron.page.kf89b0eca"}>
                        <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4">
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.k758ac620")}</Label>
                                <Input
                                    value={scheduleDraft.mode === "custom" ? scheduleDraft.rawExpression : buildCronExpression(scheduleDraft)}
                                    onChange={(event) =>
                                        setScheduleDraft((current) => ({
                                            ...current,
                                            mode: "custom",
                                            rawExpression: event.target.value,
                                        }))
                                    }
                                    placeholder={t("app.admin.dashboard.automation.cron.page.kb9fba662")}
                                />
                                <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.automation.cron.page.k1b9aa6dd")}</div>
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.k1f02616d")}</Label>
                                <Textarea value={payloadText} onChange={(event) => setPayloadText(event.target.value)} className="min-h-[140px] font-mono text-xs" />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.kb5a63537")}</Label>
                                <Textarea value={targetBindingText} onChange={(event) => setTargetBindingText(event.target.value)} className="min-h-[120px] font-mono text-xs" />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.k6b5e0d20")}</Label>
                                <Textarea value={recoveryAnchorText} onChange={(event) => setRecoveryAnchorText(event.target.value)} className="min-h-[120px] font-mono text-xs" />
                            </div>
                            <div className="space-y-2">
                                <Label>{t("app.admin.dashboard.automation.cron.page.k01d4c959")}</Label>
                                <Textarea value={sourceMetadataText} onChange={(event) => setSourceMetadataText(event.target.value)} className="min-h-[120px] font-mono text-xs" />
                            </div>
                        </div>
                    </AdvancedSection>
                    ) : null}

                    <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3">
                        <div className="flex items-center justify-between gap-4">
                            <div className="space-y-1">
                                <div className="text-sm font-medium text-slate-900">{t("app.admin.dashboard.automation.cron.page.k3936c4f6")}</div>
                                <div className="text-xs leading-5 text-slate-500">{t("app.admin.dashboard.automation.cron.page.k135979e8")}</div>
                            </div>
                            <Switch checked={draftJob.enabled} onCheckedChange={(checked) => setDraftJob((current) => ({ ...current, enabled: checked }))} />
                        </div>
                    </div>

                    {scheduleError ? <div className="text-sm text-rose-600">{scheduleError}</div> : null}

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialogOpen(false)}>
                            {t("app.admin.dashboard.automation.cron.page.kb92cb20c")}
                        </Button>
                        <Button onClick={() => void handleSaveDialog()}>{t("app.admin.dashboard.automation.cron.page.k6010e1ed")}</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <DocumentationGuideDialog
                open={guideOpen}
                onOpenChange={setGuideOpen}
                title={t("app.admin.dashboard.automation.cron.page.k14b81bfe")}
                description={t("app.admin.dashboard.automation.cron.page.k82bfd96f")}
                content={docContent}
            />
        </AdminPageShell>
    );
}
