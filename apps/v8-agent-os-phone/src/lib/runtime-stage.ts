import type {
    ChatArtifact,
    ChatMessage,
    PhoneUiArtifactNode,
    PhoneUiExecutionNode,
    PhoneUiGovernanceNode,
    PhoneUiTimelineNode,
} from "@/src/types/admin";

export type PhoneRuntimeId =
    | "chat"
    | "automation"
    | "memory"
    | "plugin_host"
    | "computer_use"
    | "rpa"
    | "extensions";

export type PhoneRuntimeCardStatus = "idle" | "recent" | "active" | "attention";

export type PhoneRuntimeTimelineEntry = {
    id: string;
    seq?: number;
    runId?: string;
    runtimeId: PhoneRuntimeId;
    topic: string;
    kind?: "progress" | "tool" | "governance" | "artifact" | "handoff";
    summary: string;
    timestamp: number;
    actorLabel?: string;
    status?: string;
    metadata?: Record<string, unknown>;
};

export type PhoneRuntimeStageCard = {
    id: PhoneRuntimeId;
    label: string;
    shortLabel: string;
    description: string;
    status: PhoneRuntimeCardStatus;
    eventCount: number;
    lastActivity?: string;
    lastTimestamp?: number;
    stepTitle?: string;
    pendingApproval?: boolean;
};

export type PhoneRuntimeStageActivity = {
    id: string;
    runtimeId: PhoneRuntimeId;
    timestamp: number;
    summary: string;
    topic?: string;
    actorLabel?: string;
    messageId: string;
    node: PhoneUiTimelineNode;
    kind: "progress" | "tool" | "governance" | "artifact" | "handoff";
};

export type PhoneRuntimeStageModel = {
    activeRuntimeId: PhoneRuntimeId | null;
    items: PhoneRuntimeStageCard[];
    activities: PhoneRuntimeStageActivity[];
};

type RuntimeDescriptor = {
    id: PhoneRuntimeId;
    label: string;
    shortLabel: string;
    description: string;
};

