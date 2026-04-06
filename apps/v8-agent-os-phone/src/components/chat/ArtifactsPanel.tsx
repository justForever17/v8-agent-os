import { memo, useEffect, useMemo, useState } from "react";
import {
    Image,
    Linking,
    Modal,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    View,
    useWindowDimensions,
} from "react-native";
import { WebView } from "react-native-webview";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";

import { Button } from "@/src/components/ui/button";
import { Card, CardContent } from "@/src/components/ui/card";
import { getArtifactContentUrl } from "@/src/lib/phone-api";
import { normalizeRenderableWorkspaceUrl } from "@/src/lib/workspace-links";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ArtifactDetail } from "@/src/types/admin";

type ArtifactsPanelProps = {
    visible: boolean;
    artifacts: ArtifactDetail[];
    activeArtifactId?: string | null;
    onSelectArtifact?: (artifactId: string) => void;
    onClose: () => void;
};

function inferArtifactKind(artifact: ArtifactDetail) {
    const kind = String(artifact.kind || "").trim().toLowerCase();
    const mime = String(artifact.mimeType || "").trim().toLowerCase();
    if (kind) {
        return kind;
    }
    if (mime.startsWith("image/")) return "image";
    if (mime.startsWith("video/")) return "video";
    if (mime.startsWith("audio/")) return "audio";
    if (mime.includes("html")) return "html";
    return "document";
}

function resolveArtifactPreviewUrl(adminBaseUrl: string, artifact: ArtifactDetail) {
    if (artifact.id || artifact.artifactId) {
        return getArtifactContentUrl(adminBaseUrl, artifact.id || artifact.artifactId || "");
    }
    const previewCandidate = normalizeRenderableWorkspaceUrl(
        adminBaseUrl,
        artifact.previewUrl || artifact.externalUrl || "",
    );
    if (previewCandidate) {
        return previewCandidate;
    }
    return normalizeRenderableWorkspaceUrl(
        adminBaseUrl,
        artifact.workspacePath || artifact.sourcePath || "",
    );
}

