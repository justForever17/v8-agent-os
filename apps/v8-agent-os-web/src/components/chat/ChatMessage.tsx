"use client";
/* eslint-disable @next/next/no-img-element */

import { User, Copy, Trash2, Check, TerminalSquare, ChevronDown, Orbit, AtSign, FileText, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState, memo, useMemo, useCallback } from "react";
import { groupTimelineNodes, type TimelineSegment } from "@/lib/chat/timeline-grouper";
import { motion } from "framer-motion";
import {
    buildCollaborationMicroStages,
    coerceAdminResourceRef,
    isClientAudioAttachment,
    isClientVisualAttachment,
    isRuntimeEpisodeGraphActivity,
    resolveAdminResourceUrl,
    type AdminProcessRef,
    type CollaborationMicroStageActivityInput,
} from "@v8/session-realtime";
import {
    buildComposerInlineSegments,
    type ComposerPresentation,
} from "@v8/session-realtime/composer-inline-references";
import {
    buildCollaborationMicroStagesFromMessageBoundNodes,
    buildMessageBoundCollaborationMicroStagePlacement,
    buildMessageBoundExecutionNodes,
    getMessageBoundExecutionTimelineNodeIdentityCandidates,
    type MessageBoundExecutionMessage,
} from "@v8/session-realtime/message-bound-execution-node";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { Message, UiExecutionNode, UiTimelineNode } from "@/store/chat-types";
import { ContentDispatcher } from "./ContentDispatcher";
import { cn } from "@/lib/utils";
import { MediaViewerLightbox, MediaItem } from "./MediaViewerLightbox";
import { ArtifactCard } from "./ArtifactCard";
import { inferArtifactCardType, resolveRuntimeArtifactUrl } from "@/lib/artifacts";
import { downloadArtifact } from "@/lib/artifact-download";
import { createArtifactDocument, createSessionOverviewDocument } from "@/lib/workbench";
import { useWorkbenchStore } from "@/store/workbench-store";
import { useT } from "@/components/providers/LocaleProvider";
import { parseContentToBlocks } from "@/lib/chat/content-detector";
import { CollaborationMicroStageScene, type CollaborationMicroStageDetailTarget } from "./collaboration/CollaborationMicroStageScene";
import type { RuntimeStageActivity } from "@/lib/runtime-stage";
import { DEFAULT_AVATAR } from "@/lib/chat-stream-state";
import {
    isCreativeCanvasCanonicalMessage,
} from "@/lib/creative-canvas-task-contract";

interface ChatMessageProps {
    message: Message;
    processes?: AdminProcessRef[];
    isLoading?: boolean;
    onDelete: (id: string) => void;
    isLast?: boolean;
    userAvatar?: string | null;
    userName?: string | null;
    supervisorProfile?: { name: string; roleLabel: string; avatar: string } | null;
    runtimeActivities?: RuntimeStageActivity[];
    executionActive?: boolean;
    animateEntrance?: boolean;
}

interface MessageActionButtonsProps {
    copied: boolean;
    onCopy: () => void;
    onDelete: () => void;
    copyLabel: string;
    deleteLabel: string;
    className?: string;
}

type SkillReferenceMetadata = {
    name: string;
    description?: string;
    path?: string;
};

type MentionReferenceMetadata = {
    key: string;
    kind: "subagent_family" | "plugin";
    label: string;
    description?: string;
};

function isExecutionNode(node: UiTimelineNode): node is UiExecutionNode {
    return node.kind === "execution";
}

const MICRO_STAGE_ACTIVITY_LIMIT = 80;

type ChatTimelineRenderSegment = TimelineSegment | {
    kind: "collaboration_stage";
    id: string;
};

function hasToolCallId(node: UiTimelineNode): node is UiExecutionNode & { toolCallId: string } {
    return isExecutionNode(node) && typeof node.toolCallId === "string" && node.toolCallId.trim().length > 0;
}

function toMicroStageActivityInput(activity: RuntimeStageActivity): CollaborationMicroStageActivityInput {
    const data = activity.node.kind === "execution" && activity.node.data && typeof activity.node.data === "object"
        ? activity.node.data as Record<string, unknown>
        : {};
    return {
        id: activity.id,
        topic: activity.topic,
        summary: activity.summary,
        timestamp: activity.timestamp,
        runtimeId: activity.runtimeId,
        data: {
            ...data,
            runId: activity.node.runId || data.runId || data.run_id || activity.messageId,
        },
    };
}

function getExecutionTopic(node: UiExecutionNode) {
    return String(node.topic || node.data?.topic || "").trim().toLowerCase();
}

function getExecutionToolName(node: UiExecutionNode) {
    return String(node.toolName || node.data?.toolName || node.data?.tool_name || "").trim().toLowerCase();
}

function hasArtifactProducingTool(segment: Extract<TimelineSegment, { kind: "trace_group" }>) {
    return segment.nodes.some((node) => {
        if (!isExecutionNode(node) || (node.executionType !== "tool_call" && node.executionType !== "tool_result")) {
            return false;
        }
        const toolName = getExecutionToolName(node);
        return toolName === "write_native_file"
            || toolName === "apply_patch"
            || toolName === "write_file"
            || Boolean(node.data?.artifact || node.data?.artifactRef || node.data?.resourceRef);
    });
}

function isMicroStageSupersededTimelineNode(node: UiTimelineNode) {
    if (!isExecutionNode(node)) {
        return false;
    }

    const topic = getExecutionTopic(node);
    if (node.executionType === "runtime_progress") {
        return topic.startsWith("runtime.episode.")
            || topic.startsWith("handoff.ref.")
            || topic.startsWith("subagent.task.")
            || topic.startsWith("delegation.")
            || topic.startsWith("delegation_broker.");
    }

    return false;
}

