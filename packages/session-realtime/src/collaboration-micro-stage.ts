import type { RuntimeEpisodeGraphActivity, RuntimeEpisodeGraphStatus } from "./runtime-episode-graph.js";
import { getRuntimeRegistryEntry, normalizeRuntimeId } from "./runtime-registry.js";

export type CollaborationMicroStageKind = "subagent" | "runtime";

export type CollaborationMicroStageStatus = RuntimeEpisodeGraphStatus | "degraded";

export type CollaborationMicroStageCue =
  | "summon"
  | "dispatch"
  | "child_agent"
  | "route"
  | "research"
  | "engineering"
  | "creative"
  | "desktop"
  | "rpa"
  | "waiting"
  | "handoff"
  | "completed"
  | "degraded"
  | "failed";

export type CollaborationMicroStageStep = {
  id: string;
  sourceActivityId: string;
  label: string;
  summary: string;
  status: CollaborationMicroStageStatus;
  cue: CollaborationMicroStageCue;
  timestamp: number;
  actorLabel?: string;
  detailRef?: string;
};

export type CollaborationMicroStage = {
  id: string;
  kind: CollaborationMicroStageKind;
  title: string;
  subtitle: string;
  status: CollaborationMicroStageStatus;
  cue: CollaborationMicroStageCue;
  timestamp: number;
  runtimeId?: string;
  episodeId?: string;
  dispatchGroup?: string;
  sourceActivityIds: string[];
  steps: CollaborationMicroStageStep[];
};

export type CollaborationMicroStageActivityInput = RuntimeEpisodeGraphActivity & {
  runtimeId?: string | null;
};

export type BuildCollaborationMicroStageOptions = {
  runId?: string | null;
  locale?: "zh-CN" | "en";
  limit?: number;
  maxStepsPerStage?: number;
};

