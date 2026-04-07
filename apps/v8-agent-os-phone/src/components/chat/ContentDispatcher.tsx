import { memo, useMemo } from "react";
import { StyleSheet, Text, View } from "react-native";
import type { AdminProcessRef } from "@v8/session-realtime";

import { GenericToolTraceCard } from "@/src/components/chat/GenericToolTraceCard";
import { InteractiveTerminalCard } from "@/src/components/chat/InteractiveTerminalCard";
import { MessageBlockItem } from "@/src/components/chat/MessageBlockItem";
import { ToolCard, type ToolInvocation } from "@/src/components/chat/ToolCard";
import {
    isBackgroundCommandTraceTool,
} from "@/src/lib/chat/command-session";
import { isHiddenPhoneTimelineNode } from "@/src/lib/chat-node-visibility";
import { buildVoicePlaybackKey, parsePhoneContentBlocks, type PhoneContentBlock } from "@/src/lib/content-detector";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { spacing } from "@/src/theme/tokens";
import type { ChatArtifact, PhoneUiExecutionNode, PhoneUiTimelineNode } from "@/src/types/admin";

const HIDDEN_TOOL_NAMES = new Set(["write_todos", "update_todo", "inspect_and_move_media"]);
const TRACE_TOOL_NAMES = new Set(["download_media_for_vision"]);

function tryParseJsonRecord(value: unknown): Record<string, unknown> | null {
    if (value && typeof value === "object" && !Array.isArray(value)) {
        return value as Record<string, unknown>;
    }
    if (typeof value !== "string") {
        return null;
    }
    try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === "object" && !Array.isArray(parsed)
            ? parsed as Record<string, unknown>
            : null;
    } catch {
        return null;
    }
}

function compactToolResult(toolName: string, value: unknown) {
    if (toolName !== "download_media_for_vision") {
        return value;
    }
    const record = tryParseJsonRecord(value);
    if (!record) {
        return value;
    }
    return {
        ok: record.ok,
        artifactId: record.artifactId ?? record.primaryArtifactId,
        kind: record.kind ?? record.primaryKind,
        mimeType: record.mimeType,
        fileName: record.fileName,
        workspacePath: record.workspacePath ?? record.canonicalPath ?? record.userVisiblePath ?? record.primaryFile,
        workspaceRelativePath: record.workspaceRelativePath,
        message: record.message ?? record.statusMessage ?? record.error,
    };
}

function buildToolInvocation(executionNode: PhoneUiExecutionNode, fallbackLabel: string): ToolInvocation {
    const toolName = String(
        executionNode.toolName
        || executionNode.data?.toolName
        || executionNode.data?.tool_name
        || executionNode.label
        || fallbackLabel,
    ).trim() || fallbackLabel;

    const result = executionNode.result
        ?? executionNode.data?.result
        ?? executionNode.data?.response
        ?? executionNode.data?.result_preview;

    return {
        toolCallId: String(
            executionNode.toolCallId
            || executionNode.data?.toolCallId
            || executionNode.data?.tool_call_id
            || `${executionNode.id}:${toolName}`,
        ).trim(),
        toolName,
        args: executionNode.args ?? executionNode.data?.args ?? executionNode.data?.request ?? {},
        state: executionNode.executionType === "tool_result" || result !== undefined && result !== null ? "result" : "call",
        result: compactToolResult(toolName, result),
    };
}

