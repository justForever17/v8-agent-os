"use client";
/* eslint-disable @next/next/no-img-element */

import { User, Copy, Trash2, Check, Sparkles, TerminalSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useState, memo, useMemo } from "react";
import { motion } from "framer-motion";
import {
    buildCollaborationMicroStages,
    buildCollaborationMicroStagesFromMessageBoundNodes,
    buildMessageBoundExecutionNodes,
    coerceAdminResourceRef,
    isRuntimeEpisodeGraphActivity,
    resolveAdminResourceUrl,
    type AdminProcessRef,
    type CollaborationMicroStageActivityInput,
    type MessageBoundExecutionMessage,
} from "@v8/session-realtime";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { Message, UiExecutionNode, UiTimelineNode } from "@/store/chat-types";
import { ContentDispatcher } from "./ContentDispatcher";
import { cn } from "@/lib/utils";
import { MediaViewerLightbox, MediaItem } from "./MediaViewerLightbox";
import { ArtifactCard } from "./ArtifactCard";
import { inferArtifactCardType, resolveRuntimeArtifactUrl } from "@/lib/artifacts";
import { useChatStore } from "@/store/chat-store";
import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
import { parseContentToBlocks } from "@/lib/chat/content-detector";
import { CollaborationMicroStageScene, type CollaborationMicroStageDetailTarget } from "./collaboration/CollaborationMicroStageScene";
import type { RuntimeStageActivity } from "@/lib/runtime-stage";

