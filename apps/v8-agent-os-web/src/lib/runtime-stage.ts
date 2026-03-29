import { Message, UiArtifactNode, UiExecutionNode, UiGovernanceNode, UiTimelineNode } from "@/store/chat-types";

export type RuntimeId = "chat" | "automation" | "memory" | "plugin_host" | "computer_use" | "rpa" | "extensions";
export type RuntimeCardStatus = "active" | "attention" | "recent" | "idle";

export interface RuntimeDescriptor {
    id: RuntimeId;
    label: string;
    shortLabel: string;
    description: string;
}

export interface RuntimeStageActivity {
    id: string;
    runtimeId: RuntimeId;
    timestamp: number;
    summary: string;
    topic?: string;
    actorLabel?: string;
    messageId: string;
    node: UiTimelineNode;
    kind: "progress" | "tool" | "governance" | "artifact" | "handoff";
}

export interface RuntimeTimelineEntry {
    id: string;
    seq: number;
    runId?: string;
    runtimeId: RuntimeId;
    topic: string;
    kind: RuntimeStageActivity["kind"];
    summary: string;
    actorLabel?: string;
    timestamp: number;
    status?: string;
    metadata?: Record<string, unknown>;
}

export interface RuntimeStageCard {
    id: RuntimeId;
    label: string;
    shortLabel: string;
    description: string;
    status: RuntimeCardStatus;
    eventCount: number;
    lastActivity?: string;
    lastTimestamp?: number;
    stepTitle?: string;
    pendingApproval?: boolean;
    recoverable?: boolean;
}

export interface RuntimeStageModel {
    activeRuntimeId: RuntimeId | null;
    items: RuntimeStageCard[];
    activities: RuntimeStageActivity[];
}

const RUNTIME_DESCRIPTORS: Record<RuntimeId, RuntimeDescriptor> = {
    chat: {
        id: "chat",
        label: "对话运行",
        shortLabel: "对话",
        description: "承接主理人的对话主链和任务编排。",
    },
    automation: {
        id: "automation",
        label: "自动流程",
        shortLabel: "自动化",
        description: "处理 Hook、Cron 与系统自动流程。",
    },
    memory: {
        id: "memory",
        label: "记忆运行",
        shortLabel: "记忆",
        description: "负责记忆召回、知识补充与上下文维护。",
    },
    plugin_host: {
        id: "plugin_host",
        label: "插件宿主",
        shortLabel: "插件宿主",
        description: "承接 OpenClaw sidecar/host 与外部消息接入。",
    },
    computer_use: {
        id: "computer_use",
        label: "桌面操作",
        shortLabel: "桌面",
        description: "执行真实桌面观察、点击、输入与 GUI 操作。",
    },
    rpa: {
        id: "rpa",
        label: "自动流程执行",
        shortLabel: "RPA",
        description: "复用和生成流程自动化草稿与执行链。",
    },
    extensions: {
        id: "extensions",
        label: "扩展运行",
        shortLabel: "扩展",
        description: "负责 Skills 与 MCP 的候选暴露、读取与执行。",
    },
};

const RUNTIME_ORDER: RuntimeId[] = ["chat", "extensions", "computer_use", "rpa", "memory", "automation", "plugin_host"];

function normalizeRuntimeString(value: string): string {
    return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_");
}