function isRenderableTimelineNode(node: UiTimelineNode, isStreaming: boolean) {
    if (node.kind === "narrative") {
        return String(node.content || "").trim().length > 0;
    }

    if (node.kind === "execution") {
        if (node.executionType === "reasoning") {
            return isStreaming
                || String(node.content || "").trim().length > 0
                || Number(node.time || 0) > 0
                || Boolean(node.reasoningKind || node.data?.reasoningKind);
        }
        if (node.executionType === "tool_call" || node.executionType === "tool_result") {
            const toolName = getExecutionToolName(node);
            return toolName !== "write_todos"
                && toolName !== "update_todo"
                && toolName !== "ask_user";
        }
        if (node.executionType === "runtime_progress") {
            return Boolean(String(node.label || node.topic || "").trim());
        }
        return true;
    }

    if (node.kind === "governance" && node.governanceType === "ask_user") {
        return false;
    }

    return true;
}

function findMicroStageAnchorIndex(nodes: UiTimelineNode[], anchorNodeId?: string) {
    if (!anchorNodeId) {
        return nodes.length;
    }
    const index = nodes.findIndex((node) => (
        getMessageBoundExecutionTimelineNodeIdentityCandidates(node).includes(anchorNodeId)
    ));
    return index >= 0 ? index : nodes.length;
}

function extractSupervisorMicroStageSpeech(nodes: UiTimelineNode[], anchorIndex: number) {
    const orderedCandidates = [
        ...nodes.slice(0, anchorIndex).reverse(),
        ...nodes.slice(anchorIndex),
    ];
    for (const node of orderedCandidates) {
        if (node.kind !== "narrative" || node.role !== "assistant") {
            continue;
        }
        const text = parseContentToBlocks(String(node.content || ""), false, 0, false)
            .filter((block) => block.type !== "voice")
            .map((block) => block.content.trim())
            .filter(Boolean)
            .join(" ")
            .replace(/\s+/g, " ")
            .trim();
        if (!text) {
            continue;
        }
        const sentence = text.match(/^.*?[。！？!?](?:\s|$)/)?.[0]?.trim() || text;
        return sentence.length > 64 ? `${sentence.slice(0, 63)}…` : sentence;
    }
    return "";
}

function extractCommandPresetName(message: Message): string | null {
    const commandPreset = message.metadata?.commandPreset;
    if (!commandPreset || typeof commandPreset !== "object") {
        return null;
    }
    const name = (commandPreset as Record<string, unknown>).name;
    return typeof name === "string" && name.trim() ? name.trim() : null;
}

function hasSpecMode(message: Message): boolean {
    return Boolean(message.metadata?.specMode || message.metadata?.taskPlanningMode);
}

function extractSkillReferences(message: Message): SkillReferenceMetadata[] {
    const raw = message.metadata?.skillReferences;
    if (!Array.isArray(raw)) {
        return [];
    }
    const seen = new Set<string>();
    const normalized: SkillReferenceMetadata[] = [];
    for (const item of raw) {
        if (!item || typeof item !== "object") {
            continue;
        }
        const record = item as Record<string, unknown>;
        const name = typeof record.name === "string" ? record.name.trim() : "";
        const description = typeof record.description === "string" ? record.description.trim() : "";
        const path = typeof record.path === "string" ? record.path.trim() : "";
        if (!name && !path) {
            continue;
        }
        const key = `${name.toLowerCase()}::${path.toLowerCase()}`;
        if (seen.has(key)) {
            continue;
        }
        seen.add(key);
        normalized.push({ name: name || path, description, path });
    }
    return normalized;
}

function extractMentionReferences(message: Message): MentionReferenceMetadata[] {
    const normalized: MentionReferenceMetadata[] = [];
    const seen = new Set<string>();
    const contextMentions = message.metadata?.contextMentions;
    if (Array.isArray(contextMentions)) {
        for (const item of contextMentions) {
            if (!item || typeof item !== "object") continue;
            const record = item as Record<string, unknown>;
            if (record.kind !== "subagent_family") continue;
            const id = typeof record.familyId === "string" ? record.familyId.trim() : typeof record.id === "string" ? record.id.trim() : "";
            const label = typeof record.label === "string" ? record.label.trim() : typeof record.name === "string" ? record.name.trim() : id;
            if (!label) continue;
            const key = `subagent_family:${id || label.toLowerCase()}`;
            if (seen.has(key)) continue;
            seen.add(key);
            normalized.push({
                key,
                kind: "subagent_family",
                label,
                description: typeof record.description === "string" ? record.description.trim() : "",
            });
        }
    }
    const pluginReferences = message.metadata?.pluginReferences;
    if (Array.isArray(pluginReferences)) {
        for (const item of pluginReferences) {
            if (!item || typeof item !== "object") continue;
            const record = item as Record<string, unknown>;
            const id = typeof record.pluginId === "string" ? record.pluginId.trim() : "";
            const label = typeof record.name === "string" ? record.name.trim() : typeof record.displayName === "string" ? record.displayName.trim() : id;
            if (!label) continue;
            const key = `plugin:${id || label.toLowerCase()}`;
            if (seen.has(key)) continue;
            seen.add(key);
            normalized.push({ key, kind: "plugin", label });
        }
    }
    return normalized;
}

function extractContextSessionRefs(message: Message): string[] {
    const raw = message.metadata?.contextSessionRefs;
    if (!Array.isArray(raw)) {
        return [];
    }
    return Array.from(new Set(
        raw
            .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
            .map((item) => typeof item.sessionId === "string" ? item.sessionId.trim() : "")
            .filter(Boolean),
    )).slice(0, 3);
}

function extractComposerPresentation(message: Message): ComposerPresentation | null {
    const raw = message.metadata?.composerPresentation;
    if (!raw || typeof raw !== "object") return null;
    const record = raw as Record<string, unknown>;
    const text = typeof record.text === "string" ? record.text : "";
    const rawReferences = Array.isArray(record.references) ? record.references : [];
    const references = rawReferences.flatMap((item) => {
        if (!item || typeof item !== "object") return [];
        const reference = item as Record<string, unknown>;
        const kind = String(reference.kind || "").trim();
        const id = String(reference.id || "").trim();
        const label = String(reference.label || "").trim();
        if (!id || !label || !["command", "skill", "subagent_family", "plugin", "canvas_resource"].includes(kind)) return [];
        return [{ kind: kind as ComposerPresentation["references"][number]["kind"], id, label }];
    });
    return text ? { text, references } : null;
}

