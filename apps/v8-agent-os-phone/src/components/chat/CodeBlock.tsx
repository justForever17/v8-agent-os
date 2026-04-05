import { memo, useMemo } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type CodeBlockProps = {
    language?: string;
    value?: string;
    content?: string;
    className?: string;
    isStreaming?: boolean;
};

export const CodeBlock = memo(function CodeBlock({
    language = "text",
    value,
    content,
}: CodeBlockProps) {
    const { colors } = useUiPrefs();
    const resolvedContent = useMemo(() => String(value ?? content ?? ""), [content, value]);
    const lines = useMemo(() => resolvedContent.split("\n"), [resolvedContent]);

    return (
        <View style={[styles.wrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
            <View style={[styles.header, { backgroundColor: colors.surface, borderBottomColor: colors.border }]}>
                <Text style={[styles.language, { color: colors.textMuted }]}>{language || "text"}</Text>
            </View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
                <View style={styles.codeStack}>
                    {lines.map((line, index) => (
                        <Text key={`${index}:${line.slice(0, 16)}`} style={[styles.text, { color: colors.textMuted }]}>
                            {line || " "}
                        </Text>
                    ))}
                </View>
            </ScrollView>
        </View>
    );
});

const styles = StyleSheet.create({
    wrap: {
        borderWidth: 1,
        borderRadius: radii.lg,
        overflow: "hidden",
    },
    header: {
        borderBottomWidth: 1,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.xs,
    },
    language: {
        fontSize: 11,
        lineHeight: 14,
        fontWeight: "700",
        letterSpacing: 0.4,
        textTransform: "lowercase",
    },
    scrollContent: {
        minWidth: "100%",
    },
    codeStack: {
        minWidth: "100%",
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
        gap: 2,
    },
    text: {
        fontSize: 12,
        lineHeight: 19,
        fontFamily: "monospace",
    },
});
