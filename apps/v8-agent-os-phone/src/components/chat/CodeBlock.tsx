import { memo, useEffect, useMemo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import * as Clipboard from "expo-clipboard";
import { MaterialCommunityIcons } from "@expo/vector-icons";

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
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        if (!copied) {
            return undefined;
        }
        const timer = setTimeout(() => setCopied(false), 1600);
        return () => clearTimeout(timer);
    }, [copied]);

    const handleCopy = async () => {
        if (!resolvedContent.trim()) {
            return;
        }
        await Clipboard.setStringAsync(resolvedContent);
        setCopied(true);
    };

    return (
        <View style={[styles.wrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
            <View style={[styles.header, { backgroundColor: colors.surface, borderBottomColor: colors.border }]}>
                <Text style={[styles.language, { color: colors.textMuted }]}>{language || "text"}</Text>
                <Pressable style={styles.copyButton} onPress={() => void handleCopy()}>
                    <MaterialCommunityIcons
                        name={copied ? "check" : "content-copy"}
                        size={14}
                        color={copied ? colors.success : colors.textMuted}
                    />
                    <Text style={[styles.copyLabel, { color: copied ? colors.success : colors.textMuted }]}>
                        {copied ? "已复制" : "复制"}
                    </Text>
                </Pressable>
            </View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
                <View style={styles.codeStack}>
                    <Text selectable style={[styles.text, { color: colors.textMuted }]}>
                        {resolvedContent || " "}
                    </Text>
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
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: spacing.sm,
    },
    language: {
        fontSize: 11,
        lineHeight: 14,
        fontWeight: "700",
        letterSpacing: 0.4,
        textTransform: "lowercase",
    },
    copyButton: {
        flexDirection: "row",
        alignItems: "center",
        gap: 5,
        paddingVertical: 2,
    },
    copyLabel: {
        fontSize: 11,
        lineHeight: 14,
        fontWeight: "700",
    },
    scrollContent: {
        minWidth: "100%",
    },
    codeStack: {
        minWidth: "100%",
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
    },
    text: {
        fontSize: 12,
        lineHeight: 19,
        fontFamily: "monospace",
    },
});
