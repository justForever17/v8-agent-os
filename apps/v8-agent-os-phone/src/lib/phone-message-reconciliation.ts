import { buildAssistantMessage } from "@/src/lib/chat-stream-state";
import type { AgentProfile, PhoneRealtimeUiEvent } from "@/src/lib/chat-stream-state";
import { normalizeMessagesForState } from "@/src/lib/chat-state";
import { translateCurrent } from "@/src/lib/locale";
import type {
    ChatArtifact,
    ChatMessage,
    ConversationDetail,
    PhoneUiTimelineNode,
    QueuedChatMessage,
    RealtimeSessionSnapshot,
    SessionTodoItem,
} from "@/src/types/admin";
import { isActiveAssistantStreamPhase, mergeTimelineNodesByIdentity } from "@v8/session-realtime";

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function buildAssistantPlaceholder(runId?: string, agentProfile?: AgentProfile): ChatMessage {
    return buildAssistantMessage({
        agentName: agentProfile?.agentName || translateCurrent("shared.actor.supervisor"),
        agentAvatar: agentProfile?.agentAvatar || "/brand-mark.png",
        agentRoleLabel: agentProfile?.agentRoleLabel || translateCurrent("shared.actor.lead"),
    }, runId, "placeholder");
}

function mergeArtifacts(base: ChatArtifact[] = [], incoming: ChatArtifact[] = []) {
    const merged = new Map<string, ChatArtifact>();
    for (const artifact of [...base, ...incoming]) {
        const key = String(
            artifact.id
            || artifact.artifactId
            || artifact.workspacePath
            || artifact.sourcePath
            || artifact.previewUrl
            || artifact.externalUrl
            || `${artifact.kind || "artifact"}:${artifact.title || ""}`,
        ).trim();
        if (!key) {
            continue;
        }
        merged.set(key, {
            ...(merged.get(key) || {}),
            ...artifact,
        });
    }
    return Array.from(merged.values());
}

function mergeTimelineNodes(base: PhoneUiTimelineNode[] = [], incoming: PhoneUiTimelineNode[] = []) {
    return mergeTimelineNodesByIdentity(base, incoming) as PhoneUiTimelineNode[];
}

const TODO_TOOL_NAMES = new Set(["write_todos", "update_todo"]);

type AssistantTaskProgressPatch = {
    phase?: NonNullable<ChatMessage["uiStreamPhase"]>;
    label?: string;
    subtitle?: string;
    currentStep?: string;
    completedCount?: number;
    totalCount?: number;
};

function normalizeTodoStatus(value: unknown) {
    const normalized = String(value || "").trim().toLowerCase();
    return normalized === "done" || normalized === "in_progress" || normalized === "skipped"
        ? normalized
        : "pending";
}

function extractTodoText(value: unknown) {
    if (typeof value === "string") {
        return value.trim();
    }
    if (!value || typeof value !== "object") {
        return "";
    }
    const record = value as Record<string, unknown>;
    return String(record.content || record.text || record.title || "").trim();
}

function coerceSessionTodoItem(value: unknown, index: number): SessionTodoItem | null {
    const content = extractTodoText(value);
    if (!content) {
        return null;
    }
    const record = value && typeof value === "object" ? value as Record<string, unknown> : {};
    return {
        id: typeof record.id === "string" ? record.id : `todo-${index}`,
        content,
        status: normalizeTodoStatus(record.status),
    };
}

function normalizeTaskTodos(value: unknown): SessionTodoItem[] {
    if (!Array.isArray(value)) {
        return [];
    }
    return value
        .map((item, index) => coerceSessionTodoItem(item, index))
        .filter((item): item is SessionTodoItem => Boolean(item));
}

