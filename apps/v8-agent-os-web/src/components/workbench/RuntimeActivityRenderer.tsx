"use client";

import { useMemo } from "react";
import type { LucideIcon } from "lucide-react";
import {
    Activity,
    Blocks,
    Camera,
    CheckCircle2,
    Code2,
    Database,
    FileSearch,
    Keyboard,
    MousePointerClick,
    Search,
    Sparkles,
    Workflow,
    XCircle,
} from "lucide-react";

import { useLocale, useT } from "@/components/providers/LocaleProvider";
import {
    formatRelativeRuntimeTime,
    getRuntimeDescriptor,
    type RuntimeStageActivity,
    type RuntimeStageModel,
} from "@/lib/runtime-stage";
import type { RuntimeActivityWorkbenchDocument } from "@/lib/workbench";
import { cn } from "@/lib/utils";

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function text(value: unknown) {
    return typeof value === "string" ? value.trim() : "";
}

function activityDetails(activity: RuntimeStageActivity) {
    const data = activity.node.kind === "execution" ? recordOf(activity.node.data) : {};
    const progress = recordOf(data.progress);
    const timelineNode = recordOf(progress.timelineNode || progress.timeline_node);
    const topic = text(timelineNode.topic) || text(activity.topic);
    const toolName = text(timelineNode.toolName || timelineNode.tool_name)
        || (activity.node.kind === "execution" ? text(activity.node.toolName) : "");
    const content = text(timelineNode.content);
    let status = text(progress.status || data.status || data.state).toLowerCase();
    const normalizedTopic = topic.toLowerCase();
    if (!status && /(?:failed|cancelled|degraded|blocked)$/.test(normalizedTopic)) status = "failed";
    if (!status && /(?:completed|finished|ready)$/.test(normalizedTopic)) status = "completed";
    if (!status && /(?:started|queued|active|progress)/.test(normalizedTopic)) status = "active";
    return { topic, toolName, content, status };
}

const RUNTIME_ICONS: Record<string, LucideIcon> = {
    research: Search,
    network_supervisor: Search,
    computer_use: MousePointerClick,
    engineering: Code2,
    creative_media: Sparkles,
    rpa: Workflow,
    automation: Workflow,
    memory: Database,
    extensions: Blocks,
};

function eventIcon(activity: RuntimeStageActivity): { icon: LucideIcon; motion: "spin" | "pulse" | null } {
    const { topic, toolName, status } = activityDetails(activity);
    const signal = `${topic} ${toolName} ${activity.summary}`.toLowerCase();
    if (["failed", "error", "cancelled", "degraded", "blocked"].includes(status)) return { icon: XCircle, motion: null };
    if (/search|query|检索|搜索/.test(signal)) return { icon: Search, motion: "spin" };
    if (/click|tap|coordinate|坐标|点击/.test(signal)) return { icon: MousePointerClick, motion: "pulse" };
    if (/input|type|keyboard|输入|键盘/.test(signal)) return { icon: Keyboard, motion: "pulse" };
    if (/screenshot|capture|observe|scan|截图|观察/.test(signal)) return { icon: Camera, motion: "pulse" };
    if (/read|fetch|open|网页|来源/.test(signal)) return { icon: FileSearch, motion: "pulse" };
    if (["completed", "success", "ready"].includes(status)) return { icon: CheckCircle2, motion: null };
    return { icon: RUNTIME_ICONS[activity.runtimeId] || Activity, motion: "pulse" };
}

function toolLabel(toolName: string, locale: "zh-CN" | "en") {
    const labels: Record<string, [string, string]> = {
        web_search: ["搜索网页", "Search web"],
        web_read: ["读取网页", "Read page"],
        research_broker: ["调研编排", "Research orchestration"],
        research_architect: ["整理与复核", "Synthesis and review"],
        observe_desktop: ["观察桌面", "Observe desktop"],
        browser_open: ["打开网页", "Open page"],
        browser_click: ["点击网页控件", "Click page control"],
        browser_input: ["网页输入", "Enter text on page"],
        desktop_click: ["桌面点击", "Desktop click"],
        desktop_input: ["桌面输入", "Desktop input"],
    };
    const pair = labels[toolName];
    return pair ? pair[locale === "en" ? 1 : 0] : "";
}

function statusLabel(status: string, t: ReturnType<typeof useT>) {
    if (["failed", "error", "cancelled", "degraded", "blocked"].includes(status)) return t("web.workbench.runtimeActivity.failed");
    if (["completed", "success", "ready"].includes(status)) return t("web.workbench.runtimeActivity.completed");
    return t("web.workbench.runtimeActivity.running");
}

