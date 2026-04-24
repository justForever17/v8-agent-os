import { memo, useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { ApprovalCard } from "@/src/components/chat/ApprovalCard";
import { CodeBlock } from "@/src/components/chat/CodeBlock";
import { MarkdownRenderer } from "@/src/components/chat/MarkdownRenderer";
import { MermaidRenderer } from "@/src/components/chat/MermaidRenderer";
import { ImagePreview, MediaPlayer } from "@/src/components/chat/MediaRenderers";
import { ModelViewer } from "@/src/components/chat/ModelViewer";
import { HTMLFileCard } from "@/src/components/chat/HTMLFileCard";
import { PDFFileCard } from "@/src/components/chat/PDFFileCard";
import { PPTCard } from "@/src/components/chat/PPTCard";
import { DownloadFileCard } from "@/src/components/chat/DownloadFileCard";
import { ThinkingCard } from "@/src/components/chat/ThinkingCard";
import { ToolCard } from "@/src/components/chat/ToolCard";
import { VoiceCard } from "@/src/components/chat/VoiceCard";
import { Badge } from "@/src/components/ui/badge";
import { Card, CardContent } from "@/src/components/ui/card";
import type { PhoneContentBlock } from "@/src/lib/content-detector";
import { translateCurrent } from "@/src/lib/locale";
import { resolveRenderableMediaCandidates, resolveRenderableMediaUrl } from "@/src/lib/workspace-links";
import type { PhoneUiTimelineNode } from "@/src/types/admin";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import { coerceAdminResourceRef } from "@v8/session-realtime";

function stringifyPayload(value: unknown) {
    if (typeof value === "string") {
        return value;
    }
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value ?? "");
    }
}