function buildAssistantTaskProgressPatch(
    todos: SessionTodoItem[],
    options?: {
        phase?: AssistantTaskProgressPatch["phase"];
        label?: string;
        subtitle?: string;
    },
): AssistantTaskProgressPatch | null {
    const normalizedTodos = todos.filter((item) => String(item.content || "").trim());
    const totalCount = normalizedTodos.length;
    if (totalCount === 0 && !options?.label && !options?.subtitle) {
        return null;
    }

    const completedCount = normalizedTodos.filter((item) => normalizeTodoStatus(item.status) === "done").length;
    const activeIndex = normalizedTodos.findIndex((item) => normalizeTodoStatus(item.status) === "in_progress");
    const nextIndex = normalizedTodos.findIndex((item) => normalizeTodoStatus(item.status) === "pending");
    const activeTodo = activeIndex >= 0 ? normalizedTodos[activeIndex] : null;
    const nextTodo = nextIndex >= 0 ? normalizedTodos[nextIndex] : null;
    const currentStep = String(activeTodo?.content || nextTodo?.content || normalizedTodos[normalizedTodos.length - 1]?.content || "").trim();

    let phase = options?.phase;
    let label = options?.label;
    let subtitle = options?.subtitle;

    if (!phase) {
        if (completedCount >= totalCount && totalCount > 0) {
            phase = "settling";
        } else if (activeTodo) {
            phase = "tooling";
        } else if (nextTodo) {
            phase = completedCount > 0 ? "tooling" : "task_planning";
        } else {
            phase = "task_planning";
        }
    }

    if (!label) {
        if (phase === "waiting_input") {
            label = translateCurrent("src.screens.chatscreen.waiting_for_your_answer");
        } else if (phase === "artifact_ready") {
            label = translateCurrent("src.screens.chatscreen.artifact_ready");
        } else if (phase === "settling") {
            label = translateCurrent("src.screens.chatscreen.task_is_nearly_complete");
        } else if (activeTodo) {
            label = translateCurrent("src.screens.chatscreen.step_progress", { current: activeIndex + 1, total: Math.max(totalCount, 1) });
        } else if (nextTodo) {
            label = completedCount > 0
                ? translateCurrent("src.screens.chatscreen.preparing_step_progress", { current: nextIndex + 1, total: Math.max(totalCount, 1) })
                : translateCurrent("src.screens.chatscreen.planning_task");
        } else {
            label = translateCurrent("src.screens.chatscreen.planning_task");
        }
    }

    if (!subtitle) {
        if (phase === "artifact_ready") {
            subtitle = currentStep || (
                totalCount > 0
                    ? translateCurrent("src.screens.chatscreen.completed_step_progress", { completed: completedCount, total: totalCount })
                    : translateCurrent("src.screens.chatscreen.artifact_can_now_be_attached_to_the_workspace")
            );
        } else if (phase === "waiting_input") {
            subtitle = currentStep || translateCurrent("src.screens.chatscreen.please_provide_the_requested_input");
        } else if (activeTodo) {
            subtitle = currentStep;
        } else if (nextTodo) {
            subtitle = currentStep || translateCurrent("src.screens.chatscreen.generated_step_count", { total: totalCount });
        } else if (totalCount > 0) {
            subtitle = translateCurrent("src.screens.chatscreen.completed_step_progress", { completed: completedCount, total: totalCount });
        }
    }

    return {
        phase,
        label,
        subtitle,
        currentStep: currentStep || undefined,
        completedCount,
        totalCount,
    };
}

function readAssistantTaskProgress(message: ChatMessage | null | undefined) {
    return recordOf(message?.metadata?.assistantTaskProgress);
}

function countDefinedAssistantTaskProgressFields(value: Record<string, unknown>) {
    return [
        value.phase,
        value.label,
        value.subtitle,
        value.currentStep,
        value.completedCount,
        value.totalCount,
    ].filter((item) => item !== undefined && item !== null && String(item).trim() !== "").length;
}

function hasStructuredAssistantPayload(message: ChatMessage | null | undefined) {
    if (!message) {
        return false;
    }
    return Boolean(
        (message.nodes || []).length > 0
        || (message.artifacts || []).length > 0
        || (message.images || []).length > 0
        || countDefinedAssistantTaskProgressFields(readAssistantTaskProgress(message)) > 0,
    );
}

function shouldPreserveLocalAssistantMessage(message: ChatMessage | null | undefined) {
    if (!message || message.role !== "assistant") {
        return false;
    }
    return Boolean(
        isOptimisticLocalMessage(message)
        || isActiveAssistantStreamPhase(message.uiStreamPhase)
        || message.uiEphemeral,
    );
}

function findLatestAssistantShellIndex(messages: ChatMessage[], runId?: string) {
    const normalizedRunId = String(runId || "").trim();
    if (normalizedRunId) {
        for (let index = messages.length - 1; index >= 0; index -= 1) {
            const message = messages[index];
            if (message.role !== "assistant") {
                continue;
            }
            if (String(message.runId || message.metadata?.runId || "").trim() === normalizedRunId) {
                return index;
            }
        }
    }

    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const message = messages[index];
        if (message.role !== "assistant") {
            continue;
        }
        if (message.uiEphemeral || isActiveAssistantStreamPhase(message.uiStreamPhase)) {
            return index;
        }
    }
    return -1;
}

