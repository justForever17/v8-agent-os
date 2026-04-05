import { useCallback, useEffect, useMemo, useState } from "react";
import {
    Alert,
    Linking,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { Redirect, router, useLocalSearchParams, type Href } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { GlassCard } from "@/src/components/common/GlassCard";
import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { openCachedFile, saveResponseToCache } from "@/src/lib/file-transfer";
import {
    fetchArtifactContentResponse,
    fetchWorkspaceFileResponse,
    getArtifact,
    listArtifacts,
} from "@/src/lib/phone-api";
import { formatClock, formatRelativeTime } from "@/src/lib/time";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { ArtifactDetail } from "@/src/types/admin";

function normalizeParam(value: string | string[] | undefined) {
    if (Array.isArray(value)) return value[0] || "";
    return value || "";
}

export default function ArtifactsScreen() {
    const { status, adminBaseUrl, authorizedFetch } = useAppSession();
    const { t, locale } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const params = useLocalSearchParams<{ conversationId?: string | string[]; artifactId?: string | string[] }>();
    const conversationId = normalizeParam(params.conversationId).trim();
    const artifactId = normalizeParam(params.artifactId).trim();

    const [loading, setLoading] = useState(true);
    const [opening, setOpening] = useState(false);
    const [artifacts, setArtifacts] = useState<ArtifactDetail[]>([]);
    const [selectedArtifact, setSelectedArtifact] = useState<ArtifactDetail | null>(null);

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "sessions", icon: "view-headline", onPress: () => router.push("/sessions" as Href) },
        { key: "approvals", icon: "bell-outline", onPress: () => router.push("/approvals" as Href) },
        { key: "settings", icon: "cog-outline", onPress: () => router.push("/settings" as Href) },
    ];

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const list = conversationId ? await listArtifacts(authorizedFetch, conversationId) : [];
            let nextSelected = list.find((item) => item.id === artifactId) || null;
            if (artifactId) {
                const detail = await getArtifact(authorizedFetch, artifactId);
                nextSelected = detail;
                if (!list.some((item) => item.id === detail.id)) {
                    list.unshift(detail);
                }
            }
            setArtifacts(list);
            setSelectedArtifact(nextSelected || list[0] || null);
        } catch (error) {
            Alert.alert(t("读取失败", "Load failed"), error instanceof Error ? error.message : t("无法读取产物信息", "Unable to load artifact details"));
        } finally {
            setLoading(false);
        }
    }, [artifactId, authorizedFetch, conversationId, t]);

    useEffect(() => {
        if (status === "authenticated") {
            void load();
        }
    }, [load, status]);

    const selectedTitle = useMemo(
        () => selectedArtifact?.title || selectedArtifact?.displayLabel || selectedArtifact?.kind || t("产物", "Artifacts"),
        [selectedArtifact, t],
    );

    const openSelectedArtifact = useCallback(async () => {
        if (!selectedArtifact) return;
        setOpening(true);
        try {
            const directUrl = selectedArtifact.externalUrl || selectedArtifact.previewUrl;
            if (directUrl) {
                await Linking.openURL(resolveAdminAssetUrl(adminBaseUrl, directUrl));
                return;
            }

            if (selectedArtifact.id) {
                const response = await fetchArtifactContentResponse(authorizedFetch, selectedArtifact.id);
                const cached = await saveResponseToCache(response, {
                    prefix: `artifact-${selectedArtifact.id}`,
                });
                const opened = await openCachedFile(cached.uri);
                if (!opened) {
                    Alert.alert(t("已缓存产物", "Artifact cached"), `${t("文件已保存到", "File saved to")}：${cached.uri}`);
                }
                return;
            }

            const workspacePath = String(
                selectedArtifact.workspacePath
                || selectedArtifact.sourcePath?.replace(/^\/workspace\//, "")
                || "",
            ).trim();
            if (workspacePath) {
                const response = await fetchWorkspaceFileResponse(authorizedFetch, workspacePath);
                const cached = await saveResponseToCache(response, {
                    prefix: `workspace-${workspacePath.split("/").pop() || "file"}`,
                });
                const opened = await openCachedFile(cached.uri);
                if (!opened) {
                    Alert.alert(t("已缓存文件", "File cached"), `${t("文件已保存到", "File saved to")}：${cached.uri}`);
                }
                return;
            }

            throw new Error(t("当前产物没有可打开的内容地址", "This artifact does not expose any openable content URL."));
        } catch (error) {
            Alert.alert(t("打开失败", "Open failed"), error instanceof Error ? error.message : t("无法打开这个产物", "Unable to open this artifact"));
        } finally {
            setOpening(false);
        }
    }, [adminBaseUrl, authorizedFetch, selectedArtifact, t]);

    if (status === "booting") {
        return <LoadingScreen label={t("正在读取产物…", "Loading artifacts...")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    return (
        <LinearGradient colors={[colors.background, "#FFF7ED"]} style={styles.gradient}>
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar actions={actions} onBrandPress={() => void goHomeToChat()} />

                {loading ? (
                    <LoadingScreen label={t("同步产物中…", "Syncing artifacts...")} />
                ) : (
                    <ScrollView contentContainerStyle={styles.content}>
                        <GlassCard>
                            <View style={styles.sectionHeader}>
                                <Text style={styles.sectionTitle}>{selectedTitle}</Text>
                                <Text style={styles.sectionMeta}>{locale === "en" ? `${artifacts.length} items` : `${artifacts.length} 个`}</Text>
                            </View>
                            {artifacts.length === 0 ? (
                                <Text style={styles.emptyText}>{t("当前会话还没有产物，等 Supervisor 生成后这里会自动出现。", "This conversation has no artifacts yet. New ones will appear here once the supervisor generates them.")}</Text>
                            ) : (
                                artifacts.map((artifact) => {
                                    const active = selectedArtifact?.id === artifact.id;
                                    return (
                                        <Pressable
                                            key={artifact.id}
                                            style={[styles.itemCard, active && styles.itemCardActive]}
                                            onPress={() => setSelectedArtifact(artifact)}
                                        >
                                            <View style={[styles.kindDot, active && styles.kindDotActive]} />
                                            <View style={styles.itemBody}>
                                                <Text style={styles.itemTitle} numberOfLines={1}>
                                                    {artifact.title || artifact.displayLabel || artifact.kind || artifact.id}
                                                </Text>
                                                <Text style={styles.itemSubtitle} numberOfLines={1}>
                                                    {artifact.displaySubtitle || artifact.kind || artifact.workspacePath || artifact.sourcePath || t("产物", "Artifact")}
                                                </Text>
                                            </View>
                                            <Text style={styles.itemTime}>
                                                {formatRelativeTime(artifact.createdAt, locale)}
                                            </Text>
                                        </Pressable>
                                    );
                                })
                            )}
                        </GlassCard>

                        <GlassCard>
                            <View style={styles.sectionHeader}>
                                <Text style={styles.sectionTitle}>{t("详情", "Details")}</Text>
                                <Pressable
                                    style={[styles.primaryButton, opening && styles.disabled]}
                                    onPress={() => void openSelectedArtifact()}
                                    disabled={!selectedArtifact || opening}
                                >
                                    <MaterialCommunityIcons name={opening ? "loading" : "open-in-new"} size={16} color="#FFFFFF" />
                                    <Text style={styles.primaryButtonText}>{opening ? t("打开中…", "Opening...") : t("打开内容", "Open content")}</Text>
                                </Pressable>
                            </View>
                            {!selectedArtifact ? (
                                <Text style={styles.emptyText}>{t("先从上面的列表里选一个产物查看详情。", "Select an artifact above to inspect its details.")}</Text>
                            ) : (
                                <View style={styles.detailList}>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>{t("标题", "Title")}</Text>
                                        <Text style={styles.detailValue}>{selectedArtifact.title || selectedArtifact.displayLabel || t("未命名产物", "Untitled artifact")}</Text>
                                    </View>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>{t("类型", "Type")}</Text>
                                        <Text style={styles.detailValue}>{selectedArtifact.kind || t("未知", "Unknown")}</Text>
                                    </View>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>{t("创建时间", "Created")}</Text>
                                        <Text style={styles.detailValue}>{formatClock(selectedArtifact.createdAt, locale) || t("未知", "Unknown")}</Text>
                                    </View>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>{t("会话", "Conversation")}</Text>
                                        <Text style={styles.detailValue}>{selectedArtifact.sessionId || conversationId || t("未知", "Unknown")}</Text>
                                    </View>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>Run</Text>
                                        <Text style={styles.detailValue}>{selectedArtifact.runId || t("无", "None")}</Text>
                                    </View>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>{t("消息", "Message")}</Text>
                                        <Text style={styles.detailValue}>{selectedArtifact.messageId || t("无", "None")}</Text>
                                    </View>
                                    <View style={styles.detailRow}>
                                        <Text style={styles.detailLabel}>{t("工作区路径", "Workspace path")}</Text>
                                        <Text style={styles.detailMono}>{selectedArtifact.workspacePath || selectedArtifact.sourcePath || t("无", "None")}</Text>
                                    </View>
                                    {selectedArtifact.metadata ? (
                                        <View style={styles.metaCard}>
                                            <Text style={styles.detailLabel}>Metadata</Text>
                                            <Text style={styles.detailMono}>
                                                {JSON.stringify(selectedArtifact.metadata, null, 2)}
                                            </Text>
                                        </View>
                                    ) : null}
                                </View>
                            )}
                        </GlassCard>
                    </ScrollView>
                )}
            </SafeAreaView>
        </LinearGradient>
    );
}

const styles = StyleSheet.create({
    gradient: { flex: 1 },
    safeArea: { flex: 1 },
    content: {
        paddingHorizontal: spacing.lg,
        paddingBottom: spacing.xl,
        gap: spacing.md,
    },
    sectionHeader: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: spacing.md,
        gap: spacing.sm,
    },
    sectionTitle: {
        color: colors.text,
        fontSize: 16,
        fontWeight: "900",
    },
    sectionMeta: {
        color: colors.textSoft,
        fontSize: 12,
        fontWeight: "800",
    },
    emptyText: {
        color: colors.textMuted,
        fontSize: 14,
        lineHeight: 22,
    },
    itemCard: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        borderRadius: radii.md,
        backgroundColor: colors.surface,
        borderWidth: 1,
        borderColor: colors.border,
        paddingHorizontal: 12,
        paddingVertical: 12,
        marginBottom: spacing.sm,
    },
    itemCardActive: {
        borderColor: "rgba(124,58,237,0.26)",
        backgroundColor: colors.primarySoft,
    },
    kindDot: {
        width: 10,
        height: 10,
        borderRadius: 5,
        backgroundColor: colors.textSoft,
    },
    kindDotActive: {
        backgroundColor: colors.primary,
    },
    itemBody: {
        flex: 1,
        gap: 4,
    },
    itemTitle: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "800",
    },
    itemSubtitle: {
        color: colors.textMuted,
        fontSize: 12,
    },
    itemTime: {
        color: colors.textSoft,
        fontSize: 11,
        fontWeight: "700",
    },
    detailList: {
        gap: spacing.sm,
    },
    detailRow: {
        gap: 4,
    },
    detailLabel: {
        color: colors.textSoft,
        fontSize: 11,
        fontWeight: "800",
        letterSpacing: 0.8,
        textTransform: "uppercase",
    },
    detailValue: {
        color: colors.text,
        fontSize: 14,
        lineHeight: 21,
    },
    detailMono: {
        color: colors.text,
        fontSize: 12,
        lineHeight: 20,
        fontFamily: "monospace",
    },
    metaCard: {
        marginTop: spacing.sm,
        borderRadius: radii.md,
        backgroundColor: colors.surface,
        borderWidth: 1,
        borderColor: colors.border,
        paddingHorizontal: 12,
        paddingVertical: 12,
        gap: 8,
    },
    primaryButton: {
        minHeight: 38,
        borderRadius: radii.md,
        backgroundColor: colors.primary,
        paddingHorizontal: 12,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
    },
    primaryButtonText: {
        color: "#FFFFFF",
        fontSize: 13,
        fontWeight: "800",
    },
    disabled: {
        opacity: 0.6,
    },
});
