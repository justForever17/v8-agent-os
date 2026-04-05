import { memo, useMemo } from "react";
import { StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ChatMessage } from "@/src/types/admin";

type ContextReference = {
    id: string;
    type: "file" | "memory" | "search" | "web";
    label: string;
    details?: string;
};

type ContextReferencesHUDProps = {
    messages: ChatMessage[];
};

function asRecord(value: unknown) {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

export const ContextReferencesHUD = memo(function ContextReferencesHUD({ messages }: ContextReferencesHUDProps) {
    const { colors, themeMode } = useUiPrefs();

    const contextRefs = useMemo(() => {
        const refs = new Map<string, ContextReference>();

        messages.forEach((message) => {
            if (message.role !== "assistant" || !Array.isArray(message.nodes)) {
                return;
            }

            message.nodes.forEach((node) => {
                if (node.kind !== "execution" || node.executionType !== "tool_call") {
                    return;
                }

                const args = asRecord(node.args);
                const toolName = String(node.toolName || "").trim();

                if (["read_file", "view_file", "replace_file_content", "multi_replace_file_content", "write_to_file"].includes(toolName)) {
                    const path = String(args.AbsolutePath || args.TargetFile || args.filePath || "").trim();
                    if (path) {
                        const filename = path.split(/[\\/]/).pop();
                        if (filename) {
                            refs.set(`file-${filename}`, {
                                id: `file-${filename}`,
                                type: "file",
                                label: filename,
                                details: path,
                            });
                        }
                    }
                    return;
                }

                if (["find_by_name", "grep_search", "list_dir"].includes(toolName)) {
                    const query = String(args.Pattern || args.Query || args.SearchDirectory || "").trim();
                    if (query) {
                        const shortQuery = query.length > 15 ? `${query.slice(0, 15)}...` : query;
                        refs.set(`search-${shortQuery}`, {
                            id: `search-${shortQuery}`,
                            type: "search",
                            label: `搜索: ${shortQuery}`,
                            details: `Tool: ${toolName}`,
                        });
                    }
                    return;
                }

                if (toolName === "search_web" || toolName === "read_url_content") {
                    const query = String(args.query || args.Url || "").trim();
                    if (query) {
                        const shortQuery = query.length > 20 ? `${query.slice(0, 20)}...` : query;
                        refs.set(`web-${shortQuery}`, {
                            id: `web-${shortQuery}`,
                            type: "web",
                            label: `网页: ${shortQuery}`,
                        });
                    }
                    return;
                }

                if (toolName === "memory_recall") {
                    const query = String(args.query || "knowledge").trim();
                    const shortQuery = query.length > 15 ? `${query.slice(0, 15)}...` : query;
                    refs.set(`memory-${shortQuery}`, {
                        id: `memory-${shortQuery}`,
                        type: "memory",
                        label: `记忆: ${shortQuery}`,
                    });
                }
            });
        });

        return Array.from(refs.values()).slice(-10);
    }, [messages]);

    if (contextRefs.length === 0) {
        return null;
    }

    const iconName = (type: ContextReference["type"]) => {
        switch (type) {
            case "file":
                return { name: "file-code-outline" as const, color: "#3B82F6" };
            case "search":
                return { name: "magnify" as const, color: "#F59E0B" };
            case "memory":
                return { name: "brain" as const, color: "#8B5CF6" };
            case "web":
                return { name: "database-search-outline" as const, color: "#10B981" };
            default:
                return { name: "file-outline" as const, color: colors.textSoft };
        }
    };

    return (
        <View style={styles.wrap}>
            {contextRefs.map((ref) => {
                const icon = iconName(ref.type);
                return (
                    <View
                        key={ref.id}
                        style={[
                            styles.chip,
                            {
                                backgroundColor: themeMode === "dark" ? "rgba(24,24,27,0.72)" : "rgba(255,255,255,0.92)",
                                borderColor: colors.border,
                                shadowColor: colors.text,
                            },
                        ]}
                    >
                        <MaterialCommunityIcons name={icon.name} size={14} color={icon.color} />
                        <Text style={[styles.label, { color: colors.textMuted }]} numberOfLines={1}>
                            {ref.label}
                        </Text>
                    </View>
                );
            })}
        </View>
    );
});

const styles = StyleSheet.create({
    wrap: {
        flexDirection: "row",
        flexWrap: "wrap",
        justifyContent: "center",
        gap: 8,
        width: "100%",
        marginBottom: spacing.md,
    },
    chip: {
        maxWidth: 180,
        minHeight: 31,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderRadius: radii.pill,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingVertical: 6,
        shadowOpacity: 0.05,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 4 },
        elevation: 2,
    },
    label: {
        flexShrink: 1,
        fontSize: 11,
        fontWeight: "500",
    },
});
