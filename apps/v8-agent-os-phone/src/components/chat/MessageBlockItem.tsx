import { memo } from "react";
import { Linking, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { ApprovalCard } from "@/src/components/chat/ApprovalCard";
import { AskUserCard } from "@/src/components/chat/AskUserCard";
import { CodeBlock } from "@/src/components/chat/CodeBlock";
import { MarkdownRenderer } from "@/src/components/chat/MarkdownRenderer";
import { MermaidRenderer } from "@/src/components/chat/MermaidRenderer";
import { ImagePreview, MediaPlayer } from "@/src/components/chat/MediaRenderers";
import { ModelViewer } from "@/src/components/chat/ModelViewer";
import { HTMLFileCard } from "@/src/components/chat/HTMLFileCard";
import { PPTCard } from "@/src/components/chat/PPTCard";
import { ThinkingCard } from "@/src/components/chat/ThinkingCard";
import { ToolCard } from "@/src/components/chat/ToolCard";
import { VoiceCard } from "@/src/components/chat/VoiceCard";
import { Badge } from "@/src/components/ui/badge";
import { Card, CardContent } from "@/src/components/ui/card";
import type { PhoneContentBlock } from "@/src/lib/content-detector";
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

    if (node?.kind === "governance") {
        const approvalKind = String(node.approvalKind || "").trim().toLowerCase();
        if (node.governanceType === "ask_user") {
            return <AskUserCard question={node.question || node.reason || node.topic || ""} status={node.status} />;
        }
        const title = node.governanceType === "safety_blocked"
            ? t("安全阻断", "Safety blocked")
            : node.governanceType === "context_governance"
                ? t("上下文治理", "Context governance")
                    : node.governanceType === "lane_updated"
                    ? t("运行调度", "Run scheduling")
                    : node.governanceType === "approval_request"
                        ? t("系统确认", "Approval")
                        : approvalKind === "safety_blocked"
                        ? t("安全阻断", "Safety blocked")
                        : t("系统控制", "Runtime control");
        const tone = node.governanceType === "safety_blocked" || approvalKind === "safety_blocked"
            ? "safety"
            : node.governanceType === "run_controlled" || node.governanceType === "lane_updated" || node.governanceType === "context_governance"
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
        return null;
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
        const rawSrc = String(block.data?.src || block.content || "").trim();
        const mediaCandidates = resolveRenderableMediaCandidates(adminBaseUrl, {
            value: rawSrc,
            resourceRef: coerceAdminResourceRef(block.data?.resourceRef || null),
            previewUrl: typeof block.data?.previewUrl === "string" ? block.data.previewUrl : undefined,
            externalUrl: typeof block.data?.externalUrl === "string" ? block.data.externalUrl : undefined,
            workspacePath: typeof block.data?.workspacePath === "string" ? block.data.workspacePath : undefined,
            sourcePath: typeof block.data?.sourcePath === "string" ? block.data.sourcePath : undefined,
        });
        const src = mediaCandidates[0] || "";
        const title = typeof block.data?.title === "string" ? block.data.title : undefined;
        if (!src) {
            return (
                <UnresolvedResourceCard
                    title={title || t("媒体资源暂不可预览", "Media preview unavailable")}
                    subtitle={t("当前资源地址暂不可达，已降级显示该节点。", "The media URL is currently unreachable, so this node has been downgraded instead of disappearing.")}
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

    if (block.type === "file-ppt") {
        const url = resolveRenderableMediaUrl(adminBaseUrl, String(block.data?.url || "").trim());
        return url ? (
            <PPTCard
                url={url}
                filename={typeof block.data?.filename === "string" ? block.data.filename : undefined}
            />
        ) : (
            <UnresolvedResourceCard
                title={t("演示文件暂不可打开", "Presentation unavailable")}
                subtitle={t("当前文件地址不可达，已保留正文其余内容。", "The file URL is unreachable right now, and the rest of the reply remains visible.")}
            />
        );
    }

    if (block.type === "file-html") {
        const url = resolveRenderableMediaUrl(adminBaseUrl, String(block.data?.url || "").trim());
        return url ? (
            <HTMLFileCard
                url={url}
                filename={typeof block.data?.filename === "string" ? block.data.filename : undefined}
            />
        ) : (
            <UnresolvedResourceCard
                title={t("HTML 文件暂不可打开", "HTML file unavailable")}
                subtitle={t("当前文件地址不可达，已保留正文其余内容。", "The file URL is unreachable right now, and the rest of the reply remains visible.")}
            />
        );
    }

    if (block.type === "model-3d") {
        const url = resolveRenderableMediaUrl(adminBaseUrl, block.content.trim());
        return url ? <ModelViewer src={url} /> : (
            <UnresolvedResourceCard
                title={t("3D 模型暂不可预览", "3D preview unavailable")}
                subtitle={t("当前模型资源地址不可达，已保留其余正式回复内容。", "The model URL is unreachable right now, and the rest of the reply remains visible.")}
            />
        );
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
        const toolName = String(block.data?.toolName || block.content || t("工具调用", "Tool call")).trim();
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

    return <MarkdownRenderer content={block.content} />;
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
});