const LOOPBACK_WORKSPACE_URL_PATTERN = /https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::9530)?\/workspace\/([^\s)]+)/gi;

function normalizeWorkspaceLinks(content: string): string {
    return content.replace(LOOPBACK_WORKSPACE_URL_PATTERN, (_match, subpath: string) => {
        const cleaned = String(subpath || "").replace(/^\/+/, "");
        return `/api/workspace/files/${cleaned}`;
    });
}

type MessageAttachmentRecord = {
    url: string;
    name: string;
    mimeType: string;
    mediaKind: string;
};

function resolveAttachmentUrl(value: unknown) {
    const raw = String(value || "").trim();
    if (!raw) {
        return "";
    }
    return resolveAdminResourceUrl("web", undefined, coerceAdminResourceRef(raw)) || raw.replace(/^\/api\/client\b/i, "/api");
}

function attachmentNameFromUrl(value: string) {
    try {
        const parsed = new URL(value, "http://v8os.local");
        const path = parsed.searchParams.get("workspace_relative_path")
            || parsed.searchParams.get("path")
            || parsed.pathname;
        const name = decodeURIComponent(String(path || "").split("/").filter(Boolean).at(-1) || "");
        return name || "attachment";
    } catch {
        return String(value || "").split(/[\\/]/).filter(Boolean).at(-1) || "attachment";
    }
}

function extractMessageAttachments(message: Message): MessageAttachmentRecord[] {
    const metadata = message.metadata && typeof message.metadata === "object" ? message.metadata : {};
    const rawAttachments = Array.isArray(metadata.attachments) ? metadata.attachments : [];
    const structured = rawAttachments
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
        .map((item) => {
            const rawUrl = item.publicUrl || item.public_url || item.url || item.workspacePath || item.workspace_path || item.resourceRef;
            return {
                url: resolveAttachmentUrl(rawUrl),
                name: String(item.name || item.filename || item.displayName || "attachment").trim(),
                mimeType: String(item.mimeType || item.mime_type || item.type || "").toLowerCase(),
                mediaKind: String(item.mediaKind || item.previewKind || item.kind || "").toLowerCase(),
            };
        })
        .filter((item) => item.url);
    const knownUrls = new Set(structured.map((item) => item.url.toLowerCase()));
    const compatibilityFiles = (Array.isArray(message.images) ? message.images : [])
        .map(resolveAttachmentUrl)
        .filter((url) => Boolean(url) && !knownUrls.has(url.toLowerCase()))
        .filter((url) => !isClientVisualAttachment({ url }))
        .map((url) => ({
            url,
            name: attachmentNameFromUrl(url),
            mimeType: "",
            mediaKind: isClientAudioAttachment({ url }) ? "audio" : "file",
        }));
    return [...structured, ...compatibilityFiles];
}

function isAudioAttachmentRecord(item: MessageAttachmentRecord) {
    return isClientAudioAttachment(item);
}

function isVisualAttachmentRecord(item: MessageAttachmentRecord) {
    return isClientVisualAttachment(item);
}