function applyAssistantTaskProgressPatch(
    current: ChatMessage[],
    patch: AssistantTaskProgressPatch | null,
    runId?: string,
    options?: { createIfMissing?: boolean },
) {
    if (!patch) {
        return current;
    }

    const next = [...current];
    let targetIndex = findLatestAssistantShellIndex(next, runId);

    if (targetIndex < 0) {
        if (!options?.createIfMissing) {
            return current;
        }
        next.push(buildAssistantPlaceholder(runId));
        targetIndex = next.length - 1;
    }

    const target = next[targetIndex];
    const nextMetadata = {
        ...(target.metadata || {}),
        assistantTaskProgress: {
            phase: patch.phase,
            label: patch.label,
            subtitle: patch.subtitle,
            currentStep: patch.currentStep,
            completedCount: patch.completedCount,
            totalCount: patch.totalCount,
        },
    };

    next[targetIndex] = {
        ...target,
        runId: runId || target.runId,
        uiEphemeral: target.uiEphemeral !== false || !hasRenderableMessagePayload(target),
        uiStreamPhase: patch.phase || target.uiStreamPhase,
        metadata: nextMetadata,
        timestamp: Date.now(),
    };

    return normalizeMessagesForState(next);
}

function applyTodoToolEvent(
    currentTodos: SessionTodoItem[],
    event: PhoneRealtimeUiEvent,
): SessionTodoItem[] | null {
    const toolName = String(event.tool?.toolName || event.data?.toolName || event.data?.tool_name || "").trim();
    if (!TODO_TOOL_NAMES.has(toolName)) {
        return null;
    }

    const args = recordOf(event.tool?.args);
    if (toolName === "write_todos") {
        const nextTodos = normalizeTaskTodos(args.todos);
        return nextTodos.length > 0 ? nextTodos : currentTodos;
    }

    if (toolName === "update_todo") {
        const index = Number(args.index);
        const status = normalizeTodoStatus(args.status);
        if (!Number.isFinite(index) || index < 0 || currentTodos.length === 0) {
            return currentTodos;
        }
        return currentTodos.map((item, itemIndex) => {
            if (itemIndex !== index) {
                if (status === "in_progress" && normalizeTodoStatus(item.status) === "in_progress") {
                    return {
                        ...item,
                        status: "pending",
                    };
                }
                return item;
            }
            return {
                ...item,
                status,
            };
        });
    }

    return null;
}

function buildArtifactFingerprint(artifact: ChatArtifact) {
    return [
        String(artifact.id || artifact.artifactId || artifact.workspacePath || artifact.sourcePath || artifact.previewUrl || artifact.externalUrl || "").trim(),
        String(artifact.kind || "").trim(),
        String(artifact.title || "").trim(),
    ].join("::");
}

function buildMessagesFingerprint(messages: ChatMessage[]) {
    const safeStringify = (value: unknown) => {
        try {
            return JSON.stringify(value);
        } catch {
            return String(value ?? "");
        }
    };
    const buildNodeFingerprint = (node: PhoneUiTimelineNode) => {
        if (!node) {
            return "";
        }
        if (node.kind === "narrative") {
            return [
                node.kind,
                node.role,
                String(node.content || ""),
                String(node.agentName || ""),
            ].join("¦");
        }
        if (node.kind === "execution") {
            return [
                node.kind,
                node.executionType,
                String(node.toolCallId || ""),
                String(node.toolName || ""),
                String(node.topic || ""),
                String(node.label || ""),
                String(node.content || ""),
                safeStringify(node.result ?? node.data ?? node.args ?? null),
            ].join("¦");
        }
        if (node.kind === "governance") {
            return [
                node.kind,
                node.governanceType,
                String(node.approvalId || ""),
                String(node.question || node.reason || node.topic || ""),
                String(node.status || ""),
            ].join("¦");
        }
        if (node.kind === "artifact") {
            return [
                node.kind,
                String(node.artifact.id || ""),
                String(node.artifact.workspacePath || node.artifact.previewUrl || node.artifact.externalUrl || ""),
            ].join("¦");
        }
        return safeStringify(node);
    };

    return messages.map((message) => [
        (() => {
            const taskProgress = recordOf(message.metadata?.assistantTaskProgress);
            return [
                String(taskProgress.phase || ""),
                String(taskProgress.label || ""),
                String(taskProgress.subtitle || ""),
                String(taskProgress.currentStep || ""),
                String(taskProgress.completedCount || ""),
                String(taskProgress.totalCount || ""),
            ].join("¦");
        })(),
        String(message.id || "").trim(),
        String(message.renderKey || "").trim(),
        String(message.role || "").trim(),
        String(message.runId || "").trim(),
        String(message.content || ""),
        String(message.uiStreamPhase || ""),
        message.uiEphemeral ? "1" : "0",
        (message.images || []).join("|"),
        (message.artifacts || []).map(buildArtifactFingerprint).join("|"),
        Array.isArray(message.nodes) ? message.nodes.map(buildNodeFingerprint).join("§") : "0",
    ].join("¦")).join("¶");
}