export function normalizeRuntimeId(raw?: string | null): RuntimeId | null {
    if (!raw) return null;

    const normalized = normalizeRuntimeString(raw);
    if (!normalized) return null;

    if (
        normalized.includes("extension")
        || normalized.includes("extensions")
        || normalized.includes("skill")
        || normalized.includes("skills")
        || normalized.includes("mcp")
    ) {
        return "extensions";
    }

    if (
        normalized.includes("computer_use")
        || normalized.includes("computeruse")
        || normalized.startsWith("computer")
        || normalized.includes("desktop")
        || normalized.includes("observe")
    ) {
        return "computer_use";
    }

    if (normalized.includes("automation") || normalized.includes("cron") || normalized.includes("hook") || normalized.includes("scheduler")) {
        return "automation";
    }

    if (
        normalized.includes("memory")
        || normalized.includes("recall")
        || normalized.includes("knowledge")
        || normalized.startsWith("mem_")
    ) {
        return "memory";
    }

    if (
        normalized.includes("channel")
        || normalized.includes("feishu")
        || normalized.includes("telegram")
        || normalized.includes("discord")
        || normalized.includes("webhook")
    ) {
        return "plugin_host";
    }

    if (normalized.includes("rpa") || normalized.includes("robot")) {
        return "rpa";
    }

    if (
        normalized.includes("chat")
        || normalized.includes("supervisor")
        || normalized.includes("conversation")
        || normalized.includes("assistant")
    ) {
        return "chat";
    }

    return null;
}

function firstRuntimeMatch(values: Array<string | null | undefined>): RuntimeId | null {
    for (const value of values) {
        const match = normalizeRuntimeId(value);
        if (match) return match;
    }
    return null;
}

function inferRuntimeIdFromArtifact(node: UiArtifactNode): RuntimeId | null {
    const artifact = node.artifact;
    const metadata = artifact.metadata as Record<string, unknown> | undefined;
    return firstRuntimeMatch([
        typeof metadata?.runtime === "string" ? metadata.runtime : undefined,
        typeof metadata?.runtimeId === "string" ? metadata.runtimeId : undefined,
        artifact.sourcePath,
        artifact.workspacePath,
        artifact.previewUrl,
        artifact.id,
    ]);
}

export function inferRuntimeIdFromNode(node: UiTimelineNode): RuntimeId | null {
    if (node.kind === "artifact") {
        return inferRuntimeIdFromArtifact(node);
    }

    if (node.kind === "execution") {
        const executionNode = node as UiExecutionNode;
        return firstRuntimeMatch([
            typeof executionNode.data?.runtime === "string" ? executionNode.data.runtime : undefined,
            typeof executionNode.data?.runtimeId === "string" ? executionNode.data.runtimeId : undefined,
            executionNode.topic,
            executionNode.toolName,
            executionNode.agentName,
            executionNode.agentRoleLabel,
            executionNode.label,
        ]);
    }

    if (node.kind === "governance") {
        const governanceNode = node as UiGovernanceNode;
        return firstRuntimeMatch([
            governanceNode.topic,
            governanceNode.reason,
            governanceNode.agentName,
            governanceNode.agentRoleLabel,
        ]);
    }

    return firstRuntimeMatch([node.agentName, node.agentRoleLabel]);
}

function summarizeExecutionNode(node: UiExecutionNode): string | null {
    if (node.executionType === "runtime_progress") {
        return node.label || node.topic || "运行中";
    }
    if (node.executionType === "tool_call") {
        return node.toolName ? `调用 ${node.toolName}` : "工具调用";
    }
    if (node.executionType === "tool_result") {
        return node.toolCallId ? `工具结果 ${node.toolCallId}` : "工具结果";
    }
    if (node.executionType === "agent_start") {
        return node.agentName ? `${node.agentName} 已接入` : "协作单元已接入";
    }
    if (node.executionType === "reasoning") {
        return "正在推理";
    }
    return null;
}

function summarizeGovernanceNode(node: UiGovernanceNode): string | null {
    if (node.governanceType === "approval_request") {
        return node.question || "等待用户审批";
    }
    return node.reason || node.status || node.topic || "运行控制已更新";
}

function summarizeArtifactNode(node: UiArtifactNode): string | null {
    return node.artifact.displayLabel || node.artifact.title || node.artifact.id || "新的产物";
}