const RUNTIME_DESCRIPTORS: Record<PhoneRuntimeId, RuntimeDescriptor> = {
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
        description: "承接 OpenClaw host 与外部消息接入。",
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

export const PHONE_RUNTIME_ORDER: PhoneRuntimeId[] = [
    "chat",
    "extensions",
    "computer_use",
    "rpa",
    "memory",
    "automation",
    "plugin_host",
];

function normalizeRuntimeString(value: string) {
    return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_");
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

function firstRuntimeMatch(values: Array<string | null | undefined>): PhoneRuntimeId | null {
    for (const value of values) {
        const match = normalizePhoneRuntimeId(value);
        if (match) {
            return match;
        }
    }
    return null;
}

function inferRuntimeIdFromArtifactNode(node: PhoneUiArtifactNode): PhoneRuntimeId | null {
    const artifact = node.artifact as ChatArtifact & { metadata?: Record<string, unknown> };
    const metadata = artifact.metadata && typeof artifact.metadata === "object" ? artifact.metadata : undefined;
    return firstRuntimeMatch([
        typeof metadata?.runtime === "string" ? metadata.runtime : undefined,
        typeof metadata?.runtimeId === "string" ? metadata.runtimeId : undefined,
        artifact.sourcePath,
        artifact.workspacePath,
        artifact.previewUrl,
        artifact.id,
    ]);
}

export function inferPhoneRuntimeIdFromNode(node: PhoneUiTimelineNode): PhoneRuntimeId | null {
    if (node.kind === "artifact") {
        return inferRuntimeIdFromArtifactNode(node);
    }

    if (node.kind === "execution") {
        const executionNode = node as PhoneUiExecutionNode;
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
        const governanceNode = node as PhoneUiGovernanceNode;
        return firstRuntimeMatch([
            governanceNode.topic,
            governanceNode.reason,
            governanceNode.agentName,
            governanceNode.agentRoleLabel,
        ]);
    }

    return firstRuntimeMatch([node.agentName, node.agentRoleLabel]);
}

function summarizeExecutionNode(node: PhoneUiExecutionNode): string | null {
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

function summarizeGovernanceNode(node: PhoneUiGovernanceNode): string | null {
    if (node.governanceType === "approval_request") {
        return node.question || "等待用户审批";
    }
    return node.reason || node.status || node.topic || "运行控制已更新";
}

function summarizeArtifactNode(node: PhoneUiArtifactNode): string | null {
    return node.artifact.displayLabel || node.artifact.title || node.artifact.id || "新的产物";
}

function summarizeTimelineNode(node: PhoneUiTimelineNode): { summary: string; kind: PhoneRuntimeStageActivity["kind"] } | null {
    if (node.kind === "execution") {
        const summary = summarizeExecutionNode(node);
        if (!summary) return null;
        return {
            summary,
            kind: node.executionType === "tool_call" || node.executionType === "tool_result"
                ? "tool"
                : node.executionType === "agent_start"
                    ? "handoff"
                    : "progress",
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

function getNodeTimestamp(message: ChatMessage, node: PhoneUiTimelineNode): number {
    if (typeof node.timestamp === "number" && Number.isFinite(node.timestamp)) {
        return node.timestamp;
    }
    return Number(message.timestamp || Date.now());
}

function coerceTimelineString(value: unknown) {
    const normalized = String(value || "").trim();
    return normalized || undefined;
}

function buildTimelineArtifactNode(entry: PhoneRuntimeTimelineEntry): PhoneUiArtifactNode {
    const metadata = entry.metadata && typeof entry.metadata === "object"
        ? entry.metadata as Record<string, unknown>
        : {};
    const artifactId = coerceTimelineString(metadata.artifactId)
        || coerceTimelineString(metadata.id)
        || coerceTimelineString(metadata.messageId)
        || entry.id;

    return {
        id: `timeline-node-${entry.id}`,
        kind: "artifact",
        timestamp: entry.timestamp,
        agentName: entry.actorLabel,
        agentRoleLabel: entry.actorLabel,
        artifact: {
            id: artifactId,
            artifactId: coerceTimelineString(metadata.artifactId),
            title: coerceTimelineString(metadata.title) || entry.summary,
            displayLabel: coerceTimelineString(metadata.displayLabel) || coerceTimelineString(metadata.title) || entry.summary,
            displaySubtitle: coerceTimelineString(metadata.displaySubtitle)
                || coerceTimelineString(metadata.workspacePath)
                || coerceTimelineString(metadata.sourcePath)
                || coerceTimelineString(metadata.kind),
            kind: coerceTimelineString(metadata.kind),
            mimeType: coerceTimelineString(metadata.mimeType),
            previewUrl: coerceTimelineString(metadata.previewUrl),
            externalUrl: coerceTimelineString(metadata.externalUrl),
            sourcePath: coerceTimelineString(metadata.sourcePath),
            workspacePath: coerceTimelineString(metadata.workspacePath),
            runId: entry.runId,
            metadata,
        },
    };
}

function buildNodeFromTimelineEntry(entry: PhoneRuntimeTimelineEntry): PhoneUiTimelineNode {
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
        };
    }

    if (entry.kind === "artifact") {
        return buildTimelineArtifactNode(entry);
    }

    const metadata = entry.metadata && typeof entry.metadata === "object"
        ? entry.metadata as Record<string, unknown>
        : undefined;
    const content = coerceTimelineString(
        metadata?.content
        || metadata?.summary
        || metadata?.message
        || metadata?.reason
        || metadata?.label,
    );
    const executionType = entry.kind === "tool"
        ? "tool_call"
        : entry.kind === "handoff"
            ? "agent_start"
            : "runtime_progress";

    return {
        id: `timeline-node-${entry.id}`,
        kind: "execution",
        executionType,
        topic: entry.topic,
        label: entry.summary,
        content,
        toolName: executionType === "tool_call" ? coerceTimelineString(metadata?.toolName || metadata?.tool_name) : undefined,
        toolCallId: executionType === "tool_call" ? coerceTimelineString(metadata?.toolCallId || metadata?.tool_call_id || metadata?.approval_id) : undefined,
        args: executionType === "tool_call" ? metadata?.args ?? metadata?.request : undefined,
        result: executionType !== "tool_call" ? metadata?.result ?? metadata?.response ?? metadata?.result_preview : undefined,
        data: entry.metadata,
        timestamp: entry.timestamp,
        agentName: entry.actorLabel,
        agentRoleLabel: entry.actorLabel,
    };
}

export function formatPhoneRelativeRuntimeTime(timestamp?: number): string {
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

export function normalizePhoneRuntimeId(raw?: string | null): PhoneRuntimeId | null {
    if (!raw) return null;

    const normalized = normalizeRuntimeString(raw);
    if (!normalized) return null;

    if (
        normalized.includes("extension")
        || normalized.includes("skill")
        || normalized.includes("mcp")
    ) {
        return "extensions";
    }

    if (
        normalized.includes("computer_use")
        || normalized.includes("desktop")
        || normalized.includes("observe")
    ) {
        return "computer_use";
    }

    if (normalized.includes("automation") || normalized.includes("cron") || normalized.includes("hook")) {
        return "automation";
    }

    if (normalized.includes("memory") || normalized.includes("recall") || normalized.includes("knowledge")) {
        return "memory";
    }

    if (
        normalized.includes("channel")
        || normalized.includes("gateway")
        || normalized.includes("plugin")
        || normalized.includes("host")
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

export function mergePhoneRuntimeTimeline(
    current: PhoneRuntimeTimelineEntry[],
    incoming: PhoneRuntimeTimelineEntry[],
) {
    const map = new Map<string, PhoneRuntimeTimelineEntry>();

    for (const item of [...current, ...incoming]) {
        const key = item.id || `${item.runtimeId}:${item.topic}:${item.timestamp}`;
        const existing = map.get(key);
        if (!existing || item.timestamp >= existing.timestamp) {
            map.set(key, item);
        }
    }

    return Array.from(map.values()).sort((left, right) => right.timestamp - left.timestamp);
}

export function getPhoneRuntimeDescriptor(runtimeId: PhoneRuntimeId) {
    return RUNTIME_DESCRIPTORS[runtimeId];
}

export function normalizePhoneRuntimeTimeline(input: unknown[]): PhoneRuntimeTimelineEntry[] {
    const entries: PhoneRuntimeTimelineEntry[] = [];
    const seen = new Set<string>();

    for (const raw of input) {
        if (!raw || typeof raw !== "object") {
            continue;
        }
        const record = raw as Record<string, unknown>;
        const runtimeId = normalizePhoneRuntimeId(typeof record.runtimeId === "string" ? record.runtimeId : null);
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

export function buildPhoneRuntimeTimelineEntryFromEvent(raw: unknown): PhoneRuntimeTimelineEntry | null {
    if (!raw || typeof raw !== "object") {
        return null;
    }
    const record = raw as Record<string, unknown>;
    const topic = typeof record.topic === "string" ? record.topic : "";
    const payload = (record.payload && typeof record.payload === "object"
        ? record.payload
        : {}) as Record<string, unknown>;
    let runtimeId = normalizePhoneRuntimeId(
        typeof record.runtimeId === "string"
            ? record.runtimeId
            : typeof payload.runtimeId === "string"
                ? payload.runtimeId
                : typeof payload.runtime === "string"
                    ? payload.runtime
                    : topic,
    );

    if (
        !topic.startsWith("extension.")
        && !topic.startsWith("chat.")
        && !topic.startsWith("computer_use.")
        && !topic.startsWith("run.")
        && !topic.startsWith("plugin_host.")
        && !topic.startsWith("memory.")
        && !topic.startsWith("automation.")
        && topic !== "supervisor.graph.diagnostics"
        && topic !== "approval.requested"
        && topic !== "artifact.recorded"
        && !runtimeId
    ) {
        return null;
    }
    let summary = "";
    let kind: NonNullable<PhoneRuntimeTimelineEntry["kind"]> = "progress";
    let status: string | undefined;

    if (topic === "chat.command_preset.applied") {
        const presetName = typeof payload.commandPresetName === "string" && payload.commandPresetName.trim()
            ? payload.commandPresetName.trim()
            : "未知命令";
        summary = `已应用命令预设：${presetName}`;
        status = "configured";
        runtimeId ||= "chat";
    } else if (topic === "chat.task_planning_mode.enabled") {
        summary = "已开启任务模式";
        status = "configured";
        runtimeId ||= "chat";
    } else if (topic === "run.continuation.scheduled") {
        const continuationCount = Number(payload.continuationCount || 0) || 0;
        const continuationReason = typeof payload.continuationReason === "string" && payload.continuationReason.trim()
            ? payload.continuationReason.trim()
            : "unknown";
        summary = `已静默续跑第 ${continuationCount} 段执行（${continuationReason}）`;
        status = "continued";
        runtimeId ||= "chat";
    } else if (topic === "supervisor.graph.diagnostics") {
        const parts: string[] = [];
        if (typeof payload.graphBuildMs === "number") parts.push(`graph ${payload.graphBuildMs}ms`);
        if (typeof payload.routeBuildMs === "number") parts.push(`route ${payload.routeBuildMs}ms`);
        if (typeof payload.systemContentBuildMs === "number") parts.push(`prompt ${payload.systemContentBuildMs}ms`);
        if (typeof payload.passiveRagMs === "number") parts.push(`rag ${payload.passiveRagMs}ms`);
        summary = parts.length > 0 ? `Supervisor 诊断：${parts.join("，")}` : "Supervisor 诊断已记录";
        status = "diagnostics";
        runtimeId ||= "chat";
    } else if (topic === "extension.route.selected") {
        const skillCount = Array.isArray(payload.skillCandidates) ? payload.skillCandidates.length : 0;
        const mcpCount = Array.isArray(payload.mcpToolCandidates) ? payload.mcpToolCandidates.length : 0;
        summary = `已筛出 ${skillCount} 个 Skills，${mcpCount} 个 MCP 工具`;
        status = "selected";
        runtimeId ||= "extensions";
    } else if (topic === "extension.skill.loaded") {
        summary = `已读取 Skill：${String(payload.skillName || "未知 Skill")}`;
        kind = "tool";
        status = "loaded";
        runtimeId ||= "extensions";
    } else if (topic === "extension.skill.blocked" || topic === "safety.skill_blocked") {
        const verdict = typeof payload.verdict === "string" && payload.verdict.trim()
            ? payload.verdict.trim()
            : "high";
        summary = `Safety Guardian 已阻断 Skill：${String(payload.skillName || "未知 Skill")}（${verdict}）`;
        kind = "governance";
        status = "blocked";
        runtimeId ||= "extensions";
    } else if (topic === "extension.mcp.candidate_exposed") {
        const count = Number(payload.count || (Array.isArray(payload.toolNames) ? payload.toolNames.length : 0)) || 0;
        summary = `已暴露 ${count} 个 MCP 工具`;
        status = "ready";
        runtimeId ||= "extensions";
    } else if (topic === "extension.mcp.invoked") {
        const names = Array.isArray(payload.toolNames)
            ? payload.toolNames.map((item) => String(item).trim()).filter(Boolean)
            : [];
        summary = names.length > 0 ? `已调用 MCP 工具：${names.slice(0, 3).join("、")}` : "已调用 MCP 工具";
        kind = "tool";
        status = "invoked";
        runtimeId ||= "extensions";
    } else if (topic === "extension.execution.completed") {
        const names = Array.isArray(payload.toolNames)
            ? payload.toolNames.map((item) => String(item).trim()).filter(Boolean)
            : [];
        summary = names.length > 0
            ? `扩展执行完成，调用了 ${names.slice(0, 3).join("、")}`
            : "扩展执行完成";
        status = "completed";
        runtimeId ||= "extensions";
    } else if (topic === "approval.requested") {
        summary = typeof payload.question === "string" && payload.question.trim()
            ? payload.question.trim()
            : "等待用户确认";
        kind = "governance";
        status = "pending";
        runtimeId ||= "automation";
    } else if (topic === "artifact.recorded") {
        summary = String(payload.title || payload.kind || payload.workspacePath || "记录新的产物");
        kind = "artifact";
        runtimeId ||= normalizePhoneRuntimeId(String(payload.kind || payload.workspacePath || "chat")) || "chat";
    } else if (topic === "run.lane.acquired") {
        summary = "已获得当前会话执行权";
        status = "acquired";
        runtimeId ||= "chat";
    } else if (topic === "run.lane.released") {
        summary = "已释放当前会话执行权";
        status = "released";
        runtimeId ||= "chat";
    } else if (topic.startsWith("run.")) {
        summary = typeof payload.label === "string" && payload.label.trim()
            ? payload.label.trim()
            : topic;
        status = typeof payload.status === "string" ? payload.status : undefined;
        runtimeId ||= "chat";
    } else if (topic.startsWith("computer_use.")) {
        summary = typeof payload.label === "string" && payload.label.trim()
            ? payload.label.trim()
            : typeof payload.action === "string" && payload.action.trim()
                ? `桌面操作：${payload.action.trim()}`
                : "桌面操作更新";
        status = typeof payload.status === "string" ? payload.status : undefined;
        runtimeId ||= "computer_use";
    } else if (topic.startsWith("automation.")) {
        summary = typeof payload.label === "string" && payload.label.trim() ? payload.label.trim() : topic;
        status = typeof payload.status === "string" ? payload.status : undefined;
        runtimeId ||= "automation";
    } else if (topic.startsWith("memory.")) {
        summary = typeof payload.label === "string" && payload.label.trim() ? payload.label.trim() : topic;
        status = typeof payload.status === "string" ? payload.status : undefined;
        runtimeId ||= "memory";
    } else if (topic.startsWith("plugin_host.")) {
        summary = typeof payload.label === "string" && payload.label.trim() ? payload.label.trim() : topic;
        status = typeof payload.status === "string" ? payload.status : undefined;
        runtimeId ||= "plugin_host";
    } else {
        return null;
    }

    if (!runtimeId) {
        return null;
    }

    return {
        id: typeof record.event_id === "string" && record.event_id.trim()
            ? record.event_id
            : `timeline-${topic}-${record.seq || summary}`,
        seq: Number(record.seq || 0) || 0,
        runId: typeof record.run_id === "string" ? record.run_id : undefined,
        runtimeId,
        topic,
        kind,
        summary,
        actorLabel: typeof (record.source as Record<string, unknown> | undefined)?.agent_id === "string"
            ? String((record.source as Record<string, unknown>).agent_id)
            : runtimeId === "extensions"
                ? "扩展运行"
                : runtimeId === "computer_use"
                    ? "桌面操作"
                    : "对话运行",
        timestamp: parseTimelineTimestamp(record.event_ts || record.ts || record.created_at),
        status,
        metadata: payload,
    };
}

export function buildPhoneRuntimeStageModel(
    messages: ChatMessage[],
    options?: {
        ownerRuntime?: string | null;
        status?: string | null;
        pendingApproval?: boolean;
        currentStepTitle?: string | null;
        runtimeTimeline?: PhoneRuntimeTimelineEntry[] | null;
    },
): PhoneRuntimeStageModel {
    const activities: PhoneRuntimeStageActivity[] = [];

    for (const message of messages) {
        for (const node of Array.isArray(message.nodes) ? message.nodes : []) {
            const runtimeId = inferPhoneRuntimeIdFromNode(node);
            const summarized = summarizeTimelineNode(node);
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
        const key = `${entry.id}:${entry.seq || 0}`;
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
            kind: entry.kind || "progress",
        });
    }

    activities.sort((left, right) => right.timestamp - left.timestamp);
    const activeRuntimeId = normalizePhoneRuntimeId(options?.ownerRuntime) ?? activities[0]?.runtimeId ?? null;
    const runtimeStatus = String(options?.status || "").trim().toLowerCase();
    const isBusy = Boolean(runtimeStatus && !["completed", "failed", "cancelled", "idle"].includes(runtimeStatus));

    const items = PHONE_RUNTIME_ORDER.map((runtimeId) => {
        const descriptor = getPhoneRuntimeDescriptor(runtimeId);
        const runtimeActivities = activities.filter((activity) => activity.runtimeId === runtimeId);
        const lastActivity = runtimeActivities[0];

        let status: PhoneRuntimeCardStatus = "idle";
        if (runtimeId === activeRuntimeId && isBusy) {
            status = options?.pendingApproval ? "attention" : "active";
        } else if (runtimeId === activeRuntimeId && runtimeStatus === "failed") {
            status = "attention";
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
        } satisfies PhoneRuntimeStageCard;
    });

    return {
        activeRuntimeId,
        items,
        activities,
    };
}