function buildSnapshotSequence(payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) {
    const root = recordOf(payload);
    const nestedSnapshot = recordOf(root.snapshot);
    return Number(root.latestSeq || root.latest_seq || nestedSnapshot.latest_seq || 0) || 0;
}

function isOptimisticLocalMessage(message: ChatMessage) {
    const id = String(message.id || "").trim();
    return id.startsWith("user-") || id.startsWith("assistant-");
}

function buildMessageComparisonKeys(message: ChatMessage) {
    const keys = new Set<string>();
    const id = String(message.id || "").trim();
    if (id) {
        keys.add(`id:${id}`);
    }

    const role = String(message.role || "").trim();
    const clientMessageId = String(message.metadata?.clientMessageId || message.metadata?.client_message_id || "").trim();
    if (clientMessageId) {
        keys.add(`client:${role}:${clientMessageId}`);
    }
    const runId = String(message.runId || message.metadata?.runId || "").trim();
    const normalizedContent = String(message.content || "").trim().replace(/\s+/g, " ");
    if (runId) {
        keys.add(`run:${role}:${runId}`);
        if (normalizedContent) {
            keys.add(`run-content:${role}:${runId}:${normalizedContent}`);
        }
    }

    if (normalizedContent) {
        keys.add(`content:${role}:${normalizedContent}`);
    }

    const artifactKeys = (message.artifacts || [])
        .map((artifact) => buildArtifactFingerprint(artifact))
        .filter(Boolean)
        .join("|");
    if (artifactKeys) {
        keys.add(`artifacts:${role}:${artifactKeys}`);
    }

    const taskProgress = readAssistantTaskProgress(message);
    const taskProgressKey = [
        String(taskProgress.phase || "").trim(),
        String(taskProgress.currentStep || "").trim(),
        String(taskProgress.label || "").trim(),
    ].filter(Boolean).join("|");
    if (taskProgressKey) {
        keys.add(`task:${role}:${taskProgressKey}`);
    }

    return Array.from(keys);
}

function hasRenderableMessagePayload(message: ChatMessage) {
    const metadata = message.metadata && typeof message.metadata === "object"
        ? message.metadata as Record<string, unknown>
        : {};
    const composerSpecMode = (metadata.specMode === true || metadata.taskPlanningMode === true)
        && (
            metadata.taskPlanningSource === "composer"
            || metadata.taskPlanningModeSource === "composer"
            || metadata.taskPlanningRequestedByComposer === true
        );
    return Boolean(
        String(message.content || "").trim()
        || (Array.isArray(message.images) && message.images.length > 0)
        || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
        || (Array.isArray(message.nodes) && message.nodes.length > 0)
        || composerSpecMode
        || (metadata.commandPreset && typeof metadata.commandPreset === "object")
        || (Array.isArray(metadata.skillReferences) && metadata.skillReferences.length > 0)
        || (Array.isArray(metadata.contextSessionRefs) && metadata.contextSessionRefs.length > 0)
        || (Array.isArray(metadata.attachments) && metadata.attachments.length > 0)
        || countDefinedAssistantTaskProgressFields(readAssistantTaskProgress(message)) > 0,
    );
}

function hasPreservableLocalAssistantState(messages: ChatMessage[]) {
    return messages.some((message) => shouldPreserveLocalAssistantMessage(message));
}

function describeLatestAssistantMessage(messages: ChatMessage[]) {
    const latestAssistant = [...messages].reverse().find((message) => message.role === "assistant");
    if (!latestAssistant) {
        return null;
    }
    return {
        id: String(latestAssistant.id || "").trim(),
        runId: String(latestAssistant.runId || latestAssistant.metadata?.runId || "").trim(),
        phase: String(latestAssistant.uiStreamPhase || "").trim(),
        nodes: Array.isArray(latestAssistant.nodes) ? latestAssistant.nodes.length : 0,
        artifacts: Array.isArray(latestAssistant.artifacts) ? latestAssistant.artifacts.length : 0,
        contentLength: String(latestAssistant.content || "").trim().length,
    };
}