function UnresolvedResourceCard({
    title,
    subtitle,
}: {
    title: string;
    subtitle: string;
}) {
    const { colors } = useUiPrefs();
    return (
        <View style={[styles.unresolvedCard, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
            <View style={[styles.unresolvedIcon, { backgroundColor: colors.surface }]}>
                <MaterialCommunityIcons name="alert-circle-outline" size={18} color={colors.warning} />
            </View>
            <View style={styles.unresolvedBody}>
                <Text style={[styles.unresolvedTitle, { color: colors.text }]} numberOfLines={1}>{title}</Text>
                <Text style={[styles.unresolvedSubtitle, { color: colors.textMuted }]}>{subtitle}</Text>
            </View>
        </View>
    );
}

function isNoticeableContextGovernance(requestInfo: unknown) {
    if (!requestInfo || typeof requestInfo !== "object" || Array.isArray(requestInfo)) {
        return false;
    }
    const record = requestInfo as Record<string, unknown>;
    return Boolean(record.noticeable_latency);
}

function ContextGovernanceDivider({
    label,
    detail,
}: {
    label: string;
    detail: string;
}) {
    const { colors } = useUiPrefs();
    return (
        <View style={styles.contextGovernanceWrap}>
            <View style={[styles.contextGovernanceLine, { backgroundColor: colors.border }]} />
            <View
                style={[
                    styles.contextGovernanceChip,
                    {
                        borderColor: colors.border,
                        backgroundColor: colors.surfaceStrong,
                        shadowColor: colors.text,
                    },
                ]}
            >
                <View style={[styles.contextGovernanceDot, { backgroundColor: colors.accent }]} />
                <Text style={[styles.contextGovernanceLabel, { color: colors.text }]} numberOfLines={1}>
                    {label}
                </Text>
                {detail ? (
                    <Text style={[styles.contextGovernanceDetail, { color: colors.textMuted }]} numberOfLines={1}>
                        {detail}
                    </Text>
                ) : null}
            </View>
            <View style={[styles.contextGovernanceLine, { backgroundColor: colors.border }]} />
        </View>
    );
}

function artifactDisplayTitle(artifact: Record<string, unknown>) {
    return String(
        artifact.displayLabel
        || artifact.title
        || artifact.artifactId
        || artifact.id
        || translateCurrent("src.components.chat.messageblockitem.attachment")
    ).trim();
}

function inferArtifactMediaKind(artifact: Record<string, unknown>, src: string) {
    const kind = String(artifact.kind || "").trim().toLowerCase();
    const mimeType = String(artifact.mimeType || artifact.mime_type || "").trim().toLowerCase();
    const probe = `${kind} ${mimeType} ${src}`.toLowerCase();
    if (probe.includes("image/") || /\.(jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff)(\?.*)?$/i.test(src)) {
        return "image" as const;
    }
    if (probe.includes("video/") || /\.(mp4|webm|mov|avi|mkv)(\?.*)?$/i.test(src)) {
        return "video" as const;
    }
    if (probe.includes("audio/") || /\.(mp3|wav|ogg|m4a|flac|aac)(\?.*)?$/i.test(src)) {
        return "audio" as const;
    }
    return null;
}

function inferArtifactDocumentKind(artifact: Record<string, unknown>, src: string) {
    const kind = String(artifact.kind || "").trim().toLowerCase();
    const mimeType = String(artifact.mimeType || artifact.mime_type || "").trim().toLowerCase();
    const resourceRef = coerceAdminResourceRef(artifact.resourceRef || null);
    const refMimeType = String(resourceRef?.mimeType || "").trim().toLowerCase();
    const title = artifactDisplayTitle(artifact).toLowerCase();
    const probe = `${kind} ${mimeType} ${refMimeType} ${title} ${src}`.toLowerCase();
    if (probe.includes("application/pdf") || /\.pdf(\?.*)?$/i.test(src) || /\.pdf$/i.test(title)) {
        return "pdf" as const;
    }
    if (
        probe.includes("powerpoint")
        || probe.includes("presentation")
        || /\.(ppt|pptx|odp)(\?.*)?$/i.test(src)
        || /\.(ppt|pptx|odp)$/i.test(title)
    ) {
        return "ppt" as const;
    }
    if (
        probe.includes("model/gltf")
        || /\.(glb|gltf)(\?.*)?$/i.test(src)
        || /\.(glb|gltf)$/i.test(title)
    ) {
        return "model" as const;
    }
    if (
        probe.includes("text/html")
        || probe.includes("application/xhtml+xml")
        || /\.html?(\?.*)?$/i.test(src)
        || /\.html?$/i.test(title)
    ) {
        return "html" as const;
    }
    return null;
}

function inferFileViewerKind(input: {
    viewerKind?: unknown;
    mimeType?: unknown;
    filename?: unknown;
    url?: unknown;
}): "pdf" | "ppt" | "model" | "html" | "download" {
    const explicit = String(input.viewerKind || "").trim().toLowerCase();
    if (explicit === "pdf" || explicit === "ppt" || explicit === "model" || explicit === "html" || explicit === "download") {
        return explicit;
    }
    const mimeType = String(input.mimeType || "").trim().toLowerCase();
    const filename = String(input.filename || "").trim().toLowerCase();
    const url = String(input.url || "").trim().toLowerCase();
    const probe = `${mimeType} ${filename} ${url}`;
    if (probe.includes("application/pdf") || /\.pdf(?:$|[?#\s])/i.test(probe)) {
        return "pdf";
    }
    if (probe.includes("powerpoint") || probe.includes("presentation") || /\.(ppt|pptx|odp)(?:$|[?#\s])/i.test(probe)) {
        return "ppt";
    }
    if (probe.includes("model/gltf") || /\.(glb|gltf)(?:$|[?#\s])/i.test(probe)) {
        return "model";
    }
    if (probe.includes("text/html") || probe.includes("application/xhtml+xml") || /\.html?(?:$|[?#\s])/i.test(probe)) {
        return "html";
    }
    return "download";
}

function inferFilenameFromUrl(url: string, fallback = "file") {
    const raw = String(url || "").trim();
    const queryName = (() => {
        try {
            const parsed = new URL(raw, "http://v8.local");
            return parsed.searchParams.get("workspace_relative_path") || parsed.searchParams.get("workspaceRelativePath") || "";
        } catch {
            return "";
        }
    })();
    const source = queryName || raw;
    const tail = source.split(/[\\/]/).filter(Boolean).pop()?.split("?")[0] || fallback;
    try {
        return decodeURIComponent(tail) || fallback;
    } catch {
        return tail || fallback;
    }
}

function isPublicPreviewUrl(url: string) {
    try {
        const parsed = new URL(url);
        const host = parsed.hostname.toLowerCase();
        if (!/^https?:$/.test(parsed.protocol)) {
            return false;
        }
        if (host === "localhost" || host === "::1" || host.endsWith(".local")) {
            return false;
        }
        if (/^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host)) {
            return false;
        }
        const private172 = host.match(/^172\.(\d{1,2})\./);
        if (private172) {
            const second = Number(private172[1]);
            if (second >= 16 && second <= 31) {
                return false;
            }
        }
        return true;
    } catch {
        return false;
    }
}

function sliceByCodePoint(value: string, length: number) {
    return Array.from(value).slice(0, length).join("");
}

function useStreamingRevealContent(content: string, enabled: boolean) {
    const [visibleContent, setVisibleContent] = useState(() => (
        enabled ? sliceByCodePoint(content, Math.min(Array.from(content).length, 8)) : content
    ));

    useEffect(() => {
        if (!enabled) {
            setVisibleContent(content);
            return undefined;
        }

        setVisibleContent((current) => {
            if (!current || !content.startsWith(current)) {
                return sliceByCodePoint(content, Math.min(Array.from(content).length, 8));
            }
            return current;
        });

        const timer = setInterval(() => {
            setVisibleContent((current) => {
                if (current === content) {
                    return current;
                }
                if (!content.startsWith(current)) {
                    return content;
                }
                const currentLength = Array.from(current).length;
                const targetLength = Array.from(content).length;
                const remaining = targetLength - currentLength;
                const step = Math.max(3, Math.min(14, Math.ceil(remaining / 5)));
                return sliceByCodePoint(content, Math.min(targetLength, currentLength + step));
            });
        }, 26);

        return () => clearInterval(timer);
    }, [content, enabled]);

    return visibleContent;
}

export const MessageBlockItem = memo(function MessageBlockItem({
    block,
    node,
    isStreaming = false,
    speaking = false,
    onSpeak,
}: {
    block?: PhoneContentBlock;
    node?: PhoneUiTimelineNode;
    isStreaming?: boolean;
    speaking?: boolean;
    onSpeak?: (text: string) => void;
}) {
    const { adminBaseUrl } = useAppSession();
    const { colors, t } = useUiPrefs();
    const blockContent = String(block?.content || "");
    const revealedBlockContent = useStreamingRevealContent(
        blockContent,
        Boolean(block && (block.type === "text" || block.type === "markdown") && (block.isStreaming || isStreaming)),
    );
    const renderFileCard = (
        url: string,
        options: {
            filename?: string;
            mimeType?: string;
            viewerKind?: "pdf" | "ppt" | "model" | "html" | "download";
        } = {},
    ) => {
        const resolvedUrl = String(url || "").trim();
        const filename = options.filename || inferFilenameFromUrl(resolvedUrl);
        const viewerKind = options.viewerKind || inferFileViewerKind({
            viewerKind: options.viewerKind,
            mimeType: options.mimeType,
            filename,
            url: resolvedUrl,
        });
        if (!resolvedUrl) {
            return (
                <UnresolvedResourceCard
                    title={filename}
                    subtitle={t("src.components.chat.messageblockitem.the_file_url_is_unreachable_right_now_and_the_rest_of_the_reply_remains_visible")}
                />
            );
        }
        if (viewerKind === "html") {
            return <HTMLFileCard url={resolvedUrl} filename={filename} />;
        }
        if (viewerKind === "model") {
            return <ModelViewer src={resolvedUrl} filename={filename} />;
        }
        if (viewerKind === "pdf") {
            return isPublicPreviewUrl(resolvedUrl)
                ? <PDFFileCard url={resolvedUrl} filename={filename} />
                : <DownloadFileCard url={resolvedUrl} filename={filename} mimeType={options.mimeType || "application/pdf"} />;
        }
        if (viewerKind === "ppt") {
            return isPublicPreviewUrl(resolvedUrl)
                ? <PPTCard url={resolvedUrl} filename={filename} />
                : <DownloadFileCard url={resolvedUrl} filename={filename} mimeType={options.mimeType || "presentation"} />;
        }
        return <DownloadFileCard url={resolvedUrl} filename={filename} mimeType={options.mimeType} />;
    };

    if (node?.kind === "governance") {
        const approvalKind = String(node.approvalKind || "").trim().toLowerCase();
        if (node.governanceType === "ask_user") {
            // ask_user is handled only by the foreground blocking input surface.
            // Avoid rendering a second input card in the message stream.
            return null;
        }
        if (node.governanceType === "context_governance") {
            if (!isNoticeableContextGovernance(node.requestInfo)) {
                return null;
            }
            return (
                <ContextGovernanceDivider
                    label={t("src.components.chat.messageblockitem.context_governance")}
                    detail={String(node.reason || node.topic || node.status || "").trim()}
                />
            );
        }
        const title = node.governanceType === "safety_blocked"
            ? t("src.components.chat.messageblockitem.safety_blocked")
            : node.governanceType === "lane_updated"
                    ? t("src.components.chat.messageblockitem.run_scheduling")
                    : node.governanceType === "approval_request"
                        ? t("src.components.chat.messageblockitem.approval")
                        : approvalKind === "safety_blocked"
                        ? t("src.components.chat.messageblockitem.safety_blocked")
                        : t("src.components.chat.messageblockitem.runtime_control");
        const tone = node.governanceType === "safety_blocked" || approvalKind === "safety_blocked"
            ? "safety"
            : node.governanceType === "run_controlled" || node.governanceType === "lane_updated"
                ? "control"
            : "approval";
        return (
            <ApprovalCard
                title={title}
                body={node.question || node.reason || node.topic || node.status || ""}
                status={node.status}
                tone={tone}
            />
        );
    }

    if (node?.kind === "artifact") {
        const artifact = (node.artifact || {}) as Record<string, unknown>;
        const resourceRef = coerceAdminResourceRef(artifact.resourceRef || null);
        const rawSrc = String(
            resourceRef?.signedUrl
            || artifact.previewUrl
            || artifact.externalUrl
            || ""
        ).trim();
        const mediaCandidates = resolveRenderableMediaCandidates(adminBaseUrl, {
            value: rawSrc,
            resourceRef,
            previewUrl: typeof artifact.previewUrl === "string" ? artifact.previewUrl : undefined,
            externalUrl: typeof artifact.externalUrl === "string" ? artifact.externalUrl : undefined,
        });
        const src = mediaCandidates[0] || "";
        const title = artifactDisplayTitle(artifact);
        const mediaKind = inferArtifactMediaKind(artifact, src || rawSrc);
        const documentKind = inferArtifactDocumentKind(artifact, src || rawSrc);
        if (!src) {
            return (
                <UnresolvedResourceCard
                    title={title}
                    subtitle={String(resourceRef?.previewBlockedReason || artifact.previewBlockedReason || artifact.displaySubtitle || resourceRef?.displaySubtitle || t("src.components.chat.messageblockitem.no_canonical_resourceref_is_available_raw_paths_are_text_only"))}
                />
            );
        }
        if (mediaKind === "image") {
            return <ImagePreview src={src} alt={title} candidates={mediaCandidates} />;
        }
        if (mediaKind === "video" || mediaKind === "audio") {
            return <MediaPlayer src={src} type={mediaKind} title={title} candidates={mediaCandidates} />;
        }
        if (documentKind) {
            return renderFileCard(src, {
                filename: title,
                mimeType: String(artifact.mimeType || artifact.mime_type || resourceRef?.mimeType || "").trim() || undefined,
                viewerKind: documentKind,
            });
        }
        return (
            <DownloadFileCard
                url={src}
                filename={title}
                mimeType={String(artifact.mimeType || artifact.mime_type || resourceRef?.mimeType || "").trim() || undefined}
            />
        );
    }

    if (node?.kind === "execution") {
        const payload = node.args ?? node.result ?? node.data;
        const hasPayload = payload !== undefined && payload !== null && stringifyPayload(payload).trim().length > 0;
        return (
            <Card style={styles.executionCard}>
                <CardContent style={styles.executionContent}>
                    <View style={styles.executionHeader}>
                        <View style={styles.executionHeaderText}>
                            {node.label ? (
                                <Text style={[styles.executionTitle, { color: colors.text }]}>{node.label}</Text>
                            ) : null}
                            <View style={styles.executionBadges}>
                                <Badge variant="secondary">{node.executionType}</Badge>
                                {node.toolName ? <Badge variant="outline">{node.toolName}</Badge> : null}
                            </View>
                        </View>
                    </View>
                    {node.content ? <MarkdownRenderer content={node.content} /> : null}
                    {hasPayload ? (
                        <CodeBlock
                            language="json"
                            value={stringifyPayload(payload)}
                        />
                    ) : null}
                </CardContent>
            </Card>
        );
    }

    if (!block) {
        return null;
    }

    const resolvedStreaming = Boolean(block.isStreaming || isStreaming);

    if ((block as { type?: string }).type === "artifact") {
        return block.content ? <MarkdownRenderer content={block.content} /> : null;
    }

    if (block.type === "voice") {
        return (
            <VoiceCard
                text={block.content}
                speaking={speaking}
                onSpeak={() => onSpeak?.(block.content)}
            />
        );
    }

    if (block.type === "image" || block.type === "video" || block.type === "audio") {
        const resourceRef = coerceAdminResourceRef(block.data?.resourceRef || null);
        const rawSrc = String(
            resourceRef?.signedUrl
            || block.data?.previewUrl
            || block.data?.externalUrl
            || ""
        ).trim();
        const resourceSubtitle = typeof resourceRef?.displaySubtitle === "string" && resourceRef.displaySubtitle.trim()
            ? resourceRef.displaySubtitle.trim()
            : "";
        const mediaCandidates = resolveRenderableMediaCandidates(adminBaseUrl, {
            value: rawSrc,
            resourceRef,
            previewUrl: typeof block.data?.previewUrl === "string" ? block.data.previewUrl : undefined,
            externalUrl: typeof block.data?.externalUrl === "string" ? block.data.externalUrl : undefined,
        });
        const src = mediaCandidates[0] || "";
        const title = typeof block.data?.title === "string" ? block.data.title : undefined;
        if (!src) {
            return (
                <UnresolvedResourceCard
                    title={title || t("src.components.chat.messageblockitem.media_preview_unavailable")}
                    subtitle={resourceRef?.previewBlockedReason || resourceSubtitle || t("src.components.chat.messageblockitem.no_canonical_resourceref_is_available_raw_paths_are_text_only")}
                />
            );
        }
        if (block.type === "image") {
            return <ImagePreview src={src} alt={title} candidates={mediaCandidates} />;
        }
        return <MediaPlayer src={src} type={block.type} title={title} candidates={mediaCandidates} />;
    }

    if (block.type === "mermaid") {
        return <MermaidRenderer code={block.content} />;
    }

    if (block.type === "code" || block.type === "html_snippet") {
        const language = String(
            block.data?.language
            || (block.type === "html_snippet" ? "html" : "text"),
        );
        return <CodeBlock language={language} value={block.content} isStreaming={resolvedStreaming} />;
    }

    if (block.type === "file-generic") {
        const url = resolveRenderableMediaUrl(adminBaseUrl, String(block.data?.url || block.content || "").trim());
        return renderFileCard(url, {
            filename: typeof block.data?.filename === "string" ? block.data.filename : undefined,
            mimeType: typeof block.data?.mimeType === "string" ? block.data.mimeType : undefined,
            viewerKind: inferFileViewerKind({
                viewerKind: block.data?.viewerKind,
                mimeType: block.data?.mimeType,
                filename: block.data?.filename,
                url,
            }),
        });
    }

    if (block.type === "file-ppt") {
        const url = resolveRenderableMediaUrl(adminBaseUrl, String(block.data?.url || "").trim());
        return renderFileCard(url, {
            filename: typeof block.data?.filename === "string" ? block.data.filename : undefined,
            viewerKind: "ppt",
        });
    }

    if (block.type === "file-html") {
        const url = resolveRenderableMediaUrl(adminBaseUrl, String(block.data?.url || "").trim());
        return renderFileCard(url, {
            filename: typeof block.data?.filename === "string" ? block.data.filename : undefined,
            viewerKind: "html",
        });
    }

    if (block.type === "file-pdf") {
        const url = resolveRenderableMediaUrl(adminBaseUrl, String(block.data?.url || "").trim());
        return renderFileCard(url, {
            filename: typeof block.data?.filename === "string" ? block.data.filename : undefined,
            viewerKind: "pdf",
        });
    }

    if (block.type === "model-3d") {
        const url = resolveRenderableMediaUrl(adminBaseUrl, block.content.trim());
        return renderFileCard(url, {
            filename: typeof block.data?.filename === "string" ? block.data.filename : undefined,
            viewerKind: "model",
        });
    }

    if (block.type === "thinking") {
        return (
            <ThinkingCard
                content={block.content}
                isStreaming={resolvedStreaming}
                elapsedTime={typeof block.data?.elapsedTime === "number" ? block.data.elapsedTime : undefined}
                data={{
                    startTime: typeof block.data?.startTime === "number" ? block.data.startTime : undefined,
                    endTime: typeof block.data?.endTime === "number" ? block.data.endTime : undefined,
                }}
            />
        );
    }

    if (block.type === "tool") {
        const toolName = String(block.data?.toolName || block.content || t("src.components.chat.contentdispatcher.tool_call")).trim();
        const toolCallId = String(block.data?.toolCallId || "").trim();
        return (
            <ToolCard
                toolInvocation={{
                    toolCallId,
                    toolName,
                    args: block.data?.args ?? {},
                    state: block.data?.result !== undefined && block.data?.result !== null ? "result" : "call",
                    result: block.data?.result,
                }}
            />
        );
    }

    return <MarkdownRenderer content={revealedBlockContent} />;
});

const styles = StyleSheet.create({
    executionCard: {
        borderRadius: radii.xl,
        width: "100%",
        overflow: "hidden",
    },
    executionContent: {
        gap: spacing.sm,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.md,
    },
    executionHeader: {
        flexDirection: "row",
        alignItems: "flex-start",
    },
    executionHeaderText: {
        flex: 1,
        gap: 8,
    },
    executionTitle: {
        fontSize: 14,
        fontWeight: "700",
        lineHeight: 20,
    },
    executionBadges: {
        flexDirection: "row",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 8,
    },
    unresolvedCard: {
        borderRadius: radii.lg,
        borderWidth: 1,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
    },
    unresolvedIcon: {
        width: 36,
        height: 36,
        borderRadius: 18,
        alignItems: "center",
        justifyContent: "center",
    },
    unresolvedBody: {
        flex: 1,
        gap: 2,
    },
    unresolvedTitle: {
        fontSize: 13,
        lineHeight: 18,
        fontWeight: "700",
    },
    unresolvedSubtitle: {
        fontSize: 11,
        lineHeight: 16,
    },
    contextGovernanceWrap: {
        width: "100%",
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
        marginVertical: spacing.xs,
    },
    contextGovernanceLine: {
        flex: 1,
        height: StyleSheet.hairlineWidth,
        opacity: 0.55,
    },
    contextGovernanceChip: {
        maxWidth: "78%",
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        paddingHorizontal: spacing.sm,
        paddingVertical: 6,
        borderRadius: radii.pill,
        borderWidth: 1,
        shadowOpacity: 0.08,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 2 },
        elevation: 1,
    },
    contextGovernanceDot: {
        width: 6,
        height: 6,
        borderRadius: 999,
        opacity: 0.9,
    },
    contextGovernanceLabel: {
        fontSize: 11,
        lineHeight: 15,
        fontWeight: "700",
    },
    contextGovernanceDetail: {
        flexShrink: 1,
        fontSize: 10,
        lineHeight: 14,
    },
});