export const ContentDispatcher = memo(function ContentDispatcher({
    node,
    messageIdentity,
    isExecuting = false,
    isStreaming = false,
    speakingKey,
    onSpeakVoice,
    onOpenArtifact,
    processes = [],
}: {
    node: PhoneUiTimelineNode;
    messageIdentity?: string;
    isExecuting?: boolean;
    isStreaming?: boolean;
    speakingKey?: string;
    onSpeakVoice?: (text: string, messageKey: string) => void;
    onOpenArtifact?: (artifact: ChatArtifact) => void;
    processes?: AdminProcessRef[];
}) {
    const { colors, t } = useUiPrefs();
    if (isHiddenPhoneTimelineNode(node)) {
        return null;
    }
    const narrativeBlocks = useMemo(
        () => (node.kind === "narrative" ? parsePhoneContentBlocks(String(node.content || "")) : []),
        [node],
    );

    const renderExecutionTool = (executionNode: PhoneUiExecutionNode) => {
        const toolInvocation = buildToolInvocation(executionNode, t("工具调用", "Tool call"));
        if (HIDDEN_TOOL_NAMES.has(toolInvocation.toolName)) {
            return null;
        }
        const matchedProcess = toolInvocation.toolCallId
            ? processes.find((process) => process.toolCallId === toolInvocation.toolCallId)
            : undefined;

        if (matchedProcess) {
            return (
                <View style={styles.stack}>
                    <ToolCard toolInvocation={toolInvocation} hideResult />
                    <InteractiveTerminalCard process={matchedProcess} compact />
                </View>
            );
        }

        if (isBackgroundCommandTraceTool(toolInvocation.toolName)) {
            return <GenericToolTraceCard toolInvocation={toolInvocation} />;
        }

        if (TRACE_TOOL_NAMES.has(toolInvocation.toolName)) {
            return <GenericToolTraceCard toolInvocation={toolInvocation} />;
        }

        return <ToolCard toolInvocation={toolInvocation} />;
    };

    if (node.kind === "narrative") {
        if (narrativeBlocks.length === 0) {
            return null;
        }
        return (
            <View style={styles.stack}>
                {narrativeBlocks.map((block, index) => {
                    const voiceKey = block.type === "voice" && messageIdentity
                        ? buildVoicePlaybackKey(messageIdentity, String(index), block.content)
                        : "";
                    return (
                        <MessageBlockItem
                            key={block.id}
                            block={block}
                            isStreaming={isStreaming}
                            speaking={Boolean(voiceKey) && speakingKey === voiceKey}
                            onSpeak={voiceKey && onSpeakVoice ? () => onSpeakVoice(block.content, voiceKey) : undefined}
                            onOpenArtifact={onOpenArtifact}
                        />
                    );
                })}
            </View>
        );
    }

    if (node.kind === "execution") {
        const executionNode = node as PhoneUiExecutionNode;

        if (executionNode.executionType === "reasoning") {
            const block: PhoneContentBlock = {
                id: `${executionNode.id}-thinking`,
                type: "thinking",
                content: String(executionNode.content || ""),
                data: executionNode.time ? { elapsedTime: executionNode.time } : undefined,
            };
            return <MessageBlockItem block={block} />;
        }

        if (executionNode.executionType === "tool_call" || executionNode.executionType === "tool_result") {
            return renderExecutionTool(executionNode);
        }

        if (executionNode.executionType === "agent_start") {
            return null;
        }

        if (executionNode.executionType === "runtime_progress") {
            return (
                <View
                    style={[
                        styles.progressPill,
                        {
                            backgroundColor: colors.surfaceStrong,
                            borderColor: colors.border,
                        },
                    ]}
                >
                    <View style={[styles.progressDot, { backgroundColor: colors.primary }]} />
                    <Text style={[styles.progressText, { color: colors.textMuted }]}>
                        {executionNode.topic && !String(executionNode.topic).startsWith("extension.")
                            ? `[${executionNode.topic}] `
                            : ""}
                        {executionNode.label || t("运行中…", "Running...")}
                    </Text>
                </View>
            );
        }
    }

    return (
        <MessageBlockItem
            node={node}
            isStreaming={isStreaming}
            onOpenArtifact={onOpenArtifact}
        />
    );
});

const styles = StyleSheet.create({
    stack: {
        gap: spacing.sm,
    },
    progressPill: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 8,
        borderWidth: 1,
        borderRadius: 12,
        paddingHorizontal: 10,
        paddingVertical: 8,
    },
    progressDot: {
        width: 6,
        height: 6,
        borderRadius: 999,
        marginTop: 6,
    },
    progressText: {
        flex: 1,
        fontSize: 11,
        lineHeight: 18,
    },
});