function mergeMessageImages(base: string[] = [], incoming: string[] = []) {
    return Array.from(new Set([...base, ...incoming].filter(Boolean)));
}

function mergeUserStructuredMetadata(
    snapshotMessage: ChatMessage,
    matchingLocal: ChatMessage,
    mergedMessage: ChatMessage,
) {
    if (matchingLocal.role !== "user") {
        return mergedMessage;
    }

    const localMetadata = matchingLocal.metadata && typeof matchingLocal.metadata === "object"
        ? matchingLocal.metadata as Record<string, unknown>
        : {};
    const snapshotMetadata = snapshotMessage.metadata && typeof snapshotMessage.metadata === "object"
        ? snapshotMessage.metadata as Record<string, unknown>
        : {};
    const preservedMetadata: Record<string, unknown> = {};

    for (const key of ["commandPreset", "composerPresentation", "taskPlanningSource", "clientMessageId"]) {
        if (!snapshotMetadata[key] && localMetadata[key]) preservedMetadata[key] = localMetadata[key];
    }
    for (const key of ["skillReferences", "pluginReferences", "contextMentions", "contextSessionRefs", "explicitSubagentFamilies", "attachments"]) {
        const local = localMetadata[key];
        const snapshot = snapshotMetadata[key];
        if ((!Array.isArray(snapshot) || snapshot.length === 0) && Array.isArray(local) && local.length > 0) {
            preservedMetadata[key] = local;
        }
    }
    for (const key of ["taskPlanningMode", "specMode", "taskPlanningRequestedByComposer"]) {
        if (snapshotMetadata[key] !== true && localMetadata[key] === true) preservedMetadata[key] = true;
    }

    if (Object.keys(preservedMetadata).length === 0) {
        return mergedMessage;
    }

    return {
        ...mergedMessage,
        metadata: {
            ...(mergedMessage.metadata || {}),
            ...preservedMetadata,
        },
        images: mergeMessageImages(matchingLocal.images || [], mergedMessage.images || []),
        artifacts: mergeArtifacts(matchingLocal.artifacts || [], mergedMessage.artifacts || []),
    };
}

function shouldPreserveAssistantPlaceholder(
    snapshotMessage: ChatMessage,
    matchingLocal: ChatMessage,
) {
    if (matchingLocal.role !== "assistant") {
        return false;
    }
    if (!matchingLocal.uiEphemeral && !isActiveAssistantStreamPhase(matchingLocal.uiStreamPhase)) {
        return false;
    }
    const snapshotRenderable = hasRenderableMessagePayload(snapshotMessage);
    return !snapshotRenderable;
}

function mergeAssistantPlaceholder(
    snapshotMessage: ChatMessage,
    matchingLocal: ChatMessage,
    mergedMessage: ChatMessage,
) {
    const localTaskProgress = readAssistantTaskProgress(matchingLocal);
    const nextMessage: ChatMessage = {
        ...mergedMessage,
        runId: matchingLocal.runId || mergedMessage.runId,
        uiEphemeral: true,
        uiStreamPhase: matchingLocal.uiStreamPhase || mergedMessage.uiStreamPhase,
        metadata: {
            ...(matchingLocal.metadata || {}),
            ...(mergedMessage.metadata || {}),
        },
        images: mergeMessageImages(matchingLocal.images || [], mergedMessage.images || []),
        artifacts: mergeArtifacts(matchingLocal.artifacts || [], mergedMessage.artifacts || []),
        nodes: mergeTimelineNodes(mergedMessage.nodes || [], matchingLocal.nodes || []),
    };
    if (String(matchingLocal.content || "").trim().length > String(nextMessage.content || "").trim().length) {
        nextMessage.content = matchingLocal.content;
    }
    if (countDefinedAssistantTaskProgressFields(localTaskProgress) > 0) {
        nextMessage.metadata = {
            ...(nextMessage.metadata || {}),
            assistantTaskProgress: localTaskProgress,
        };
    }
    return nextMessage;
}