interface ChatMessageProps {
    message: Message;
    processes?: AdminProcessRef[];
    isLoading?: boolean;
    onDelete: (id: string) => void;
    isLast?: boolean;
    userAvatar?: string | null;
    userName?: string | null;
    runtimeActivities?: RuntimeStageActivity[];
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

function isExecutionNode(node: UiTimelineNode): node is UiExecutionNode {
    return node.kind === "execution";
}

const MICRO_STAGE_TOOL_NAMES = new Set(["delegation_broker", "runtime_broker"]);

function hasToolCallId(node: UiTimelineNode): node is UiExecutionNode & { toolCallId: string } {
    return isExecutionNode(node) && typeof node.toolCallId === "string" && node.toolCallId.trim().length > 0;
}

function toMicroStageActivityInput(activity: RuntimeStageActivity): CollaborationMicroStageActivityInput {
    return {
        id: activity.id,
        topic: activity.topic,
        summary: activity.summary,
        timestamp: activity.timestamp,
        runtimeId: activity.runtimeId,
        data: activity.node.kind === "execution" && activity.node.data && typeof activity.node.data === "object"
            ? activity.node.data as Record<string, unknown>
            : {},
    };
}

function getExecutionTopic(node: UiExecutionNode) {
    return String(node.topic || node.data?.topic || "").trim().toLowerCase();
}

function getExecutionToolName(node: UiExecutionNode) {
    return String(node.toolName || node.data?.toolName || node.data?.tool_name || "").trim().toLowerCase();
}

function isMicroStageSupersededTimelineNode(node: UiTimelineNode, microStageVisible: boolean) {
    if (!microStageVisible || !isExecutionNode(node)) {
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

    return (node.executionType === "tool_call" || node.executionType === "tool_result")
        && MICRO_STAGE_TOOL_NAMES.has(getExecutionToolName(node));
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
            return toolName !== "write_todos" && toolName !== "update_todo";
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

function extractSupervisorMicroStageSpeech(nodes: UiTimelineNode[]) {
    for (const node of nodes) {
        if (node.kind !== "narrative" || node.role !== "assistant") {
            continue;
        }
        const text = parseContentToBlocks(String(node.content || ""), false, 0)
            .filter((block) => block.type !== "voice")
            .map((block) => block.content.trim())
            .filter(Boolean)
            .join(" ")
            .replace(/\s+/g, " ")
            .trim();
        if (!text) {
            continue;
        }
        return text.length > 42 ? `${text.slice(0, 41)}...` : text;
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

function hasTaskPlanningMode(message: Message): boolean {
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

function extractMessageAttachments(message: Message): MessageAttachmentRecord[] {
    const metadata = message.metadata && typeof message.metadata === "object" ? message.metadata : {};
    const rawAttachments = Array.isArray(metadata.attachments) ? metadata.attachments : [];
    return rawAttachments
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
}

function isAudioAttachmentRecord(item: MessageAttachmentRecord) {
    return item.mediaKind === "audio"
        || item.mimeType.startsWith("audio/")
        || /\.(mp3|m4a|wav|ogg|opus|aac|flac|webm)(?:[?#].*)?$/i.test(item.url);
}

function isVisualAttachmentRecord(item: MessageAttachmentRecord) {
    return item.mediaKind === "image"
        || item.mediaKind === "video"
        || item.mimeType.startsWith("image/")
        || item.mimeType.startsWith("video/")
        || /\.(png|jpe?g|webp|gif|bmp|heic|heif|mp4|webm|mov|m4v)(?:[?#].*)?$/i.test(item.url);
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

function ChatMessageComponent({ message, processes = [], isLoading, onDelete, isLast, userAvatar, userName, runtimeActivities = [] }: ChatMessageProps) {
    const t = useT();
    const [isCopied, setIsCopied] = useState(false);
    const setActiveArtifactId = useChatStore((state) => state.setActiveArtifactId);
    const commandPresetName = useMemo(() => extractCommandPresetName(message), [message]);
    const taskPlanningModeEnabled = useMemo(() => hasTaskPlanningMode(message), [message]);
    const skillReferences = useMemo(() => extractSkillReferences(message), [message]);
    const shouldRenderUserMetadata = Boolean(commandPresetName || taskPlanningModeEnabled || skillReferences.length > 0);
    const normalizedContent = useMemo(() => normalizeWorkspaceLinks(message.content || ""), [message.content]);
    const copyLabel = t(lt("复制消息", "Copy message"));
    const deleteLabel = t(lt("删除消息", "Delete message"));
    const userDisplayName = String(userName || "").trim() || t(lt("聊天用户", "Chat user"));
    const attachmentRecords = useMemo(() => extractMessageAttachments(message), [message]);
    const audioAttachments = useMemo(
        () => attachmentRecords.filter(isAudioAttachmentRecord),
        [attachmentRecords],
    );
    const visualAttachmentUrls = useMemo(
        () => attachmentRecords.filter((item) => !isAudioAttachmentRecord(item) && isVisualAttachmentRecord(item)).map((item) => item.url),
        [attachmentRecords],
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
        () => Array.from(new Set([
            ...(Array.isArray(message.images) ? message.images : []),
            ...visualAttachmentUrls,
        ]))
            .map((value) => {
                const raw = String(value || "").trim();
                if (!raw) {
                    return "";
                }
                return resolveAdminResourceUrl("web", undefined, coerceAdminResourceRef(raw)) || raw.replace(/^\/api\/client\b/i, "/api");
            })
            .filter(Boolean),
        [message.images, visualAttachmentUrls],
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
            ? runtimeActivities.filter((activity) => isRuntimeEpisodeGraphActivity({ topic: activity.topic }))
            : []),
        [isLast, message.role, runtimeActivities],
    );
    const messageBoundExecutionNodes = useMemo(
        () => buildMessageBoundExecutionNodes([message as unknown as MessageBoundExecutionMessage]),
        [message],
    );
    const messageBoundMicroStages = useMemo(
        () => buildCollaborationMicroStagesFromMessageBoundNodes(
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
    const visibleBubbleMicroStages = messageBoundMicroStages.length > 0
        ? messageBoundMicroStages
        : liveFallbackMicroStages;
    const microStageSupervisorSpeech = useMemo(
        () => extractSupervisorMicroStageSpeech(message.nodes || []),
        [message.nodes],
    );
    const microStageVisible = visibleBubbleMicroStages.length > 0;
    const visibleNodes = useMemo(() => {
        return (message.nodes || []).filter((node) => {
            if (isMicroStageSupersededTimelineNode(node, microStageVisible)) {
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
    const handleOpenMicroStageDetailRef = (target: CollaborationMicroStageDetailTarget) => {
        if (typeof window === "undefined") {
            return;
        }
        window.dispatchEvent(new CustomEvent("v8:micro-stage-detail", { detail: target }));
    };
    const hasAssistantNarrativeNode = renderableNodes.some((node) =>
        node.kind === "narrative"
        && node.role === "assistant"
        && String(node.content || "").trim().length > 0,
    );
    const assistantContentFallbackVisible = message.role === "assistant"
        && normalizedContent.trim().length > 0
        && !hasAssistantNarrativeNode;
    const assistantHasVisibleSurface = message.role === "assistant" && (
        visibleBubbleMicroStages.length > 0
        || renderableNodes.length > 0
        || assistantContentFallbackVisible
        || imagesArray.length > 0
        || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
    );

    if (message.role === "assistant" && !assistantHasVisibleSurface && !(isLoading && isLast)) {
        return null;
    }

    // USER MESSAGE
    if (message.role === 'user' || message.role === 'tool') {
        const isTool = message.role === 'tool';
        return (
            <>
                <motion.div 
                    initial={{ opacity: 0, y: 10, scale: 0.98 }}
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
                    {isTool && <span className="text-[11px] uppercase tracking-wider text-muted-foreground/60 font-semibold mb-1 mr-1">{t(lt("系统输出", "System output"))}</span>}
                    {!isTool && (
                        <span className="mr-1 max-w-full truncate text-[12px] font-semibold text-muted-foreground/80">
                            {userDisplayName}
                        </span>
                    )}

                    <div className={cn(
                        "relative min-w-[60px] px-4 py-3.5 text-[14px] shadow-lg transition-all duration-300 sm:px-5 sm:py-4 sm:text-[15px]",
                        isTool
                            ? "bg-zinc-900/90 text-zinc-300 rounded-2xl border border-white/5 font-mono text-xs overflow-x-auto backdrop-blur-md"
                            : "bg-gradient-to-br from-violet-600 to-indigo-600 text-white rounded-3xl rounded-tr-sm shadow-violet-500/20 backdrop-blur-md border border-white/10"
                    )}>
                        {!isTool && shouldRenderUserMetadata && (
                            <div className="mb-3 flex flex-wrap gap-2">
                                {commandPresetName && (
                                    <span className="inline-flex items-center rounded-full border border-white/30 bg-white/15 px-2.5 py-1 text-[11px] font-semibold tracking-wide text-white/95 backdrop-blur-sm">
                                        /{commandPresetName}
                                    </span>
                                )}
                                {skillReferences.map((skill) => (
                                    <span
                                        key={`${skill.name}:${skill.path || ""}`}
                                        className="inline-flex items-center rounded-full border border-fuchsia-200/60 bg-fuchsia-500/20 px-2.5 py-1 text-[11px] font-semibold tracking-wide text-white backdrop-blur-sm"
                                        title={skill.path || skill.description || skill.name}
                                    >
                                        @{skill.name}
                                    </span>
                                ))}
                                {taskPlanningModeEnabled && (
                                    <span className="inline-flex items-center rounded-full border border-white/30 bg-white/15 px-2.5 py-1 text-[11px] font-semibold tracking-wide text-white/95 backdrop-blur-sm">
                                        Spec
                                    </span>
                                )}
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
                                            {attachment.name || t(lt("语音消息", "Voice message"))}
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
                        {normalizedContent.trim() ? (
                            <div className={cn("leading-relaxed", isTool ? "whitespace-pre-wrap break-all" : "prose-invert")}>
                                <MarkdownRenderer content={normalizedContent} />
                            </div>
                        ) : null}

                        {!isLoading && normalizedContent && !isTool && (
                            <MessageActionButtons
                                copied={isCopied}
                                onCopy={() => handleCopy(normalizedContent)}
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

    // ASSISTANT MESSAGE
    return (
        <>
        <motion.div 
            initial={{ opacity: 0, y: 10, scale: 0.98 }}
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
                        {message.agentAvatar ? (
                            <img src={message.agentAvatar} alt={message.agentName} className="w-full h-full object-cover" />
                        ) : (
                            <div className="w-full h-full bg-gradient-to-br from-violet-500 to-primary flex items-center justify-center text-white">
                                <Sparkles className="h-[18px] w-[18px] sm:h-5 sm:w-5" />
                            </div>
                        )}
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
                            {message.agentName || "V8 Engine"}
                        </span>

                        {/* Role Badge */}
                        {(message.agentRoleLabel || message.agentType) && (
                            <span className={cn(
                                "rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.16em] shadow-sm backdrop-blur-md",
                                message.agentType === 'supervisor' || message.agentRoleLabel?.includes('群主')
                                    ? "bg-amber-500/10 text-amber-600 border-amber-500/20 dark:text-amber-400"
                                    : "bg-blue-500/10 text-blue-600 border-blue-500/20 dark:text-blue-400"
                            )}>
                                {message.agentRoleLabel || (message.agentType === 'supervisor' ? t(lt("主理人", "Lead")) : t(lt("专家", "Specialist")))}
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
                {/* Decorative top sheen */}
                <div className="absolute top-0 left-0 w-full h-[2px] bg-gradient-to-r from-violet-500/40 via-purple-400/20 to-transparent opacity-80" />

                <div className="space-y-4 px-4 py-4 text-[14px] leading-relaxed text-foreground/90 sm:px-5 sm:py-[18px] sm:text-[15px]">
                    {visibleBubbleMicroStages.length > 0 && (
                        <CollaborationMicroStageScene
                            stages={visibleBubbleMicroStages}
                            supervisorSpeech={microStageSupervisorSpeech}
                            onOpenDetailRef={handleOpenMicroStageDetailRef}
                        />
                    )}

                    {renderableNodes.map((node, i) => (
                        <ContentDispatcher 
                            key={node.id || i}
                            node={node}
                            isExecuting={!!(isLoading && isLast)}
                            isStreaming={!!(isLoading && isLast)}
                            resultNode={hasToolCallId(node) && node.executionType === 'tool_call'
                                ? resultNodesByToolCallId.get(node.toolCallId.trim())
                                : undefined}
                            processes={processes}
                        />
                    ))}
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
                                            subtitle={artifact.displaySubtitle || artifact.canonicalPath || artifact.workspaceRelativePath || artifactUrl || "暂无路径信息"}
                                            type={inferArtifactCardType(artifact)}
                                            onClick={() => setActiveArtifactId(artifact.id)}
                                            onDownload={artifactUrl ? () => window.open(artifactUrl, "_blank", "noopener,noreferrer") : undefined}
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
