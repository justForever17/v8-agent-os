import { memo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { Button } from "@/src/components/ui/button";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type ArtifactType = "code" | "markdown" | "html" | "image" | "video" | "audio" | "document" | "file";

function resolveIcon(type: ArtifactType) {
    switch (type) {
        case "code":
            return "code-tags";
        case "html":
            return "view-dashboard-outline";
        case "image":
            return "image-outline";
        case "video":
            return "video-outline";
        case "audio":
            return "music-note-outline";
        case "markdown":
        case "document":
            return "file-document-outline";
        case "file":
        default:
            return "link-variant";
    }
}

function resolveTone(type: ArtifactType) {
    switch (type) {
        case "code":
            return { background: "rgba(59,130,246,0.10)", border: "rgba(59,130,246,0.24)", color: "#2563EB" };
        case "html":
            return { background: "rgba(249,115,22,0.10)", border: "rgba(249,115,22,0.24)", color: "#EA580C" };
        case "image":
            return { background: "rgba(236,72,153,0.10)", border: "rgba(236,72,153,0.24)", color: "#DB2777" };
        case "video":
            return { background: "rgba(139,92,246,0.10)", border: "rgba(139,92,246,0.24)", color: "#7C3AED" };
        case "audio":
            return { background: "rgba(245,158,11,0.10)", border: "rgba(245,158,11,0.24)", color: "#D97706" };
        default:
            return { background: "rgba(16,185,129,0.10)", border: "rgba(16,185,129,0.24)", color: "#059669" };
    }
}

export const ArtifactCard = memo(function ArtifactCard({
    title,
    type,
    subtitle,
    onPress,
    onDownload,
}: {
    title: string;
    type: ArtifactType;
    subtitle?: string;
    onPress?: () => void;
    onDownload?: () => void;
}) {
    const { colors, t } = useUiPrefs();
    const tone = resolveTone(type);

    return (
        <View
            style={[
                styles.card,
                {
                    backgroundColor: colors.surfaceStrong,
                    borderColor: colors.border,
                },
            ]}
        >
            <View style={[styles.iconWrap, { backgroundColor: tone.background, borderColor: tone.border }]}>
                <MaterialCommunityIcons name={resolveIcon(type)} size={20} color={tone.color} />
            </View>

            <Pressable
                disabled={!onPress}
                onPress={onPress}
                style={({ pressed }) => [
                    styles.body,
                    {
                        opacity: pressed ? 0.86 : 1,
                    },
                ]}
            >
                <Text style={[styles.title, { color: colors.text }]} numberOfLines={1}>
                    {title}
                </Text>
                <Text style={[styles.subtitle, { color: colors.textMuted }]} numberOfLines={1}>
                    {subtitle || t("点击查看产物内容", "Tap to inspect artifact")}
                </Text>
            </Pressable>

            <View style={styles.actions}>
                {onPress ? (
                    <Button variant="outline" size="sm" onPress={onPress} style={styles.actionButton}>
                        <MaterialCommunityIcons name="arrow-expand" size={14} color={colors.textMuted} />
                        <Text style={[styles.actionText, { color: colors.textMuted }]}>{t("预览", "Preview")}</Text>
                    </Button>
                ) : null}
                {onDownload ? (
                    <Button variant="outline" size="sm" onPress={onDownload} style={styles.actionButton}>
                        <MaterialCommunityIcons name="download" size={14} color={colors.textMuted} />
                        <Text style={[styles.actionText, { color: colors.textMuted }]}>{t("下载", "Download")}</Text>
                    </Button>
                ) : null}
            </View>
        </View>
    );
});

const styles = StyleSheet.create({
    card: {
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
        borderRadius: radii.lg,
        borderWidth: 1,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
    },
    iconWrap: {
        width: 40,
        height: 40,
        borderRadius: radii.sm,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    body: {
        flex: 1,
        gap: 2,
    },
    title: {
        fontSize: 14,
        lineHeight: 18,
        fontWeight: "600",
    },
    subtitle: {
        fontSize: 12,
        lineHeight: 16,
    },
    actions: {
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.xs,
    },
    actionButton: {
        minHeight: 32,
        paddingHorizontal: 10,
    },
    actionText: {
        fontSize: 12,
        lineHeight: 16,
        fontWeight: "500",
    },
});
