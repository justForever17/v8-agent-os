import { memo, useEffect, useMemo, useState } from "react";
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import * as Clipboard from "expo-clipboard";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { WebView } from "react-native-webview";

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
    const [previewOpen, setPreviewOpen] = useState(false);
    const normalizedLanguage = String(language || "text").trim().toLowerCase();
    const htmlPreviewable = (normalizedLanguage === "html" || normalizedLanguage === "htm") && resolvedContent.trim().length > 0;

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
                <Text style={[styles.language, { color: colors.textMuted }]}>{normalizedLanguage || "text"}</Text>
                <View style={styles.headerActions}>
                    {htmlPreviewable ? (
                        <Pressable style={styles.iconButton} onPress={() => setPreviewOpen(true)}>
                            <MaterialCommunityIcons name="open-in-new" size={15} color={colors.textMuted} />
                        </Pressable>
                    ) : null}
                    <Pressable style={styles.iconButton} onPress={() => void handleCopy()}>
                        <MaterialCommunityIcons
                            name={copied ? "check" : "content-copy"}
                            size={15}
                            color={copied ? colors.success : colors.textMuted}
                        />
                    </Pressable>
                </View>
            </View>
            <View style={styles.viewport}>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
                    <ScrollView
                        nestedScrollEnabled
                        showsVerticalScrollIndicator={false}
                        style={styles.verticalScroll}
                        contentContainerStyle={styles.codeStack}
                    >
                        <Text selectable style={[styles.text, { color: colors.textMuted }]}>
                            {resolvedContent || " "}
                        </Text>
                    </ScrollView>
                </ScrollView>
            </View>
            {htmlPreviewable ? (
                <Modal visible={previewOpen} transparent animationType="fade" onRequestClose={() => setPreviewOpen(false)}>
                    <View style={[styles.overlay, { backgroundColor: colors.overlay }]}>
                        <Pressable style={StyleSheet.absoluteFill} onPress={() => setPreviewOpen(false)} />
                        <View style={[styles.modalCard, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                            <View style={[styles.modalHeader, { backgroundColor: colors.surfaceStrong, borderBottomColor: colors.border }]}>
                                <Text style={[styles.modalTitle, { color: colors.text }]} numberOfLines={1}>
                                    HTML 预览
                                </Text>
                                <Pressable style={styles.iconButton} onPress={() => setPreviewOpen(false)}>
                                    <MaterialCommunityIcons name="close" size={18} color={colors.text} />
                                </Pressable>
                            </View>
                            <View style={styles.previewWrap}>
                                <WebView originWhitelist={["*"]} source={{ html: resolvedContent }} style={styles.previewWebview} />
                            </View>
                        </View>
                    </View>
                </Modal>
            ) : null}
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
    headerActions: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
    },
    iconButton: {
        width: 28,
        height: 28,
        borderRadius: 999,
        alignItems: "center",
        justifyContent: "center",
    },
    viewport: {
        maxHeight: 236,
    },
    scrollContent: {
        minWidth: "100%",
    },
    verticalScroll: {
        maxHeight: 236,
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
    overlay: {
        flex: 1,
        justifyContent: "center",
        paddingHorizontal: 12,
        paddingVertical: 24,
    },
    modalCard: {
        borderWidth: 1,
        borderRadius: 22,
        overflow: "hidden",
        maxHeight: "90%",
    },
    modalHeader: {
        minHeight: 52,
        paddingHorizontal: 14,
        paddingVertical: 10,
        borderBottomWidth: 1,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: spacing.sm,
    },
    modalTitle: {
        flex: 1,
        fontSize: 14,
        lineHeight: 18,
        fontWeight: "700",
    },
    previewWrap: {
        minHeight: 420,
    },
    previewWebview: {
        minHeight: 420,
        backgroundColor: "transparent",
    },
});