function MessageActionButtons({
    copied,
    onCopy,
    onDelete,
    copyLabel,
    deleteLabel,
    className,
}: MessageActionButtonsProps) {
    return (
        <div className={cn("flex items-center justify-end gap-2", className)}>
            <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 rounded-none border-0 bg-transparent p-0 text-muted-foreground/70 shadow-none hover:bg-transparent hover:text-foreground"
                onClick={onCopy}
                aria-label={copyLabel}
                title={copyLabel}
            >
                {copied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
            </Button>
            <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 rounded-none border-0 bg-transparent p-0 text-muted-foreground/70 shadow-none hover:bg-transparent hover:text-destructive"
                onClick={onDelete}
                aria-label={deleteLabel}
                title={deleteLabel}
            >
                <Trash2 className="h-3.5 w-3.5" />
            </Button>
        </div>
    );
}

function AssistantActivityDots({ label }: { label: string }) {
    return (
        <div
            className="flex min-h-6 min-w-12 items-center justify-center gap-1.5"
            role="status"
            aria-label={label}
        >
            {[0, 1, 2].map((index) => (
                <span
                    key={index}
                    className="size-1.5 rounded-full bg-primary/80 motion-safe:animate-bounce motion-reduce:animate-none"
                    style={{ animationDelay: `${index * 140}ms`, animationDuration: "920ms" }}
                />
            ))}
        </div>
    );
}

function ChatMessageComponent({ message, processes = [], isLoading, onDelete, isLast, userAvatar, userName, supervisorProfile, runtimeActivities = [], executionActive = false, animateEntrance = false }: ChatMessageProps) {
    const t = useT();
    const [isCopied, setIsCopied] = useState(false);
    const workbenchSessionId = useWorkbenchStore((state) => state.sessionId);
    const openWorkbenchDocument = useWorkbenchStore((state) => state.openDocument);
    const commandPresetName = useMemo(() => extractCommandPresetName(message), [message]);
    const specModeEnabled = useMemo(() => hasSpecMode(message), [message]);
    const skillReferences = useMemo(() => extractSkillReferences(message), [message]);
    const mentionReferences = useMemo(() => extractMentionReferences(message), [message]);
    const contextSessionRefs = useMemo(() => extractContextSessionRefs(message), [message]);
    const canvasHumanSurface = useMemo(
        () => message.role === "user" && isCreativeCanvasCanonicalMessage(message.content, message.metadata),
        [message.content, message.metadata, message.role],
    );
    const composerPresentation = useMemo(
        () => canvasHumanSurface
            ? { text: t("web.workbench.canvas.humanMessage"), references: [] }
            : extractComposerPresentation(message),
        [canvasHumanSurface, message, t],
    );
    const composerPresentationSegments = useMemo(
        () => composerPresentation ? buildComposerInlineSegments(composerPresentation.text, composerPresentation.references) : [],
        [composerPresentation],
    );
    const shouldRenderUserMetadata = !canvasHumanSurface && Boolean(
        contextSessionRefs.length > 0
        || specModeEnabled
        || (!composerPresentation && (commandPresetName || skillReferences.length > 0 || mentionReferences.length > 0)),
    );
    const normalizedContent = useMemo(() => normalizeWorkspaceLinks(message.content || ""), [message.content]);
    const copyLabel = t("web.generated.8095bb5671");
    const deleteLabel = t("web.generated.2f98d36496");
    const userDisplayName = String(userName || "").trim() || t("web.generated.d2ccadbbf7");
    const attachmentRecords = useMemo(
        () => canvasHumanSurface ? [] : extractMessageAttachments(message),
        [canvasHumanSurface, message],
    );
    const audioAttachments = useMemo(
        () => attachmentRecords.filter(isAudioAttachmentRecord),
        [attachmentRecords],
    );
    const fileAttachments = useMemo(
        () => attachmentRecords.filter((item) => !isAudioAttachmentRecord(item) && !isVisualAttachmentRecord(item)),
        [attachmentRecords],
    );
    const visualAttachmentUrls = useMemo(
        () => attachmentRecords.filter((item) => !isAudioAttachmentRecord(item) && isVisualAttachmentRecord(item)).map((item) => item.url),
        [attachmentRecords],
    );
    const nonVisualAttachmentUrls = useMemo(
        () => new Set([...audioAttachments, ...fileAttachments].map((item) => item.url.toLowerCase())),
        [audioAttachments, fileAttachments],
    );

    const handleCopy = (content: string) => {
        navigator.clipboard.writeText(content);
        setIsCopied(true);
        setTimeout(() => setIsCopied(false), 2000);
    };

    // Lightbox State
    const [viewerOpen, setViewerOpen] = useState(false);
    const [viewerStartingIndex, setViewerStartingIndex] = useState(0);

    // Prepare media items for Lightbox (if message has images)
    const imagesArray = useMemo(
        () => canvasHumanSurface ? [] : Array.from(new Set([
            ...(Array.isArray(message.images) ? message.images : []),
            ...visualAttachmentUrls,
        ]
            .map((value) => {
                const raw = String(value || "").trim();
                if (!raw) {
                    return "";
                }
                return resolveAdminResourceUrl("web", undefined, coerceAdminResourceRef(raw)) || raw.replace(/^\/api\/client\b/i, "/api");
            })
            .filter((value) => (
                Boolean(value)
                && !nonVisualAttachmentUrls.has(value.toLowerCase())
                && isClientVisualAttachment({ url: value })
            )))),
        [canvasHumanSurface, message.images, nonVisualAttachmentUrls, visualAttachmentUrls],
    );
    const mediaItems: MediaItem[] = useMemo(() => {
        return imagesArray.map((url) => {
            const isVid = url.match(/\.(mp4|webm|mov)$/i);
            return {
                type: isVid ? 'video' : 'image',
                src: url,
                name: url.split('/').pop() || 'media'
            };
        });
    }, [imagesArray]);
    const toolCallIds = useMemo(() => {
        return new Set(
            (message.nodes || [])
                .filter((node): node is UiExecutionNode & { toolCallId: string } => hasToolCallId(node) && node.executionType === 'tool_call')
                .map((node) => node.toolCallId.trim())
                .filter(Boolean),
        );
    }, [message.nodes]);
    const resultNodesByToolCallId = useMemo(() => {
        const mapping = new Map<string, UiExecutionNode>();
        for (const node of message.nodes || []) {
            if (hasToolCallId(node) && node.executionType === 'tool_result') {
                mapping.set(node.toolCallId.trim(), node);
            }
        }
        return mapping;
    }, [message.nodes]);
    const bubbleExecutionActivities = useMemo(
        () => (message.role !== "user" && message.role !== "tool" && isLast
            ? runtimeActivities
                .filter((activity) => isRuntimeEpisodeGraphActivity({ topic: activity.topic }))
                .slice(0, MICRO_STAGE_ACTIVITY_LIMIT)
            : []),
        [isLast, message.role, runtimeActivities],
    );
    const messageBoundExecutionNodes = useMemo(
        () => buildMessageBoundExecutionNodes([message as unknown as MessageBoundExecutionMessage]),
        [message],
    );
    const messageBoundMicroStagePlacement = useMemo(
        () => buildMessageBoundCollaborationMicroStagePlacement(
            messageBoundExecutionNodes,
            {
                runId: message.runId,
                locale: "zh-CN",
                limit: 10,
                maxStepsPerStage: 4,
            },
        ),
        [message.runId, messageBoundExecutionNodes],
    );
    const messageBoundMicroStages = useMemo(
        () => buildCollaborationMicroStagesFromMessageBoundNodes(messageBoundExecutionNodes, {
            runId: message.runId,
            locale: "zh-CN",
            limit: 10,
            maxStepsPerStage: 4,
        }),
        [message.runId, messageBoundExecutionNodes],
    );
    const liveFallbackMicroStages = useMemo(
        () => buildCollaborationMicroStages(
            bubbleExecutionActivities.map(toMicroStageActivityInput),
            {
                runId: message.runId,
                locale: "zh-CN",
                limit: 10,
                maxStepsPerStage: 4,
            },
        ),
        [bubbleExecutionActivities, message.runId],
    );
    const visibleBubbleMicroStages = liveFallbackMicroStages.length
        ? liveFallbackMicroStages
        : messageBoundMicroStages;
    const microStageSceneKey = `collaboration-stage:${message.runId || visibleBubbleMicroStages[0]?.id || message.id}`;
    const microStageAnchorIndex = useMemo(
        () => findMicroStageAnchorIndex(
            message.nodes || [],
            messageBoundMicroStagePlacement?.anchorNodeId,
        ),
        [message.nodes, messageBoundMicroStagePlacement?.anchorNodeId],
    );
    const microStageSupervisorSpeech = useMemo(
        () => extractSupervisorMicroStageSpeech(message.nodes || [], microStageAnchorIndex),
        [message.nodes, microStageAnchorIndex],
    );
    const microStageVisible = visibleBubbleMicroStages.length > 0;
    const visibleNodes = useMemo(() => {
        return (message.nodes || []).filter((node) => {
            if (isMicroStageSupersededTimelineNode(node)) {
                return false;
            }
            if (!hasToolCallId(node) || node.executionType !== 'tool_result') {
                return true;
            }
            return !toolCallIds.has(node.toolCallId.trim());
        });
    }, [message.nodes, microStageVisible, toolCallIds]);
    const renderableNodes = useMemo(
        () => visibleNodes.filter((node) => isRenderableTimelineNode(node, Boolean(isLoading && isLast))),
        [isLast, isLoading, visibleNodes],
    );
    const [expandedTraceGroups, setExpandedTraceGroups] = useState<Record<string, boolean>>({});
    const timelineSegments = useMemo(() => {
        const segments: ChatTimelineRenderSegment[] = [];
        let traceChunk: UiTimelineNode[] = [];
        let chunkIndex = 0;
        let stageInserted = false;
        const flushChunk = () => {
            if (traceChunk.length === 0) return;
            const grouped = groupTimelineNodes(traceChunk, resultNodesByToolCallId);
            segments.push(...grouped.map((segment) => ({
                ...segment,
                id: `chunk-${chunkIndex}:${segment.id}`,
            })));
            traceChunk = [];
            chunkIndex += 1;
        };

        (message.nodes || []).forEach((node, index) => {
            if (microStageVisible && !stageInserted && index === microStageAnchorIndex) {
                flushChunk();
                segments.push({
                    kind: "collaboration_stage",
                    id: messageBoundMicroStagePlacement?.id || `collaboration-stage:${message.id}`,
                });
                stageInserted = true;
            }
            if (isMicroStageSupersededTimelineNode(node)) {
                return;
            }
            if (hasToolCallId(node) && node.executionType === "tool_result" && toolCallIds.has(node.toolCallId.trim())) {
                return;
            }
            if (!isRenderableTimelineNode(node, Boolean(isLoading && isLast))) {
                return;
            }
            traceChunk.push(node);
        });
        flushChunk();

        if (microStageVisible && !stageInserted) {
            segments.push({
                kind: "collaboration_stage",
                id: messageBoundMicroStagePlacement?.id || `collaboration-stage:${message.id}`,
            });
        }
        return segments;
    }, [
        isLast,
        isLoading,
        message.id,
        message.nodes,
        messageBoundMicroStagePlacement?.id,
        microStageAnchorIndex,
        microStageVisible,
        resultNodesByToolCallId,
        toolCallIds,
    ]);
    const handleOpenMicroStageDetailRef = (target: CollaborationMicroStageDetailTarget) => {
        if (typeof window === "undefined") {
            return;
        }
        window.dispatchEvent(new CustomEvent("v8:micro-stage-detail", { detail: target }));
    };
    const handleOpenMicroStageOverview = useCallback(() => {
        const workbench = useWorkbenchStore.getState();
        if (!workbench.sessionId) {
            return;
        }
        workbench.openDocument(createSessionOverviewDocument(workbench.sessionId), {
            activate: true,
            mode: "split",
        });
    }, []);
    const hasAssistantNarrativeNode = renderableNodes.some((node) =>
        node.kind === "narrative"
        && node.role === "assistant"
        && String(node.content || "").trim().length > 0,
    );
    const assistantContentFallbackVisible = message.role === "assistant"
        && normalizedContent.trim().length > 0
        && !hasAssistantNarrativeNode;
    const hasAssistantTextResponse = hasAssistantNarrativeNode || assistantContentFallbackVisible;
    const assistantHasVisibleSurface = message.role === "assistant" && (
        visibleBubbleMicroStages.length > 0
        || renderableNodes.length > 0
        || assistantContentFallbackVisible
        || imagesArray.length > 0
        || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
    );
    const assistantEmptyActive = message.role === "assistant"
        && Boolean(isLoading && isLast)
        && !assistantHasVisibleSurface;

    if (message.role === "assistant" && !assistantHasVisibleSurface && !(isLoading && isLast)) {
        return null;
    }

    // USER MESSAGE
    if (message.role === 'user' || message.role === 'tool') {
        const isTool = message.role === 'tool';
        return (
            <>
                <motion.div 
                    initial={animateEntrance ? { opacity: 0, y: 10, scale: 0.98 } : false}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                    className={cn("mx-auto mb-6 flex w-full flex-row-reverse gap-2.5 sm:mb-7 sm:gap-3.5", isTool ? "max-w-4xl" : "max-w-3xl")}
                >
                {/* Avatar */}
                <div className={cn("w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 shadow-lg mt-1 overflow-hidden backdrop-blur-sm border", isTool ? "bg-zinc-800/80 border-zinc-700 text-zinc-400" : "bg-gradient-to-br from-primary to-violet-600 border-white/20 text-white")}>
                    {isTool ? (
                        <TerminalSquare className="w-5 h-5" />
                    ) : userAvatar ? (
                        <img src={userAvatar} alt={userDisplayName} className="w-full h-full object-cover" />
                    ) : (
                        <span className="text-sm font-bold">{userDisplayName.charAt(0).toUpperCase() || <User className="w-5 h-5" />}</span>
                    )}
                </div>

                <div className="flex min-w-0 max-w-[88%] flex-col items-end gap-1.5 sm:max-w-[85%]">
                    {/* Tool specific label */}
                    {isTool && <span className="text-[11px] uppercase tracking-wider text-muted-foreground/60 font-semibold mb-1 mr-1">{t("web.generated.d1eaff25e6")}</span>}
                    {!isTool && (
                        <span className="mr-1 max-w-full truncate text-[12px] font-semibold text-muted-foreground/80">
                            {userDisplayName}
                        </span>
                    )}

                    <div className={cn(
                        "relative min-w-[60px] px-4 py-3.5 text-[14px] shadow-lg transition-all duration-300 sm:px-5 sm:py-4 sm:text-[15px]",
                        isTool
                            ? "bg-zinc-900/90 text-zinc-300 rounded-2xl border border-white/5 font-mono text-xs overflow-x-auto backdrop-blur-md"
                            : "bg-gradient-to-br from-violet-600 to-indigo-600 text-white selection:bg-slate-950/45 selection:text-white rounded-3xl rounded-tr-sm shadow-violet-500/20 backdrop-blur-md border border-white/10"
                    )}>
                        {!isTool && shouldRenderUserMetadata && (
                            <div className="mb-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[14px] font-semibold leading-6 sm:text-[15px]">
                                {!composerPresentation && commandPresetName && (
                                    <span className="inline-flex h-6 items-center gap-1 text-white/95">
                                        <span aria-hidden="true" className="font-bold">/</span>
                                        <span>{commandPresetName.replace(/^\/+/, "")}</span>
                                    </span>
                                )}
                                {!composerPresentation && skillReferences.map((skill) => (
                                    <span
                                        key={`${skill.name}:${skill.path || ""}`}
                                        className="inline-flex h-6 items-center gap-1 text-white/95"
                                        title={skill.path || skill.description || skill.name}
                                    >
                                        <AtSign className="h-4 w-4 shrink-0" />
                                        <span>{skill.name}</span>
                                    </span>
                                ))}
                                {!composerPresentation && mentionReferences.map((reference) => (
                                    <span
                                        key={reference.key}
                                        className="inline-flex h-6 items-center gap-1 text-white/95"
                                        title={reference.description || reference.label}
                                    >
                                        {reference.kind === "plugin"
                                            ? <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-emerald-300" />
                                            : <AtSign className="h-4 w-4 shrink-0" />}
                                        <span>{reference.label}</span>
                                    </span>
                                ))}
                                {contextSessionRefs.map((sessionId) => (
                                    <span
                                        key={sessionId}
                                        className="inline-flex h-6 items-center gap-1 text-cyan-100"
                                        title={sessionId}
                                    >
                                        {t("web.chat.contextSessionRef")} · {sessionId.slice(0, 12)}
                                    </span>
                                ))}
                                {specModeEnabled && (
                                    <span className="inline-flex h-6 items-center text-white/95">
                                        Spec
                                    </span>
                                )}
                            </div>
                        )}
                        {!isTool && fileAttachments.length > 0 && (
                            <div className="mb-3 space-y-2">
                                {fileAttachments.map((attachment, index) => (
                                    <button
                                        type="button"
                                        key={`${attachment.url}:${index}`}
                                        onClick={() => downloadArtifact(attachment.url, attachment.name)}
                                        className="flex min-h-11 items-center gap-2 rounded-lg border border-white/20 bg-white/15 px-3 py-2 text-white transition-colors hover:bg-white/20"
                                    >
                                        <FileText className="h-4 w-4 shrink-0" aria-hidden="true" />
                                        <span className="min-w-0 flex-1 truncate text-sm font-medium">
                                            {attachment.name}
                                        </span>
                                        <Download className="h-4 w-4 shrink-0 opacity-80" aria-hidden="true" />
                                    </button>
                                ))}
                            </div>
                        )}
                        {!isTool && audioAttachments.length > 0 && (
                            <div className="mb-3 space-y-2">
                                {audioAttachments.map((attachment, index) => (
                                    <div
                                        key={`${attachment.url}:${index}`}
                                        className="rounded-2xl border border-white/20 bg-white/15 p-2 backdrop-blur-sm"
                                    >
                                        <div className="mb-1 truncate px-1 text-[11px] font-medium text-white/85">
                                            {attachment.name || t("web.generated.136a263bcd")}
                                        </div>
                                        <audio
                                            controls
                                            preload="metadata"
                                            src={attachment.url}
                                            className="h-9 w-[260px] max-w-full"
                                        />
                                    </div>
                                ))}
                            </div>
                        )}
                        {imagesArray.length > 0 && (
                            <div className="flex flex-wrap gap-2 mb-3">
                                {imagesArray.map((url, i) => {
                                    const isVideo = url.match(/\.(mp4|webm|mov)$/i);
                                    return (
                                        <div 
                                            key={i} 
                                            className={cn(
                                                "relative rounded-xl overflow-hidden bg-black/20 shrink-0 border border-white/20 shadow-inner group/image cursor-pointer hover:border-white/40 transition-colors duration-300",
                                                animateEntrance && "animate-in zoom-in-95",
                                                imagesArray.length === 1 ? "w-full max-w-[320px] max-h-[240px]" : "w-32 h-32"
                                            )}
                                            onClick={() => {
                                                setViewerStartingIndex(i);
                                                setViewerOpen(true);
                                            }}
                                        >
                                            {isVideo ? (
                                                <video src={url} className="w-full h-full object-cover group-hover/image:scale-[1.02] transition-transform duration-500 ease-out" muted playsInline />
                                            ) : (
                                                <img src={url} alt={`attachment-${i}`} className="w-full h-full object-cover group-hover/image:scale-[1.02] transition-transform duration-500 ease-out" />
                                            )}
                                            
                                            {/* Hover Play icon for video */}
                                            {isVideo && (
                                                <div className="absolute inset-0 bg-black/10 group-hover/image:bg-black/30 transition-colors flex items-center justify-center">
                                                    <div className="w-10 h-10 rounded-full bg-black/50 backdrop-blur-sm flex items-center justify-center text-white/90 opacity-0 group-hover/image:opacity-100 transition-opacity">
                                                        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="6 3 20 12 6 21 6 3"/></svg>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                        {composerPresentation ? (
                            <div className="whitespace-pre-wrap break-words text-[14px] leading-relaxed sm:text-[15px]">
                                {composerPresentationSegments.map((segment, index) => (
                                    <span
                                        key={`${segment.start}:${segment.end}:${index}`}
                                        className={segment.type === "reference"
                                            ? segment.reference?.kind === "command"
                                                ? "font-semibold text-violet-200"
                                                : "font-semibold text-orange-200"
                                            : undefined}
                                    >
                                        {segment.text}
                                    </span>
                                ))}
                            </div>
                        ) : normalizedContent.trim() ? (
                            <div className={cn("leading-relaxed", isTool ? "whitespace-pre-wrap break-all" : "prose-invert")}>
                                <MarkdownRenderer content={normalizedContent} />
                            </div>
                        ) : null}

                        {!isLoading && (composerPresentation?.text || normalizedContent) && !isTool && (
                            <MessageActionButtons
                                copied={isCopied}
                                onCopy={() => handleCopy(composerPresentation?.text || normalizedContent)}
                                onDelete={() => onDelete(message.id)}
                                copyLabel={copyLabel}
                                deleteLabel={deleteLabel}
                                className="mt-3"
                            />
                        )}
                    </div>
                </div>
            </motion.div>
            
            {/* Global Lightbox for this message bubble */}
            <MediaViewerLightbox 
                isOpen={viewerOpen} 
                onClose={() => setViewerOpen(false)} 
                items={mediaItems} 
                initialIndex={viewerStartingIndex} 
            />
        </>
    );
}

    const usesCurrentSupervisorProfile = message.role === "assistant"
        && (!message.agentType || message.agentType === "supervisor");
    const displayAgentName = usesCurrentSupervisorProfile
        ? supervisorProfile?.name || message.agentName || "智能主管"
        : message.agentName || "V8 Engine";
    const displayAgentRoleLabel = usesCurrentSupervisorProfile
        ? supervisorProfile?.roleLabel || message.agentRoleLabel || "主理人"
        : message.agentRoleLabel;
    const displayAgentAvatar = usesCurrentSupervisorProfile
        ? supervisorProfile?.avatar || message.agentAvatar || DEFAULT_AVATAR
        : message.agentAvatar || DEFAULT_AVATAR;

    // ASSISTANT MESSAGE
    return (
        <>
        <motion.div 
            initial={animateEntrance ? { opacity: 0, y: 10, scale: 0.98 } : false}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="group mx-auto mb-6 flex w-full max-w-4xl flex-col gap-2.5 sm:mb-7 sm:gap-3"
        >
            {/* Header */}
            <div className="flex select-none items-center gap-3 pl-1">
                <div className="relative h-9 w-9 sm:h-10 sm:w-10">
                    <div className={cn(
                        "flex h-9 w-9 items-center justify-center overflow-hidden rounded-2xl border shadow-md transition-all duration-500 sm:h-10 sm:w-10",
                        "bg-white/80 dark:bg-zinc-800/80 border-white/50 dark:border-white/10 backdrop-blur-md",
                        isLoading && isLast ? "shadow-[0_0_15px_rgba(139,92,246,0.3)] border-violet-500/30" : ""
                    )}>
                        <img src={displayAgentAvatar} alt={displayAgentName} className="w-full h-full object-cover" />
                    </div>
                    {/* Status Dot */}
                    {isLoading && isLast && (
                        <span className="absolute -bottom-1 -right-1 flex h-3 w-3 sm:h-3.5 sm:w-3.5">
                            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60"></span>
                            <span className="relative inline-flex h-3 w-3 rounded-full border-2 border-background bg-primary sm:h-3.5 sm:w-3.5"></span>
                        </span>
                    )}
                </div>

                <div className="flex flex-col justify-center">
                    <div className="flex items-center gap-2">
                        <span className="bg-gradient-to-r from-foreground to-foreground/70 bg-clip-text text-[14px] font-bold tracking-tight text-transparent sm:text-[15px]">
                            {displayAgentName}
                        </span>

                        {/* Role Badge */}
                        {(displayAgentRoleLabel || message.agentType) && (
                            <span className={cn(
                                "rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.16em] shadow-sm backdrop-blur-md",
                                usesCurrentSupervisorProfile || message.agentRoleLabel?.includes('群主')
                                    ? "bg-amber-500/10 text-amber-600 border-amber-500/20 dark:text-amber-400"
                                    : "bg-blue-500/10 text-blue-600 border-blue-500/20 dark:text-blue-400"
                            )}>
                                {displayAgentRoleLabel || (message.agentType === 'supervisor' ? t("web.generated.510a63c701") : t("web.generated.e4139b1ce2"))}
                            </span>
                        )}
                    </div>
                </div>
            </div>

            {/* Content Body */}
            <div className={cn(
                "relative min-w-0 overflow-hidden rounded-[24px] rounded-tl-sm transition-all duration-500 sm:min-w-[300px]",
                "bg-white/40 dark:bg-zinc-900/40 border border-white/20 dark:border-white/5 backdrop-blur-xl shadow-xl shadow-black/5"
            )}>
                <div className="min-h-[56px] space-y-4 px-4 py-4 text-[14px] leading-relaxed text-foreground/90 sm:px-5 sm:py-[18px] sm:text-[15px]" aria-live="polite">
                    {assistantEmptyActive ? <AssistantActivityDots label={t("web.chat.supervisorResponding")} /> : null}
                    {timelineSegments.map((segment, index) => {
                        const isActiveSegment = Boolean(
                            isLoading
                            && isLast
                            && index === timelineSegments.length - 1
                        );
                        if (segment.kind === "collaboration_stage") {
                            return (
                                <CollaborationMicroStageScene
                                    key={microStageSceneKey}
                                    stages={visibleBubbleMicroStages}
                                    executionActive={executionActive}
                                    supervisorSpeech={microStageSupervisorSpeech}
                                    onOpenDetailRef={handleOpenMicroStageDetailRef}
                                    overviewLinkLabel={t("web.collaborationMicroStage.viewOverview")}
                                    onOpenOverview={handleOpenMicroStageOverview}
                                />
                            );
                        }
                        if (segment.kind === "node") {
                            return (
                                <ContentDispatcher 
                                    key={segment.node.id || index}
                                    node={segment.node}
                                    isExecuting={isActiveSegment}
                                    isStreaming={isActiveSegment}
                                    resultNode={hasToolCallId(segment.node) && segment.node.executionType === 'tool_call'
                                        ? resultNodesByToolCallId.get(segment.node.toolCallId.trim())
                                        : undefined}
                                    processes={processes}
                                />
                            );
                        }

                        const defaultExpanded = hasArtifactProducingTool(segment)
                            || (hasAssistantTextResponse ? (isLoading && isLast) : false);
                        const isExpanded = expandedTraceGroups[segment.id] ?? defaultExpanded;
                        const hasActiveProgress = isActiveSegment;

                        return (
                            <div key={segment.id} className="my-1.5 flex flex-col rounded-xl border border-zinc-200/50 dark:border-zinc-800/50 bg-zinc-500/5 dark:bg-zinc-400/5 overflow-hidden shadow-sm">
                                <button
                                    type="button"
                                    onClick={() => setExpandedTraceGroups((prev) => ({ ...prev, [segment.id]: !isExpanded }))}
                                    className="flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-zinc-500/10 dark:hover:bg-zinc-400/10 transition-colors duration-200"
                                >
                                    <div className="flex items-center gap-2 min-w-0">
                                        <div className={cn(
                                            "flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-zinc-500/10 dark:bg-zinc-400/10 text-muted-foreground",
                                            hasActiveProgress && "animate-pulse text-amber-500 bg-amber-500/10"
                                        )}>
                                            <Orbit className="h-3.5 w-3.5" />
                                        </div>
                                        <div className="min-w-0 flex-1">
                                            <div className="text-xs font-semibold text-foreground/80 leading-normal">
                                                {hasActiveProgress ? (
                                                    t("web.chat.trace.active")
                                                ) : (
                                                    <>
                                                        {segment.reasoningCount > 0 && t("web.chat.trace.reasoningCount", { count: segment.reasoningCount })}
                                                        {segment.reasoningCount > 0 && segment.toolCount > 0 && " · "}
                                                        {segment.toolCount > 0 && t("web.chat.trace.toolCount", { count: segment.toolCount })}
                                                    </>
                                                )}
                                            </div>
                                            {segment.totalDuration > 0 && (
                                                <div className="mt-0.5 text-[10px] text-muted-foreground/60 leading-none truncate">
                                                    {t("web.chat.trace.duration", { seconds: segment.totalDuration })}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                    <ChevronDown className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform duration-200", isExpanded && "rotate-180")} />
                                </button>

                                {isExpanded && (
                                    <div className="border-t border-zinc-200/40 dark:border-zinc-800/40 bg-background/30 p-2.5 space-y-2">
                                        {segment.nodes.map((node, nodeIdx) => {
                                            const isActiveNode = hasActiveProgress
                                                && nodeIdx === segment.nodes.length - 1;
                                            return (
                                                <ContentDispatcher
                                                    key={node.id || nodeIdx}
                                                    node={node}
                                                    isExecuting={isActiveNode}
                                                    isStreaming={isActiveNode}
                                                    resultNode={hasToolCallId(node) && node.executionType === 'tool_call'
                                                        ? resultNodesByToolCallId.get(node.toolCallId.trim())
                                                        : undefined}
                                                    processes={processes}
                                                />
                                            );
                                        })}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                    {assistantContentFallbackVisible && (
                        <div className="prose prose-sm max-w-none dark:prose-invert">
                            <MarkdownRenderer content={normalizedContent} />
                        </div>
                    )}

                    <div className="space-y-4">
                        {Array.isArray(message.artifacts) && message.artifacts.length > 0 && (
                            <div className="space-y-2">
                                {message.artifacts.map((artifact) => {
                                    const artifactUrl = resolveRuntimeArtifactUrl(artifact);
                                    return (
                                        <ArtifactCard
                                            key={artifact.id}
                                            id={artifact.id}
                                            title={artifact.displayLabel || artifact.title || artifact.id}
                                            subtitle={artifact.displaySubtitle || artifact.canonicalPath || artifact.workspaceRelativePath || artifactUrl || t("web.chat.artifact.noPath")}
                                            type={inferArtifactCardType(artifact)}
                                            onClick={workbenchSessionId ? () => openWorkbenchDocument(createArtifactDocument(artifact, workbenchSessionId), { activate: true, mode: "split" }) : undefined}
                                            onDownload={artifactUrl ? () => downloadArtifact(artifactUrl, artifact.title || artifact.id) : undefined}
                                        />
                                    );
                                })}
                            </div>
                        )}
                        {imagesArray.length > 0 && (
                            <div className="mb-1 flex flex-wrap gap-2">
                                {imagesArray.map((url, i) => {
                                    const isVideo = url.match(/\.(mp4|webm|mov)$/i);
                                    return (
                                        <div 
                                            key={i} 
                                            className={cn(
                                                "relative rounded-xl overflow-hidden bg-black/20 shrink-0 border border-white/20 shadow-inner group/image cursor-pointer hover:border-white/40 transition-colors animate-in zoom-in-95 duration-300",
                                                imagesArray.length === 1 ? "w-full max-w-[320px] max-h-[240px]" : "w-32 h-32"
                                            )}
                                            onClick={() => {
                                                setViewerStartingIndex(i);
                                                setViewerOpen(true);
                                            }}
                                        >
                                            {isVideo ? (
                                                <video src={url} className="w-full h-full object-cover group-hover/image:scale-[1.02] transition-transform duration-500 ease-out" muted playsInline />
                                            ) : (
                                                <img src={url} alt={`attachment-${i}`} className="w-full h-full object-cover group-hover/image:scale-[1.02] transition-transform duration-500 ease-out" />
                                            )}
                                            
                                            {/* Hover Play icon for video */}
                                            {isVideo && (
                                                <div className="absolute inset-0 bg-black/10 group-hover/image:bg-black/30 transition-colors flex items-center justify-center">
                                                    <div className="w-10 h-10 rounded-full bg-black/50 backdrop-blur-sm flex items-center justify-center text-white/90 opacity-0 group-hover/image:opacity-100 transition-opacity">
                                                        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="6 3 20 12 6 21 6 3"/></svg>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                </div>

                {!isLoading && message.content && (
                    <div className="border-t border-border/30 px-4 pb-3 pt-2.5 sm:px-5">
                        <MessageActionButtons
                            copied={isCopied}
                            onCopy={() => handleCopy(message.content)}
                            onDelete={() => onDelete(message.id)}
                            copyLabel={copyLabel}
                            deleteLabel={deleteLabel}
                        />
                    </div>
                )}
            </div>
        </motion.div>

            {/* Global Lightbox for this message bubble */}
            <MediaViewerLightbox 
                isOpen={viewerOpen} 
                onClose={() => setViewerOpen(false)} 
                items={mediaItems} 
                initialIndex={viewerStartingIndex} 
            />
        </>
    );
}

const ChatMessage = memo(ChatMessageComponent);
ChatMessage.displayName = "ChatMessage";
export { ChatMessage };