function summarizeNode(node: UiTimelineNode): { summary: string; kind: RuntimeStageActivity["kind"] } | null {
    if (node.kind === "execution") {
        const summary = summarizeExecutionNode(node);
        if (!summary) return null;
        return {
            summary,
            kind: node.executionType === "tool_call" || node.executionType === "tool_result" ? "tool" : node.executionType === "agent_start" ? "handoff" : "progress",
        };
    }

    if (node.kind === "governance") {
        const summary = summarizeGovernanceNode(node);
        if (!summary) return null;
        return { summary, kind: "governance" };
    }

    if (node.kind === "artifact") {
        const summary = summarizeArtifactNode(node);
        if (!summary) return null;
        return { summary, kind: "artifact" };
    }

    return null;
}

function getNodeTimestamp(message: Message, node: UiTimelineNode): number {
    if (typeof node.timestamp === "number" && Number.isFinite(node.timestamp)) {
        return node.timestamp;
    }
    return message.timestamp;
}

function parseTimelineTimestamp(raw: unknown): number {
    if (typeof raw === "number" && Number.isFinite(raw)) {
        return raw;
    }
    if (typeof raw === "string" && raw.trim()) {
        const timestamp = Date.parse(raw);
        if (Number.isFinite(timestamp)) {
            return timestamp;
        }
    }
    return Date.now();
}

export function normalizeRuntimeTimeline(input: unknown[]): RuntimeTimelineEntry[] {
    const entries: RuntimeTimelineEntry[] = [];
    const seen = new Set<string>();

    for (const raw of input) {
        if (!raw || typeof raw !== "object") {
            continue;
        }
        const record = raw as Record<string, unknown>;
        const runtimeId = normalizeRuntimeId(typeof record.runtimeId === "string" ? record.runtimeId : null);
        const topic = typeof record.topic === "string" ? record.topic : "";
        const summary = typeof record.summary === "string" ? record.summary.trim() : "";
        if (!runtimeId || !topic || !summary) {
            continue;
        }

        const id = typeof record.id === "string" && record.id.trim()
            ? record.id
            : `timeline-${runtimeId}-${record.seq || summary}`;
        const seq = Number(record.seq || 0) || 0;
        const key = `${id}:${seq}`;
        if (seen.has(key)) {
            continue;
        }
        seen.add(key);

        const kind = record.kind === "tool"
            || record.kind === "governance"
            || record.kind === "artifact"
            || record.kind === "handoff"
            ? record.kind
            : "progress";

        entries.push({
            id,
            seq,
            runId: typeof record.runId === "string" ? record.runId : undefined,
            runtimeId,
            topic,
            kind,
            summary,
            actorLabel: typeof record.actorLabel === "string" ? record.actorLabel : undefined,
            timestamp: parseTimelineTimestamp(record.timestamp),
            status: typeof record.status === "string" ? record.status : undefined,
            metadata: record.metadata && typeof record.metadata === "object"
                ? record.metadata as Record<string, unknown>
                : undefined,
        });
    }

    entries.sort((left, right) => right.timestamp - left.timestamp);
    return entries;
}