type StageDraft = CollaborationMicroStage & {
  latestScore: number;
};

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readTimestamp(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function getActivityData(activity: CollaborationMicroStageActivityInput) {
  const data = readRecord(activity.data);
  const episode = readRecord(data.episode);
  const handoff = readRecord(data.handoff);
  const handoffRef = readRecord(data.handoffRef);
  if (Object.keys(episode).length > 0) return episode;
  if (Object.keys(handoffRef).length > 0) return handoffRef;
  if (Object.keys(handoff).length > 0) return handoff;
  return data;
}

function getRunId(activity: CollaborationMicroStageActivityInput) {
  const data = getActivityData(activity);
  return readString(data.runId)
    || readString(data.run_id)
    || readString(data.rootRunId)
    || readString(data.root_run_id)
    || readString(data.sessionId)
    || readString(data.session_id);
}

function normalizeRuntimeKind(value: string) {
  const normalizedRuntimeId = normalizeRuntimeId(value);
  if (normalizedRuntimeId) return normalizedRuntimeId;
  const normalized = value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_");
  if (normalized.includes("research") || normalized.includes("evidence")) return "research";
  if (normalized.includes("engineering") || normalized.includes("patch") || normalized.includes("verification")) return "engineering";
  if (normalized.includes("creative") || normalized.includes("media") || normalized.includes("asset")) return "creative_media";
  if (normalized.includes("computer") || normalized.includes("desktop") || normalized.includes("browser")) return "computer_use";
  if (normalized.includes("rpa") || normalized.includes("trace")) return "rpa";
  if (normalized.includes("delegation") || normalized.includes("subagent") || normalized.includes("child")) return "subagent_swarm";
  return normalized || "runtime";
}

function getRuntimeKind(activity: CollaborationMicroStageActivityInput) {
  const data = getActivityData(activity);
  return normalizeRuntimeKind(
    readString(data.kind)
    || readString(data.runtimeKind)
    || readString(data.runtime_kind)
    || readString(activity.runtimeId)
    || readString(activity.topic),
  );
}

function isMissingDelegation(activity: CollaborationMicroStageActivityInput) {
  const data = getActivityData(activity);
  const topic = readString(activity.topic).toLowerCase();
  const dispatchStatus = readString(data.dispatchStatus || data.dispatch_status).toLowerCase();
  return Boolean(
    data.missingTasks
    || data.missing_tasks
    || data.missingResult
    || data.missing_result
    || data.diagnosticKey === "delegation_missing_tasks"
    || dispatchStatus === "missing_tasks"
    || topic.includes("missing_tasks")
  );
}

function isSubagentActivity(activity: CollaborationMicroStageActivityInput) {
  const topic = readString(activity.topic);
  const runtimeKind = getRuntimeKind(activity);
  return runtimeKind === "subagent_swarm"
    || topic.startsWith("subagent.task.")
    || topic.startsWith("delegation.child.")
    || topic.startsWith("delegation.")
    || topic.startsWith("delegation_broker.");
}

function isRuntimeActivity(activity: CollaborationMicroStageActivityInput) {
  const topic = readString(activity.topic);
  if (!topic.startsWith("runtime.episode.") && !topic.startsWith("handoff.ref.")) return false;
  const runtimeKind = getRuntimeKind(activity);
  return runtimeKind !== "subagent_swarm" && runtimeKind !== "chat" && runtimeKind !== "planner_lane";
}

function inferStatus(activity: CollaborationMicroStageActivityInput): CollaborationMicroStageStatus {
  const data = getActivityData(activity);
  const topic = readString(activity.topic).toLowerCase();
  const text = [
    readString(data.dispatchStatus || data.dispatch_status),
    readString(data.status),
    readString(data.state),
    readString(data.phase),
    topic,
    readString(activity.summary),
  ].join(" ").toLowerCase();
  if (isMissingDelegation(activity) || /degraded|partial|recover/.test(text)) return "degraded";
  if (/(fail|error|reject|blocked|cancel|stalled)/.test(text)) return "failed";
  if (/(complete|finish|done|success|succeeded|merged|ready|handoff)/.test(text)) return "completed";
  if (/(attempt|revealed|unconfirmed)/.test(text)) return "attempted";
  if (/(queued|pending|waiting)/.test(text)) return "pending";
  return "active";
}

function mergeStatus(left: CollaborationMicroStageStatus, right: CollaborationMicroStageStatus): CollaborationMicroStageStatus {
  const rank: Record<CollaborationMicroStageStatus, number> = {
    failed: 6,
    degraded: 5,
    completed: 4,
    active: 3,
    attempted: 2,
    pending: 1,
  };
  return rank[right] >= rank[left] ? right : left;
}

function getSubagentGroup(activity: CollaborationMicroStageActivityInput) {
  const data = getActivityData(activity);
  return readString(data.dispatchGroup)
    || readString(data.dispatch_group)
    || readString(data.delegationId)
    || readString(data.delegation_id)
    || readString(data.parentDelegationId)
    || readString(data.parent_delegation_id)
    || readString(data.taskBriefId)
    || readString(data.task_brief_id)
    || getRunId(activity)
    || readString(activity.id);
}

function getEpisodeId(activity: CollaborationMicroStageActivityInput) {
  const data = getActivityData(activity);
  return readString(data.episodeId)
    || readString(data.episode_id)
    || readString(data.producerEpisodeId)
    || readString(data.producer_episode_id)
    || readString(data.needId)
    || readString(data.need_id)
    || readString(activity.id);
}

function getActorLabel(activity: CollaborationMicroStageActivityInput, kind: CollaborationMicroStageKind, locale: "zh-CN" | "en") {
  const data = getActivityData(activity);
  if (kind === "subagent") {
    return readString(data.subagentName)
      || readString(data.subagent_name)
      || readString(data.targetLabel)
      || readString(data.target_label)
      || readString(data.workerType)
      || readString(data.worker_type)
      || readString(data.agentName)
      || readString(data.agent_name)
      || (locale === "en" ? "Subagent" : "子代理");
  }
  const runtimeKind = getRuntimeKind(activity);
  const runtimeId = normalizeRuntimeId(runtimeKind);
  return runtimeId
    ? getRuntimeRegistryEntry(runtimeId, locale).label
    : (locale === "en" ? "Runtime" : "运行时");
}

function getSummary(activity: CollaborationMicroStageActivityInput) {
  const data = getActivityData(activity);
  return readString(data.compactSummary)
    || readString(data.taskGoal)
    || readString(data.task_goal)
    || readString(data.summary)
    || readString(data.reason)
    || readString(activity.summary);
}

function getDetailRef(activity: CollaborationMicroStageActivityInput) {
  const data = getActivityData(activity);
  return readString(data.detailRef)
    || readString(data.detail_ref)
    || readString(data.rawRef)
    || readString(data.raw_ref)
    || readString(data.handoffRefId)
    || readString(data.handoff_ref_id);
}

function inferCue(activity: CollaborationMicroStageActivityInput, kind: CollaborationMicroStageKind, status: CollaborationMicroStageStatus): CollaborationMicroStageCue {
  if (status === "failed") return "failed";
  if (status === "degraded") return "degraded";
  if (status === "completed") return "completed";
  const topic = readString(activity.topic).toLowerCase();
  const runtimeKind = getRuntimeKind(activity);
  if (topic.startsWith("handoff.ref.")) return "handoff";
  if (kind === "subagent") {
    if (topic.startsWith("delegation.child.")) return "child_agent";
    if (topic.startsWith("subagent.task.")) return "dispatch";
    return "summon";
  }
  if (status === "pending") return "waiting";
  if (runtimeKind === "research") return "research";
  if (runtimeKind === "engineering") return "engineering";
  if (runtimeKind === "creative_media") return "creative";
  if (runtimeKind === "computer_use" || runtimeKind === "desktop_live") return "desktop";
  if (runtimeKind === "rpa") return "rpa";
  return "route";
}

function stepLabel(cue: CollaborationMicroStageCue, kind: CollaborationMicroStageKind, locale: "zh-CN" | "en") {
  const zh: Record<CollaborationMicroStageCue, string> = {
    summon: "召唤协作",
    dispatch: "子任务执行",
    child_agent: "孙代理协作",
    route: "运行路由",
    research: "查阅资料",
    engineering: "工程执行",
    creative: "素材生成",
    desktop: "桌面操作",
    rpa: "流程执行",
    waiting: "等待接单",
    handoff: "结果回流",
    completed: "完成回流",
    degraded: "降级回流",
    failed: "执行异常",
  };
  const en: Record<CollaborationMicroStageCue, string> = {
    summon: "Summon",
    dispatch: "Task running",
    child_agent: "Child agent",
    route: "Runtime route",
    research: "Reading sources",
    engineering: "Engineering",
    creative: "Media job",
    desktop: "Desktop action",
    rpa: "Flow running",
    waiting: "Waiting",
    handoff: "Handoff",
    completed: "Completed",
    degraded: "Degraded",
    failed: "Failed",
  };
  if (cue === "summon" && kind === "subagent") {
    return locale === "en" ? "Delegating" : "派遣子代理";
  }
  return locale === "en" ? en[cue] : zh[cue];
}

function stageTitle(kind: CollaborationMicroStageKind, firstActivity: CollaborationMicroStageActivityInput, locale: "zh-CN" | "en") {
  if (kind === "subagent") {
    return locale === "en" ? "Subagent micro-stage" : "子代理微舞台";
  }
  const runtimeKind = getRuntimeKind(firstActivity);
  const runtimeId = normalizeRuntimeId(runtimeKind);
  return runtimeId
    ? getRuntimeRegistryEntry(runtimeId, locale).label
    : (locale === "en" ? "Runtime micro-stage" : "运行时微舞台");
}

function stageSubtitle(kind: CollaborationMicroStageKind, status: CollaborationMicroStageStatus, locale: "zh-CN" | "en") {
  if (kind === "subagent") {
    if (status === "degraded") return locale === "en" ? "Delegation degraded; usable residue returned." : "委派降级，等待可采纳结果回流。";
    if (status === "failed") return locale === "en" ? "Delegation failed." : "委派异常，需要主链处理。";
    return locale === "en" ? "Supervisor is coordinating real subagent work." : "主理人正在协调真实子代理活动。";
  }
  if (status === "degraded") return locale === "en" ? "Runtime returned a degraded handoff." : "运行时已降级回流。";
  if (status === "failed") return locale === "en" ? "Runtime reported a failure." : "运行时报告异常。";
  return locale === "en" ? "Runtime episode is feeding results back to the chat." : "运行时 episode 正在把结果回流到对话。";
}

export function buildCollaborationMicroStages(
  activities: CollaborationMicroStageActivityInput[],
  options: BuildCollaborationMicroStageOptions = {},
): CollaborationMicroStage[] {
  const locale = options.locale || "zh-CN";
  const expectedRunId = readString(options.runId);
  const maxStepsPerStage = Math.max(1, options.maxStepsPerStage || 4);
  const limit = Math.max(1, options.limit || 4);
  const drafts = new Map<string, StageDraft>();

  const orderedActivities = [...activities].sort((left, right) => {
    const leftTs = readTimestamp(left.timestamp);
    const rightTs = readTimestamp(right.timestamp);
    if (leftTs !== rightTs) return leftTs - rightTs;
    return readString(left.id).localeCompare(readString(right.id));
  });

  for (const activity of orderedActivities) {
    const runId = getRunId(activity);
    if (expectedRunId && runId && runId !== expectedRunId) continue;

    const kind: CollaborationMicroStageKind | null = isSubagentActivity(activity)
      ? "subagent"
      : isRuntimeActivity(activity)
        ? "runtime"
        : null;
    if (!kind) continue;

    const status = inferStatus(activity);
    const cue = inferCue(activity, kind, status);
    const timestamp = readTimestamp(activity.timestamp);
    const group = kind === "subagent" ? getSubagentGroup(activity) : getEpisodeId(activity);
    if (!group) continue;
    const stageId = `${kind}:${group}`;
    const summary = getSummary(activity);
    const step: CollaborationMicroStageStep = {
      id: `${stageId}:step:${readString(activity.id) || timestamp}`,
      sourceActivityId: readString(activity.id) || `${stageId}:${timestamp}`,
      label: stepLabel(cue, kind, locale),
      summary,
      status,
      cue,
      timestamp,
      actorLabel: getActorLabel(activity, kind, locale),
      detailRef: getDetailRef(activity),
    };

    const existing = drafts.get(stageId);
    if (!existing) {
      drafts.set(stageId, {
        id: stageId,
        kind,
        title: stageTitle(kind, activity, locale),
        subtitle: stageSubtitle(kind, status, locale),
        status,
        cue,
        timestamp,
        runtimeId: kind === "runtime" ? getRuntimeKind(activity) : "subagent_swarm",
        episodeId: kind === "runtime" ? group : undefined,
        dispatchGroup: kind === "subagent" ? group : undefined,
        sourceActivityIds: [step.sourceActivityId],
        steps: [step],
        latestScore: timestamp,
      });
      continue;
    }

    const sourceIds = new Set([...existing.sourceActivityIds, step.sourceActivityId]);
    const nextStatus = mergeStatus(existing.status, status);
    existing.status = nextStatus;
    existing.cue = status === "active" || timestamp >= existing.timestamp ? cue : existing.cue;
    existing.subtitle = stageSubtitle(kind, nextStatus, locale);
    existing.timestamp = Math.max(existing.timestamp, timestamp);
    existing.latestScore = Math.max(existing.latestScore, timestamp);
    existing.sourceActivityIds = Array.from(sourceIds);
    const stepExists = existing.steps.some((item) => item.sourceActivityId === step.sourceActivityId);
    if (!stepExists) {
      existing.steps.push(step);
      existing.steps.sort((left, right) => left.timestamp - right.timestamp);
      if (existing.steps.length > maxStepsPerStage) {
        existing.steps = existing.steps.slice(existing.steps.length - maxStepsPerStage);
      }
    }
  }

  return Array.from(drafts.values())
    .sort((left, right) => left.latestScore - right.latestScore)
    .slice(Math.max(0, drafts.size - limit))
    .map(({ latestScore: _latestScore, ...stage }) => stage);
}
