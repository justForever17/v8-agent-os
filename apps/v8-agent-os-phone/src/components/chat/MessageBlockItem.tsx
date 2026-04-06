import { memo } from "react";
import { Linking, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { ApprovalCard } from "@/src/components/chat/ApprovalCard";
import { ArtifactCard } from "@/src/components/chat/ArtifactCard";
import { AskUserCard } from "@/src/components/chat/AskUserCard";
import { CodeBlock } from "@/src/components/chat/CodeBlock";
import { MarkdownRenderer } from "@/src/components/chat/MarkdownRenderer";
import { MermaidRenderer } from "@/src/components/chat/MermaidRenderer";
import { ModelViewer } from "@/src/components/chat/ModelViewer";
import { HTMLFileCard } from "@/src/components/chat/HTMLFileCard";
import { PPTCard } from "@/src/components/chat/PPTCard";
import { ThinkingCard } from "@/src/components/chat/ThinkingCard";
import { ToolCard } from "@/src/components/chat/ToolCard";
import { VoiceCard } from "@/src/components/chat/VoiceCard";
import { Badge } from "@/src/components/ui/badge";
import { Card, CardContent } from "@/src/components/ui/card";
import type { PhoneContentBlock } from "@/src/lib/content-detector";
import { normalizeRenderableWorkspaceUrl } from "@/src/lib/workspace-links";
import type { ChatArtifact, PhoneUiTimelineNode } from "@/src/types/admin";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

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

export const MessageBlockItem = memo(function MessageBlockItem({
    block,
    node,
    isStreaming = false,
    speaking = false,
    onSpeak,
    onOpenArtifact,
}: {
    block?: PhoneContentBlock;
    node?: PhoneUiTimelineNode;
    isStreaming?: boolean;
    speaking?: boolean;
    onSpeak?: (text: string) => void;
    onOpenArtifact?: (artifact: ChatArtifact) => void;
}) {
    const { adminBaseUrl } = useAppSession();
    const { colors, t } = useUiPrefs();

    if (node?.kind === "governance") {
        const approvalKind = String(node.approvalKind || "").trim().toLowerCase();
        const isAskUser = approvalKind === "human_input_required" || approvalKind === "ask_user" || approvalKind === "waiting_input";
        if (isAskUser) {
            return <AskUserCard question={node.question || node.reason || node.topic || ""} status={node.status} />;
        }
        return (
            <ApprovalCard
                title={approvalKind === "safety_blocked" ? t("安全阻断", "Safety blocked") : t("系统确认", "Approval")}
                body={node.question || node.reason || node.topic || node.status || ""}
                status={node.status}
                tone={approvalKind === "safety_blocked" ? "safety" : node.governanceType === "run_controlled" ? "control" : "approval"}
            />
        );
    }

    if (node?.kind === "artifact") {
        return (
            <ArtifactCard
                title={node.artifact.displayLabel || node.artifact.title || t("产物", "Artifact")}
                type="file"
                subtitle={node.artifact.displaySubtitle || node.artifact.workspacePath || node.artifact.sourcePath || ""}
                onPress={() => onOpenArtifact?.(node.artifact)}
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

    if (block.type === "artifact") {
        const artifactTitle = String(block.data?.title || t("生成产物", "Generated artifact"));
        const artifactType = String(block.data?.type || "file").toLowerCase();
        const artifactRecord: ChatArtifact = {
            title: typeof block.data?.title === "string" ? block.data.title : artifactTitle,
            kind: typeof block.data?.type === "string" ? block.data.type : artifactType,
            previewUrl: typeof block.data?.url === "string" ? block.data.url : undefined,
            externalUrl: typeof block.data?.url === "string" ? block.data.url : undefined,
            workspacePath: typeof block.data?.workspacePath === "string" ? block.data.workspacePath : undefined,
            sourcePath: typeof block.data?.sourcePath === "string" ? block.data.sourcePath : undefined,
        };
        return (
            <View style={styles.artifactStack}>
                {block.content ? (
                    <CodeBlock
                        language={artifactType}
                        value={block.content}
                        isStreaming={resolvedStreaming}
                    />
                ) : null}
                {!resolvedStreaming ? (
                    <ArtifactCard
                        title={artifactTitle}
                        type={artifactType === "html" ? "html" : artifactType === "markdown" ? "markdown" : artifactType === "code" ? "code" : "file"}
                        subtitle={artifactType}
                        onPress={block.data ? () => onOpenArtifact?.(artifactRecord) : undefined}
                    />
                ) : null}
            </View>
        );
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
        const url = normalizeRenderableWorkspaceUrl(adminBaseUrl, String(block.data?.url || "").trim());
        return url ? (
            <PPTCard
                url={url}
                filename={typeof block.data?.filename === "string" ? block.data.filename : undefined}
            />
        ) : null;
    }

    if (block.type === "file-html") {
        const url = normalizeRenderableWorkspaceUrl(adminBaseUrl, String(block.data?.url || "").trim());
        return url ? (
            <HTMLFileCard
                url={url}
                filename={typeof block.data?.filename === "string" ? block.data.filename : undefined}
            />
        ) : null;
    }

    if (block.type === "model-3d") {
        const url = normalizeRenderableWorkspaceUrl(adminBaseUrl, block.content.trim());
        return url ? <ModelViewer src={url} /> : null;
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
    artifactStack: {
        gap: spacing.sm,
        width: "100%",
        minWidth: 0,
    },
});