function mergeStructuredSnapshotMessages(
    current: ChatMessage[],
    snapshotMessages: ChatMessage[],
) {
    const normalizedSnapshot = normalizeMessagesForState(snapshotMessages);
    if (current.length === 0) {
        return normalizedSnapshot;
    }

    const currentByKey = new Map<string, ChatMessage>();
    current.forEach((message) => {
        buildMessageComparisonKeys(message).forEach((key) => {
            if (!currentByKey.has(key)) {
                currentByKey.set(key, message);
            }
        });
    });

    const matchedLocal = new Set<ChatMessage>();
    const mergedSnapshot = normalizedSnapshot.map((snapshotMessage) => {
        const matchingLocal = buildMessageComparisonKeys(snapshotMessage)
            .map((key) => currentByKey.get(key))
            .find(Boolean);
        if (!matchingLocal) {
            return snapshotMessage;
        }
        matchedLocal.add(matchingLocal);
        const snapshotTranscriptVersion = Number((snapshotMessage.metadata || {}).transcriptVersion || 0);
        const snapshotCanonical = snapshotTranscriptVersion > 0 || (snapshotMessage.nodes?.length || 0) > 0;
        if (snapshotCanonical) {
            const canonicalSnapshot = normalizeMessagesForState([snapshotMessage])[0] || snapshotMessage;
            if (shouldPreserveAssistantPlaceholder(snapshotMessage, matchingLocal)) {
                return mergeAssistantPlaceholder(snapshotMessage, matchingLocal, canonicalSnapshot);
            }
            return mergeUserStructuredMetadata(snapshotMessage, matchingLocal, canonicalSnapshot);
        }

        const localHasStructuredState = hasStructuredAssistantPayload(matchingLocal);
        const snapshotHasStructuredState = hasStructuredAssistantPayload(snapshotMessage);
        const snapshotRenderable = hasRenderableMessagePayload(snapshotMessage);
        const snapshotAuthoritativeAssistant = snapshotMessage.role === "assistant" && snapshotRenderable && snapshotHasStructuredState;
        const mergedMessage = snapshotAuthoritativeAssistant
            ? (normalizeMessagesForState([snapshotMessage])[0] || snapshotMessage)
            : (normalizeMessagesForState([snapshotMessage, matchingLocal])[0] || snapshotMessage);
        if (snapshotAuthoritativeAssistant && matchingLocal.role === "assistant") {
            mergedMessage.metadata = {
                ...(matchingLocal.metadata || {}),
                ...(mergedMessage.metadata || {}),
            };
            if ((!mergedMessage.images || mergedMessage.images.length === 0) && matchingLocal.images?.length) {
                mergedMessage.images = mergeMessageImages(matchingLocal.images || [], snapshotMessage.images || []);
            }
            if ((!mergedMessage.artifacts || mergedMessage.artifacts.length === 0) && matchingLocal.artifacts?.length) {
                mergedMessage.artifacts = mergeArtifacts(matchingLocal.artifacts || [], snapshotMessage.artifacts || []);
            }
            if ((!mergedMessage.toolInvocations || mergedMessage.toolInvocations.length === 0) && matchingLocal.toolInvocations?.length) {
                mergedMessage.toolInvocations = matchingLocal.toolInvocations;
            }
        }
        if (matchingLocal.role === "assistant" && localHasStructuredState && !snapshotHasStructuredState) {
            mergedMessage.nodes = (matchingLocal.nodes?.length || 0) >= (snapshotMessage.nodes?.length || 0)
                ? matchingLocal.nodes
                : snapshotMessage.nodes;
            mergedMessage.artifacts = (matchingLocal.artifacts?.length || 0) >= (snapshotMessage.artifacts?.length || 0)
                ? matchingLocal.artifacts
                : snapshotMessage.artifacts;
            mergedMessage.images = mergeMessageImages(matchingLocal.images || [], snapshotMessage.images || []);
            if (matchingLocal.toolInvocations?.length) {
                mergedMessage.toolInvocations = matchingLocal.toolInvocations;
            }
        }

        if (shouldPreserveAssistantPlaceholder(snapshotMessage, matchingLocal)) {
            return mergeAssistantPlaceholder(snapshotMessage, matchingLocal, mergedMessage);
        }
        return mergeUserStructuredMetadata(snapshotMessage, matchingLocal, mergedMessage);
    });
    const retainedHistory = current.filter((message) => !matchedLocal.has(message));
    return normalizeMessagesForState([...retainedHistory, ...mergedSnapshot]);
}