export function buildRuntimeTimelineEntryFromEvent(raw: unknown): RuntimeTimelineEntry | null {
    if (!raw || typeof raw !== "object") {
        return null;
    }
    const record = raw as Record<string, unknown>;
    const topic = typeof record.topic === "string" ? record.topic : "";
    if (!topic.startsWith("extension.") && !topic.startsWith("chat.")) {
        return null;
    }
    const payload = (record.payload && typeof record.payload === "object"
        ? record.payload
        : {}) as Record<string, unknown>;

    let summary = "";
    let kind: RuntimeStageActivity["kind"] = "progress";
    let status: string | undefined;

    if (topic === "chat.command_preset.applied") {
        const presetName = typeof payload.commandPresetName === "string" && payload.commandPresetName.trim()
            ? payload.commandPresetName.trim()
            : "未知命令";
        summary = `已应用命令预设：${presetName}`;
        status = "configured";
    } else if (topic === "chat.task_planning_mode.enabled") {
        summary = "已开启任务模式";
        status = "configured";
    } else if (topic === "extension.route.selected") {
        const skillCount = Array.isArray(payload.skillCandidates) ? payload.skillCandidates.length : 0;
        const mcpCount = Array.isArray(payload.mcpToolCandidates) ? payload.mcpToolCandidates.length : 0;
        summary = `已筛出 ${skillCount} 个 Skills，${mcpCount} 个 MCP 工具`;
        status = "selected";
    } else if (topic === "extension.skill.loaded") {
        summary = `已读取 Skill：${String(payload.skillName || "未知 Skill")}`;
        kind = "tool";
        status = "loaded";
    } else if (topic === "extension.mcp.candidate_exposed") {
        const count = Number(payload.count || (Array.isArray(payload.toolNames) ? payload.toolNames.length : 0)) || 0;
        summary = `已暴露 ${count} 个 MCP 工具`;
        status = "ready";
    } else if (topic === "extension.mcp.invoked") {
        const names = Array.isArray(payload.toolNames)
            ? payload.toolNames.map((item) => String(item).trim()).filter(Boolean)
            : [];
        summary = names.length > 0 ? `已调用 MCP 工具：${names.slice(0, 3).join("、")}` : "已调用 MCP 工具";
        kind = "tool";
        status = "invoked";
    } else if (topic === "extension.execution.completed") {
        const names = Array.isArray(payload.toolNames)
            ? payload.toolNames.map((item) => String(item).trim()).filter(Boolean)
            : [];
        if (names.length > 0) {
            summary = `扩展执行完成，调用了 ${names.slice(0, 3).join("、")}`;
        } else {
            summary = "扩展执行完成";
        }
        status = "completed";
    } else {
        return null;
    }

    return {
        id: typeof record.event_id === "string" && record.event_id.trim()
            ? record.event_id
            : `timeline-${topic}-${record.seq || summary}`,
        seq: Number(record.seq || 0) || 0,
        runId: typeof record.run_id === "string" ? record.run_id : undefined,
        runtimeId: topic.startsWith("chat.") ? "chat" : "extensions",
        topic,
        kind,
        summary,
        actorLabel: typeof (record.source as Record<string, unknown> | undefined)?.agent_id === "string"
            ? String((record.source as Record<string, unknown>).agent_id)
            : topic.startsWith("chat.") ? "对话运行" : "扩展运行",
        timestamp: parseTimelineTimestamp(record.event_ts || record.ts || record.created_at),
        status,
        metadata: payload,
    };
}

export function mergeRuntimeTimeline(
    current: RuntimeTimelineEntry[],
    incoming: RuntimeTimelineEntry[],
): RuntimeTimelineEntry[] {
    const map = new Map<string, RuntimeTimelineEntry>();
    for (const item of [...current, ...incoming]) {
        const key = `${item.id}:${item.seq}`;
        const existing = map.get(key);
        if (!existing || item.timestamp >= existing.timestamp) {
            map.set(key, item);
        }
    }
    return Array.from(map.values()).sort((left, right) => right.timestamp - left.timestamp);
}

function buildNodeFromTimelineEntry(entry: RuntimeTimelineEntry): UiTimelineNode {
    if (entry.kind === "governance") {
        return {
            id: `timeline-node-${entry.id}`,
            kind: "governance",
            governanceType: "run_controlled",
            topic: entry.topic,
            status: entry.status,
            reason: entry.summary,
            timestamp: entry.timestamp,
            agentName: entry.actorLabel,
            agentRoleLabel: entry.actorLabel,
        } satisfies UiGovernanceNode;
    }

    return {
        id: `timeline-node-${entry.id}`,
        kind: "execution",
        executionType: "runtime_progress",
        topic: entry.topic,
        label: entry.summary,
        data: entry.metadata,
        timestamp: entry.timestamp,
        agentName: entry.actorLabel,
        agentRoleLabel: entry.actorLabel,
    } satisfies UiExecutionNode;
}