export const ArtifactsPanel = memo(function ArtifactsPanel({
    visible,
    artifacts,
    activeArtifactId,
    onSelectArtifact,
    onClose,
}: ArtifactsPanelProps) {
    const { adminBaseUrl } = useAppSession();
    const { colors, themeMode, t } = useUiPrefs();
    const { width } = useWindowDimensions();
    const [isExpanded, setIsExpanded] = useState(false);
    const [internalActiveId, setInternalActiveId] = useState<string | null>(activeArtifactId || artifacts[0]?.id || null);
    const isNarrow = width < 860;

    useEffect(() => {
        if (activeArtifactId) {
            setInternalActiveId(activeArtifactId);
            return;
        }
        if (artifacts.length > 0) {
            setInternalActiveId((current) => current || artifacts[0].id);
        } else {
            setInternalActiveId(null);
        }
    }, [activeArtifactId, artifacts]);

    const activeArtifact = useMemo(() => {
        if (!internalActiveId) {
            return artifacts[0] || null;
        }
        return artifacts.find((item) => item.id === internalActiveId) || artifacts[0] || null;
    }, [artifacts, internalActiveId]);

    if (!visible || !activeArtifact) {
        return null;
    }

    const kind = inferArtifactKind(activeArtifact);
    const previewUrl = resolveArtifactPreviewUrl(adminBaseUrl, activeArtifact);
    const openExternal = async () => {
        if (!previewUrl) {
            return;
        }
        await Linking.openURL(previewUrl);
    };

    return (
        <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
            <View style={[styles.overlay, { backgroundColor: themeMode === "dark" ? "rgba(0,0,0,0.58)" : "rgba(15,23,42,0.38)" }]}>
                <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
                <Card
                    style={[
                        styles.panel,
                        isExpanded ? styles.panelExpanded : undefined,
                        {
                            backgroundColor: colors.surfaceStrong,
                            borderColor: colors.border,
                        },
                    ]}
                >
                    <LinearGradient
                        colors={
                            themeMode === "dark"
                                ? ["rgba(24,24,27,0.985)", "rgba(15,15,18,0.975)"]
                                : ["rgba(255,255,255,0.98)", "rgba(247,244,238,0.97)"]
                        }
                        style={StyleSheet.absoluteFill}
                    />
                    <View style={[styles.header, { borderBottomColor: colors.border }]}>
                        <View style={styles.headerMain}>
                            <View style={[styles.headerIcon, { backgroundColor: themeMode === "dark" ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.90)", borderColor: colors.border }]}>
                                <MaterialCommunityIcons
                                    name={kind === "image" ? "image-outline" : kind === "video" ? "video-outline" : kind === "audio" ? "music-note-outline" : kind === "html" ? "language-html5" : "file-outline"}
                                    size={17}
                                    color={colors.textMuted}
                                />
                            </View>
                            <View style={styles.headerText}>
                                <Text style={[styles.title, { color: colors.text }]} numberOfLines={1}>
                                    {activeArtifact.displayLabel || activeArtifact.title || t("产物", "Artifact")}
                                </Text>
                                <Text style={[styles.subtitle, { color: colors.textMuted }]} numberOfLines={1}>
                                    {activeArtifact.displaySubtitle || activeArtifact.workspacePath || activeArtifact.sourcePath || kind}
                                </Text>
                            </View>
                        </View>
                        <View style={styles.headerActions}>
                            <Button variant="ghost" size="icon" onPress={() => setIsExpanded((current) => !current)}>
                                <MaterialCommunityIcons name={isExpanded ? "arrow-collapse" : "arrow-expand"} size={18} color={colors.textMuted} />
                            </Button>
                            <Button variant="ghost" size="icon" onPress={onClose}>
                                <MaterialCommunityIcons name="close" size={18} color={colors.textMuted} />
                            </Button>
                        </View>
                    </View>

                    {artifacts.length > 1 ? (
                        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabsRow}>
                            {artifacts.map((artifact) => {
                                const selected = artifact.id === activeArtifact.id;
                                return (
                                    <Pressable
                                        key={artifact.id}
                                        style={[
                                            styles.tabButton,
                                            {
                                                backgroundColor: selected ? colors.surface : "transparent",
                                                borderColor: selected ? colors.border : "transparent",
                                            },
                                        ]}
                                        onPress={() => {
                                            setInternalActiveId(artifact.id);
                                            onSelectArtifact?.(artifact.id);
                                        }}
                                    >
                                        <Text style={[styles.tabText, { color: selected ? colors.text : colors.textMuted }]}>
                                            {artifact.displayLabel || artifact.title || artifact.id}
                                        </Text>
                                    </Pressable>
                                );
                            })}
                        </ScrollView>
                    ) : null}

                    <View style={[styles.body, isNarrow && styles.bodyNarrow]}>
                        <View style={styles.previewColumn}>
                            <Card style={[styles.previewCard, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                                <CardContent style={styles.previewContent}>
                                    {kind === "image" && previewUrl ? (
                                        <Image source={{ uri: previewUrl }} resizeMode="contain" style={styles.imagePreview} />
                                    ) : null}
                                    {kind === "html" && previewUrl ? (
                                        <WebView source={{ uri: previewUrl }} style={styles.webPreview} />
                                    ) : null}
                                    {kind === "video" && previewUrl ? (
                                        <WebView source={{ uri: previewUrl }} style={styles.webPreview} />
                                    ) : null}
                                    {kind === "audio" && previewUrl ? (
                                        <View style={styles.placeholderWrap}>
                                            <MaterialCommunityIcons name="music-circle-outline" size={42} color={colors.primary} />
                                            <Text style={[styles.placeholderTitle, { color: colors.text }]}>
                                                {t("音频产物", "Audio artifact")}
                                            </Text>
                                            <Text style={[styles.placeholderSubtitle, { color: colors.textMuted }]}>
                                                {t("当前使用外部链接播放音频", "Open externally to play audio")}
                                            </Text>
                                        </View>
                                    ) : null}
                                    {!previewUrl || !["image", "html", "video", "audio"].includes(kind) ? (
                                        <View style={styles.placeholderWrap}>
                                            <MaterialCommunityIcons name="file-search-outline" size={42} color={colors.textSoft} />
                                            <Text style={[styles.placeholderTitle, { color: colors.text }]}>
                                                {t("暂无内联预览", "No inline preview")}
                                            </Text>
                                            <Text style={[styles.placeholderSubtitle, { color: colors.textMuted }]}>
                                                {t("可通过外部链接打开该产物。", "Open the artifact via its external URL.")}
                                            </Text>
                                        </View>
                                    ) : null}
                                </CardContent>
                            </Card>
                        </View>

                        <View style={[styles.metaColumn, isNarrow && styles.metaColumnNarrow]}>
                            <Card style={[styles.metaCard, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                                <CardContent style={styles.metaContent}>
                                    <View style={styles.metaSection}>
                                        <Text style={[styles.metaLabel, { color: colors.textSoft }]}>Artifact ID</Text>
                                        <Text style={[styles.metaValue, { color: colors.text }]}>{activeArtifact.id}</Text>
                                    </View>
                                    <View style={styles.metaSection}>
                                        <Text style={[styles.metaLabel, { color: colors.textSoft }]}>Session / Run / Message</Text>
                                        <Text style={[styles.metaValue, { color: colors.text }]}>{activeArtifact.sessionId || "—"}</Text>
                                        <Text style={[styles.metaValue, { color: colors.text }]}>{activeArtifact.runId || "—"}</Text>
                                        <Text style={[styles.metaValue, { color: colors.text }]}>{activeArtifact.messageId || "—"}</Text>
                                    </View>
                                    <View style={styles.metaSection}>
                                        <Text style={[styles.metaLabel, { color: colors.textSoft }]}>Paths</Text>
                                        <Text style={[styles.metaValue, { color: colors.text }]}>{activeArtifact.workspacePath || "—"}</Text>
                                        <Text style={[styles.metaValue, { color: colors.textMuted }]}>{activeArtifact.sourcePath || "—"}</Text>
                                    </View>
                                    <View style={styles.metaActions}>
                                        <Button variant="outline" onPress={() => void openExternal()} disabled={!previewUrl}>
                                            {t("打开链接", "Open")}
                                        </Button>
                                    </View>
                                </CardContent>
                            </Card>
                        </View>
                    </View>
                </Card>
            </View>
        </Modal>
    );
});

const styles = StyleSheet.create({
    overlay: {
        flex: 1,
        justifyContent: "center",
        alignItems: "center",
        paddingHorizontal: 14,
        paddingVertical: 24,
    },
    panel: {
        width: "92%",
        maxWidth: 920,
        maxHeight: "84%",
        borderRadius: 24,
        overflow: "hidden",
        borderWidth: 1,
        shadowColor: "#0F172A",
        shadowOpacity: 0.18,
        shadowRadius: 28,
        shadowOffset: { width: 0, height: 16 },
        elevation: 22,
    },
    panelExpanded: {
        width: "96%",
        maxWidth: 1120,
        maxHeight: "94%",
    },
    header: {
        minHeight: 68,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: spacing.sm,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
        borderBottomWidth: StyleSheet.hairlineWidth,
    },
    headerMain: {
        flex: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
        minWidth: 0,
    },
    headerIcon: {
        width: 36,
        height: 36,
        borderRadius: 14,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    headerText: {
        flex: 1,
        gap: 2,
        minWidth: 0,
    },
    title: {
        fontSize: 15,
        fontWeight: "900",
        letterSpacing: -0.2,
    },
    subtitle: {
        fontSize: 11,
        lineHeight: 16,
    },
    headerActions: {
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.xs,
    },
    tabsRow: {
        gap: spacing.xs,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
    },
    tabButton: {
        maxWidth: 180,
        minHeight: 30,
        borderRadius: radii.pill,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 12,
    },
    tabText: {
        fontSize: 12,
        fontWeight: "700",
    },
    body: {
        flex: 1,
        flexDirection: "row",
        gap: spacing.md,
        padding: spacing.md,
    },
    bodyNarrow: {
        flexDirection: "column",
    },
    previewColumn: {
        flex: 1.1,
    },
    metaColumn: {
        width: 300,
        maxWidth: "40%",
    },
    metaColumnNarrow: {
        width: "100%",
        maxWidth: "100%",
    },
    previewCard: {
        flex: 1,
    },
    previewContent: {
        flex: 1,
        minHeight: 320,
        justifyContent: "center",
    },
    imagePreview: {
        width: "100%",
        height: 360,
        borderRadius: radii.lg,
    },
    webPreview: {
        width: "100%",
        height: 360,
        borderRadius: radii.lg,
        overflow: "hidden",
    },
    placeholderWrap: {
        minHeight: 280,
        alignItems: "center",
        justifyContent: "center",
        gap: spacing.sm,
        paddingHorizontal: spacing.lg,
    },
    placeholderTitle: {
        fontSize: 16,
        fontWeight: "800",
    },
    placeholderSubtitle: {
        fontSize: 12,
        lineHeight: 18,
        textAlign: "center",
    },
    metaCard: {
        flex: 1,
    },
    metaContent: {
        gap: spacing.md,
    },
    metaSection: {
        gap: 6,
    },
    metaLabel: {
        fontSize: 10,
        fontWeight: "800",
        letterSpacing: 1.4,
        textTransform: "uppercase",
    },
    metaValue: {
        fontSize: 12,
        lineHeight: 18,
    },
    metaActions: {
        marginTop: spacing.sm,
    },
});