function mergeAuthoritativeSnapshotMessages(
    current: ChatMessage[],
    snapshotMessages: ChatMessage[],
    preserveOptimisticLocalState: boolean,
) {
    const normalizedSnapshot = normalizeMessagesForState(snapshotMessages);
    if (!preserveOptimisticLocalState) {
        return mergeStructuredSnapshotMessages(current, normalizedSnapshot);
    }

    const preservableLocals = current.filter((message) =>
        isOptimisticLocalMessage(message)
        || shouldPreserveLocalAssistantMessage(message),
    );
    if (preservableLocals.length === 0) {
        return normalizedSnapshot;
    }

    const usedPreservableLocals = new Set<string>();
    const mergedSnapshotMessages = normalizedSnapshot.map((snapshotMessage) => {
        const snapshotKeys = new Set(buildMessageComparisonKeys(snapshotMessage));
        const matchingLocal = preservableLocals.find((candidate) => {
            const localId = String(candidate.id || "").trim();
            if (localId && usedPreservableLocals.has(localId)) {
                return false;
            }
            if (
                candidate.role === "assistant"
                && snapshotMessage.role === "assistant"
                && candidate.runId
                && snapshotMessage.runId
                && candidate.runId === snapshotMessage.runId
            ) {
                return true;
            }
            return buildMessageComparisonKeys(candidate).some((key) => snapshotKeys.has(key));
        });

        if (!matchingLocal) {
            return snapshotMessage;
        }
        const matchingLocalId = String(matchingLocal.id || "").trim();
        if (matchingLocalId) {
            usedPreservableLocals.add(matchingLocalId);
        }
        const snapshotTranscriptVersion = Number((snapshotMessage.metadata || {}).transcriptVersion || 0);
        const snapshotCanonical = snapshotTranscriptVersion > 0 || (snapshotMessage.nodes?.length || 0) > 0;
        if (snapshotCanonical) {
            if (shouldPreserveAssistantPlaceholder(snapshotMessage, matchingLocal)) {
                return mergeAssistantPlaceholder(snapshotMessage, matchingLocal, snapshotMessage);
            }
            return mergeUserStructuredMetadata(snapshotMessage, matchingLocal, snapshotMessage);
        }

        const localStreamActive = matchingLocal.role === "assistant" && isActiveAssistantStreamPhase(matchingLocal.uiStreamPhase);
        const snapshotRenderable = hasRenderableMessagePayload(snapshotMessage);
        const localRenderable = hasRenderableMessagePayload(matchingLocal);
        const localTaskProgress = readAssistantTaskProgress(matchingLocal);
        const snapshotTaskProgress = readAssistantTaskProgress(snapshotMessage);
        const localHasStructuredState = hasStructuredAssistantPayload(matchingLocal);
        const snapshotHasStructuredState = hasStructuredAssistantPayload(snapshotMessage);
        const snapshotAuthoritativeAssistant = snapshotMessage.role === "assistant" && snapshotRenderable && snapshotHasStructuredState;
        const mergedMessage = snapshotAuthoritativeAssistant
            ? (normalizeMessagesForState([snapshotMessage])[0] || snapshotMessage)
            : (normalizeMessagesForState([snapshotMessage, matchingLocal])[0] || snapshotMessage);
        const localTaskProgressRicher = countDefinedAssistantTaskProgressFields(localTaskProgress) > countDefinedAssistantTaskProgressFields(snapshotTaskProgress);
        const shouldMergeLocalStructuredState = localHasStructuredState
            && localStreamActive
            && (!snapshotRenderable || !snapshotHasStructuredState);

        if (snapshotAuthoritativeAssistant) {
            mergedMessage.metadata = {
                ...(matchingLocal.metadata || {}),
                ...(mergedMessage.metadata || {}),
            };
            if ((!mergedMessage.images || mergedMessage.images.length === 0) && matchingLocal.images?.length) {
                mergedMessage.images = mergeMessageImages(matchingLocal.images || [], snapshotMessage.images || []);
            }
            if ((!mergedMessage.artifacts || mergedMessage.artifacts.length === 0) && matchingLocal.artifacts?.length) {
                mergedMessage.artifacts = mergeArtifacts(matchingLocal.artifacts || [], snapshotMessage.artifacts || []);
            }
            if ((!mergedMessage.toolInvocations || mergedMessage.toolInvocations.length === 0) && matchingLocal.toolInvocations?.length) {
                mergedMessage.toolInvocations = matchingLocal.toolInvocations;
            }
        }

        if (matchingLocal.role === "assistant" && matchingLocal.uiEphemeral) {
            mergedMessage.uiEphemeral = !snapshotRenderable || localStreamActive;
            mergedMessage.uiStreamPhase = localStreamActive
                ? matchingLocal.uiStreamPhase
                : snapshotMessage.uiStreamPhase;
        }

        if (matchingLocal.role === "assistant"
            && countDefinedAssistantTaskProgressFields(localTaskProgress) > 0
            && (localStreamActive || !snapshotRenderable || !snapshotHasStructuredState || localTaskProgressRicher)) {
            mergedMessage.metadata = {
                ...(mergedMessage.metadata || {}),
                assistantTaskProgress: localTaskProgress,
            };
        }

        if (matchingLocal.role === "assistant" && (localStreamActive || shouldMergeLocalStructuredState)) {
            if (localStreamActive && !snapshotRenderable
                && String(matchingLocal.content || "").trim().length > String(mergedMessage.content || "").trim().length) {
                mergedMessage.content = matchingLocal.content;
            }
            if (shouldMergeLocalStructuredState) {
                mergedMessage.nodes = mergeTimelineNodes(mergedMessage.nodes || [], matchingLocal.nodes || []);
                mergedMessage.images = mergeMessageImages(mergedMessage.images || [], matchingLocal.images || []);
                mergedMessage.artifacts = mergeArtifacts(mergedMessage.artifacts || [], matchingLocal.artifacts || []);
                if ((!mergedMessage.toolInvocations || mergedMessage.toolInvocations.length === 0) && matchingLocal.toolInvocations?.length) {
                    mergedMessage.toolInvocations = matchingLocal.toolInvocations;
                }
            }
            if ((!snapshotRenderable || localStreamActive || !snapshotHasStructuredState) && matchingLocal.uiEphemeral) {
                mergedMessage.uiEphemeral = true;
            }
            if (localStreamActive && matchingLocal.uiStreamPhase) {
                mergedMessage.uiStreamPhase = matchingLocal.uiStreamPhase;
            }
        }

        if (shouldPreserveAssistantPlaceholder(snapshotMessage, matchingLocal)) {
            return mergeAssistantPlaceholder(snapshotMessage, matchingLocal, mergedMessage);
        }
        return mergeUserStructuredMetadata(snapshotMessage, matchingLocal, mergedMessage);
    });

    const unmatchedOptimisticLocals = preservableLocals.filter((message) => {
        const messageId = String(message.id || "").trim();
        if (messageId && usedPreservableLocals.has(messageId)) {
            return false;
        }
        const comparisonKeys = buildMessageComparisonKeys(message);
        return !mergedSnapshotMessages.some((snapshotMessage) => {
            const snapshotKeys = new Set(buildMessageComparisonKeys(snapshotMessage));
            return comparisonKeys.some((key) => snapshotKeys.has(key));
        });
    });

    return normalizeMessagesForState([...mergedSnapshotMessages, ...unmatchedOptimisticLocals]);
}