export function RuntimeActivityRenderer({
    document,
    runtimeModel,
}: {
    document: RuntimeActivityWorkbenchDocument;
    runtimeModel: RuntimeStageModel;
}) {
    const t = useT();
    const { locale } = useLocale();
    const runtimeId = document.subjectRef.runtimeId;
    const descriptor = getRuntimeDescriptor(runtimeId, locale);
    const card = runtimeModel.items.find((item) => item.id === runtimeId);
    const activities = useMemo(() => {
        const byIdentity = new Map<string, RuntimeStageActivity>();
        for (const activity of runtimeModel.messageActivities) {
            if (activity.runtimeId !== runtimeId) continue;
            const key = activity.eventSeq
                ? `seq:${activity.eventSeq}`
                : `${activity.id}:${activity.timestamp}`;
            const previous = byIdentity.get(key);
            if (!previous || activity.timestamp >= previous.timestamp) byIdentity.set(key, activity);
        }
        return [...byIdentity.values()]
            .sort((left, right) => {
                if (left.eventSeq && right.eventSeq && left.eventSeq !== right.eventSeq) return left.eventSeq - right.eventSeq;
                return left.timestamp - right.timestamp;
            })
            .slice(-400);
    }, [runtimeId, runtimeModel.messageActivities]);
    const HeaderIcon = RUNTIME_ICONS[runtimeId] || Activity;
    const latestActivityStatus = activities.length
        ? activityDetails(activities[activities.length - 1]).status
        : "";
    const runtimeActive = card?.status === "active" || [
        "active",
        "running",
        "queued",
        "starting",
        "streaming",
        "processing",
        "in_progress",
    ].includes(latestActivityStatus);

    return (
        <div data-runtime-activity-detail={runtimeId} className="h-full min-h-0 overflow-auto bg-background">
            <div className="mx-auto w-full max-w-[760px]">
                <header className="sticky top-0 z-10 border-b border-border/60 bg-background/95 px-4 py-3 backdrop-blur">
                    <div className="flex items-center gap-3">
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-primary/20 bg-primary/10 text-primary">
                            <HeaderIcon className={cn("h-4 w-4", runtimeActive && "animate-pulse")} />
                        </span>
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                                <h2 className="truncate text-sm font-semibold text-foreground">{descriptor.label}</h2>
                                <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                                    <span className={cn("h-1.5 w-1.5 rounded-full", runtimeActive ? "bg-emerald-500 animate-pulse" : card?.status === "attention" ? "bg-amber-500" : "bg-muted-foreground/40")} />
                                    {runtimeActive ? t("web.workbench.runtimeActivity.running") : t("web.workbench.runtimeActivity.history")}
                                </span>
                            </div>
                            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{card?.lastActivity || descriptor.description}</p>
                        </div>
                        <span className="text-[10px] tabular-nums text-muted-foreground">{t("web.workbench.runtimeActivity.eventCount", { count: activities.length })}</span>
                    </div>
                </header>

                {activities.length ? (
                    <ol className="relative px-4 py-3" aria-label={t("web.workbench.runtimeActivity.timeline")}>
                        <span className="absolute bottom-5 left-[29px] top-5 w-px bg-border/70" aria-hidden="true" />
                        {activities.map((activity, index) => {
                            const details = activityDetails(activity);
                            const meta = eventIcon(activity);
                            const Icon = meta.icon;
                            const isLatestActive = runtimeActive && index === activities.length - 1 && !["completed", "success", "ready", "failed", "error", "cancelled", "degraded", "blocked"].includes(details.status);
                            const label = toolLabel(details.toolName, locale);
                            return (
                                <li
                                    key={`${activity.id}:${activity.eventSeq || activity.timestamp}:${index}`}
                                    data-runtime-activity-seq={activity.eventSeq || undefined}
                                    data-runtime-activity-topic={details.topic || undefined}
                                    className="relative flex min-h-14 gap-3 py-2"
                                >
                                    <span className={cn(
                                        "relative z-[1] flex h-7 w-7 shrink-0 items-center justify-center rounded-full border bg-background",
                                        ["failed", "error", "cancelled", "degraded", "blocked"].includes(details.status)
                                            ? "border-rose-300 text-rose-600 dark:border-rose-500/40 dark:text-rose-300"
                                            : ["completed", "success", "ready"].includes(details.status)
                                                ? "border-emerald-300 text-emerald-600 dark:border-emerald-500/40 dark:text-emerald-300"
                                                : "border-primary/35 text-primary",
                                    )}>
                                        <Icon className={cn(
                                            "h-3.5 w-3.5",
                                            isLatestActive && meta.motion === "spin" && "animate-[spin_1.6s_linear_infinite]",
                                            isLatestActive && meta.motion === "pulse" && "animate-pulse",
                                        )} data-runtime-activity-motion={isLatestActive ? meta.motion || undefined : undefined} />
                                    </span>
                                    <div className="min-w-0 flex-1 pt-0.5">
                                        <div className="flex min-w-0 items-start gap-2">
                                            <p className="min-w-0 flex-1 break-words text-[12px] font-medium leading-5 text-foreground/90">{activity.summary}</p>
                                            <time className="shrink-0 pt-0.5 text-[9px] tabular-nums text-muted-foreground">{formatRelativeRuntimeTime(activity.timestamp, locale)}</time>
                                        </div>
                                        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
                                            {label ? <span>{label}</span> : null}
                                            {label ? <span aria-hidden="true">·</span> : null}
                                            <span>{statusLabel(details.status, t)}</span>
                                        </div>
                                        {details.content && details.content !== activity.summary ? <p className="mt-1 break-words text-[11px] leading-5 text-muted-foreground">{details.content}</p> : null}
                                    </div>
                                </li>
                            );
                        })}
                    </ol>
                ) : (
                    <div className="px-6 py-12 text-center text-sm text-muted-foreground">{t("web.workbench.runtimeActivity.syncing")}</div>
                )}
            </div>
        </div>
    );
}