export function formatRelativeRuntimeTime(timestamp?: number): string {
    if (!timestamp) return "刚刚";
    const diffMs = Date.now() - timestamp;
    const diffMinutes = Math.max(0, Math.floor(diffMs / 60000));
    if (diffMinutes < 1) return "刚刚";
    if (diffMinutes < 60) return `${diffMinutes} 分钟前`;
    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) return `${diffHours} 小时前`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays} 天前`;
}

export function getRuntimeDescriptor(runtimeId: RuntimeId): RuntimeDescriptor {
    return RUNTIME_DESCRIPTORS[runtimeId];
}

export function getRuntimeDescriptors(): RuntimeDescriptor[] {
    return RUNTIME_ORDER.map((runtimeId) => RUNTIME_DESCRIPTORS[runtimeId]);
}

interface BuildRuntimeStageModelOptions {
    ownerRuntime?: string | null;
    status?: string | null;
    pendingApproval?: boolean;
    recoverable?: boolean;
    currentStepTitle?: string | null;
    runtimeTimeline?: RuntimeTimelineEntry[] | null;
}

export function buildRuntimeStageModel(
    messages: Message[],
    options?: BuildRuntimeStageModelOptions,
): RuntimeStageModel {
    const activities: RuntimeStageActivity[] = [];

    for (const message of messages) {
        for (const node of message.nodes || []) {
            const runtimeId = inferRuntimeIdFromNode(node);
            const summarized = summarizeNode(node);
            if (!runtimeId || !summarized) continue;

            activities.push({
                id: node.id,
                runtimeId,
                timestamp: getNodeTimestamp(message, node),
                summary: summarized.summary,
                topic: node.kind === "execution" ? node.topic : node.kind === "governance" ? node.topic : undefined,
                actorLabel: node.agentName || node.agentRoleLabel,
                messageId: message.id,
                node,
                kind: summarized.kind,
            });
        }
    }

    const seenTimelineKeys = new Set<string>();
    for (const entry of options?.runtimeTimeline || []) {
        const key = `${entry.id}:${entry.seq}`;
        if (seenTimelineKeys.has(key)) {
            continue;
        }
        seenTimelineKeys.add(key);
        activities.push({
            id: entry.id,
            runtimeId: entry.runtimeId,
            timestamp: entry.timestamp,
            summary: entry.summary,
            topic: entry.topic,
            actorLabel: entry.actorLabel,
            messageId: entry.runId || entry.id,
            node: buildNodeFromTimelineEntry(entry),
            kind: entry.kind,
        });
    }

    activities.sort((left, right) => right.timestamp - left.timestamp);

    const activeRuntimeId = normalizeRuntimeId(options?.ownerRuntime) ?? activities[0]?.runtimeId ?? null;
    const isBusy = Boolean(options?.status && !["completed", "failed", "cancelled"].includes(options.status));

    const items = RUNTIME_ORDER.map((runtimeId) => {
        const descriptor = getRuntimeDescriptor(runtimeId);
        const runtimeActivities = activities.filter((activity) => activity.runtimeId === runtimeId);
        const lastActivity = runtimeActivities[0];

        let status: RuntimeCardStatus = "idle";
        if (runtimeId === activeRuntimeId && isBusy) {
            status = options?.pendingApproval ? "attention" : "active";
        } else if (lastActivity) {
            status = "recent";
        }

        return {
            id: runtimeId,
            label: descriptor.label,
            shortLabel: descriptor.shortLabel,
            description: descriptor.description,
            status,
            eventCount: runtimeActivities.length,
            lastActivity: runtimeId === activeRuntimeId && options?.currentStepTitle
                ? options.currentStepTitle
                : lastActivity?.summary,
            lastTimestamp: lastActivity?.timestamp,
            stepTitle: runtimeId === activeRuntimeId ? options?.currentStepTitle || undefined : undefined,
            pendingApproval: runtimeId === activeRuntimeId ? options?.pendingApproval : false,
            recoverable: runtimeId === activeRuntimeId ? options?.recoverable : false,
        } satisfies RuntimeStageCard;
    });

    return {
        activeRuntimeId,
        items,
        activities,
    };
}