function extractSnapshotMessages(payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) {
    const root = recordOf(payload);
    const snapshot = recordOf(root.snapshot);
    const messageCandidates = [root.timeline, snapshot.timeline, root.messages, snapshot.messages];
    for (const candidate of messageCandidates) {
        if (Array.isArray(candidate) && candidate.length > 0) {
            return candidate.filter((item): item is ChatMessage => Boolean(item) && typeof item === "object");
        }
    }
    return null;
}

function extractQueuedMessages(payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined): QueuedChatMessage[] | null {
    const root = recordOf(payload);
    const snapshot = recordOf(root.snapshot);
    const candidates = [root.queuedMessages, snapshot.queuedMessages];
    for (const candidate of candidates) {
        if (Array.isArray(candidate)) {
            return candidate
                .filter((item): item is QueuedChatMessage => Boolean(item) && typeof item === "object")
                .filter((item) => String(item.id || "").trim());
        }
    }
    return null;
}


export {
    TODO_TOOL_NAMES,
    applyAssistantTaskProgressPatch,
    applyTodoToolEvent,
    buildAssistantPlaceholder,
    buildAssistantTaskProgressPatch,
    buildMessagesFingerprint,
    buildSnapshotSequence,
    describeLatestAssistantMessage,
    extractQueuedMessages,
    extractSnapshotMessages,
    findLatestAssistantShellIndex,
    hasPreservableLocalAssistantState,
    mergeAuthoritativeSnapshotMessages,
};

export type { AssistantTaskProgressPatch };
