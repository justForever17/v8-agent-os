import { Redirect, router, type Href } from "expo-router";
import { useEffect, useState } from "react";
import {
    Alert,
    ActivityIndicator,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    TextInput,
    View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { GlassCard } from "@/src/components/common/GlassCard";
import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { getConnectionSummary, listProjects } from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { ConnectionSummary, ProjectSummary } from "@/src/types/admin";

export default function ConnectScreen() {
    const { status, user, adminBaseUrl, setAdminBaseUrl, signOut, authorizedFetch } = useAppSession();
    const { t } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const [draftBaseUrl, setDraftBaseUrl] = useState(adminBaseUrl);
    const [busy, setBusy] = useState(false);
    const [summary, setSummary] = useState<ConnectionSummary | null>(null);
    const [projects, setProjects] = useState<ProjectSummary[]>([]);
    const [refreshing, setRefreshing] = useState(false);

    useEffect(() => {
        setDraftBaseUrl(adminBaseUrl);
    }, [adminBaseUrl]);

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "desktop-live", icon: "monitor-dashboard", onPress: () => router.push("/desktop-live" as Href) },
        { key: "rpa", icon: "robot-outline", onPress: () => router.push("/rpa" as Href) },
        { key: "settings", icon: "cog-outline", onPress: () => router.push("/settings" as Href) },
    ];

    useEffect(() => {
        if (status !== "authenticated") {
            return;
        }
        let cancelled = false;
        const hydrate = async () => {
            setRefreshing(true);
            try {
                const [nextSummary, nextProjects] = await Promise.all([
                    getConnectionSummary(authorizedFetch),
                    listProjects(authorizedFetch).catch(() => []),
                ]);
                if (!cancelled) {
                    setSummary(nextSummary);
                    setProjects(nextProjects);
                }
            } catch (error) {
                if (!cancelled) {
                    Alert.alert(t("读取失败", "Load failed"), error instanceof Error ? error.message : t("无法读取连接摘要", "Unable to load the connection summary"));
                }
            } finally {
                if (!cancelled) {
                    setRefreshing(false);
                }
            }
        };
        void hydrate();
        return () => {
            cancelled = true;
        };
    }, [authorizedFetch, status, t]);

    if (status === "booting") {
        return <LoadingScreen label={t("正在读取连接信息…", "Loading connection details...")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    const saveAndReconnect = async () => {
        const nextUrl = draftBaseUrl.trim();
        if (!nextUrl) {
            Alert.alert(t("保存失败", "Save failed"), t("请先填写可访问的 Admin 地址", "Please enter a reachable Admin URL first."));
            return;
        }
        if (nextUrl === adminBaseUrl) {
            Alert.alert(t("无需切换", "No change needed"), t("当前手机端已经连接到这个 Admin 地址。", "Phone is already connected to this Admin URL."));
            return;
        }
        setBusy(true);
        try {
            await setAdminBaseUrl(nextUrl);
            await signOut();
            router.replace("/login");
        } catch (error) {
            Alert.alert(t("切换失败", "Switch failed"), error instanceof Error ? error.message : t("无法切换连接地址", "Unable to switch the connection URL."));
        } finally {
            setBusy(false);
        }
    };

    return (
        <LinearGradient
            colors={[colors.background, "#FFF7ED"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.gradient}
        >
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar actions={actions} onBrandPress={() => void goHomeToChat()} />

                <ScrollView contentContainerStyle={styles.content}>
                    <GlassCard>
                        <View style={styles.sectionTitleRow}>
                            <Text style={styles.sectionTitle}>{t("当前连接", "Current connection")}</Text>
                            {refreshing ? <ActivityIndicator color={colors.primary} size="small" /> : null}
                        </View>
                        <View style={styles.summaryRow}>
                            <Text style={styles.summaryLabel}>Admin</Text>
                            <Text style={styles.summaryValue}>{summary?.connection?.adminBaseUrl || adminBaseUrl || t("未连接", "Not connected")}</Text>
                        </View>
                        <View style={styles.summaryRow}>
                            <Text style={styles.summaryLabel}>{t("Bridge 模式", "Bridge mode")}</Text>
                            <Text style={styles.summaryValue}>{summary?.connection?.bridgeMode || "unknown"}</Text>
                        </View>
                        <View style={styles.summaryRow}>
                            <Text style={styles.summaryLabel}>Engine</Text>
                            <Text style={styles.summaryValue}>{summary?.connection?.engineBaseUrl || t("未知", "Unknown")}</Text>
                        </View>
                        <View style={styles.summaryRow}>
                            <Text style={styles.summaryLabel}>{t("桌面桥接", "Desktop bridge")}</Text>
                            <Text style={styles.summaryValue}>{summary?.connection?.desktopLiveBridgeBaseUrl || t("未启用", "Disabled")}</Text>
                        </View>
                        <View style={styles.summaryRow}>
                            <Text style={styles.summaryLabel}>{t("连通性", "Reachability")}</Text>
                            <Text style={[styles.summaryValue, summary?.connection?.reachable ? styles.okText : styles.warnText]}>
                                {summary?.connection?.reachable ? t("已连接", "Reachable") : t("未验证", "Unverified")}
                            </Text>
                        </View>
                        <View style={styles.summaryRow}>
                            <Text style={styles.summaryLabel}>{t("当前用户", "Current user")}</Text>
                            <Text style={styles.summaryValue}>{summary?.user?.name || user?.name || user?.login || t("未知用户", "Unknown user")}</Text>
                        </View>
                        <View style={styles.summaryRow}>
                            <Text style={styles.summaryLabel}>{t("邮箱", "Email")}</Text>
                            <Text style={styles.summaryValue}>{summary?.user?.email || user?.email || t("未提供邮箱", "No email provided")}</Text>
                        </View>
                        {projects.length > 0 ? (
                            <View style={styles.projectInlineList}>
                                {projects.slice(0, 3).map((project) => (
                                    <View key={project.id || project.slug || project.name} style={styles.projectInlineChip}>
                                        <MaterialCommunityIcons name="briefcase-outline" size={14} color={colors.primaryDeep} />
                                        <Text style={styles.projectInlineText} numberOfLines={1}>
                                            {project.name || project.slug || project.id || t("未命名项目", "Untitled project")}
                                        </Text>
                                    </View>
                                ))}
                            </View>
                        ) : null}

                        <Text style={styles.sectionTitle}>{t("切换管理台", "Switch admin")}</Text>
                        <TextInput
                            value={draftBaseUrl}
                            onChangeText={setDraftBaseUrl}
                            autoCapitalize="none"
                            autoCorrect={false}
                            placeholder="http://127.0.0.1:9528"
                            placeholderTextColor={colors.textSoft}
                            style={styles.input}
                        />
                        <Pressable style={[styles.primaryButton, busy && styles.disabled]} onPress={() => void saveAndReconnect()}>
                            <Text style={styles.primaryButtonText}>{busy ? t("切换中…", "Switching...") : t("保存并重新登录", "Save and sign in again")}</Text>
                        </Pressable>
                    </GlassCard>
                </ScrollView>
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
    sectionTitleRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: spacing.md,
    },
    sectionTitle: {
        color: colors.text,
        fontSize: 15,
        fontWeight: "800",
        marginBottom: spacing.md,
    },
    summaryRow: {
        gap: 4,
        marginBottom: spacing.sm,
    },
    summaryLabel: {
        color: colors.textSoft,
        fontSize: 11,
        fontWeight: "800",
        textTransform: "uppercase",
        letterSpacing: 0.8,
    },
    summaryValue: {
        color: colors.text,
        fontSize: 14,
        lineHeight: 20,
    },
    okText: {
        color: colors.success,
    },
    warnText: {
        color: colors.warning,
    },
    projectList: {
        gap: spacing.sm,
    },
    projectInlineList: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 8,
        marginTop: spacing.sm,
        marginBottom: spacing.md,
    },
    projectInlineChip: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderRadius: radii.pill,
        paddingHorizontal: 10,
        paddingVertical: 7,
        backgroundColor: "rgba(124,58,237,0.08)",
        borderWidth: 1,
        borderColor: "rgba(124,58,237,0.14)",
        maxWidth: "100%",
    },
    projectInlineText: {
        color: colors.primaryDeep,
        fontSize: 12,
        fontWeight: "700",
    },
    projectCard: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: spacing.sm,
        borderRadius: radii.md,
        backgroundColor: colors.surface,
        borderWidth: 1,
        borderColor: colors.border,
        paddingHorizontal: 12,
        paddingVertical: 12,
    },
    projectIcon: {
        width: 34,
        height: 34,
        borderRadius: 17,
        backgroundColor: colors.primarySoft,
        alignItems: "center",
        justifyContent: "center",
    },
    projectBody: {
        flex: 1,
        gap: 4,
    },
    projectTitle: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "800",
    },
    projectSummary: {
        color: colors.textMuted,
        fontSize: 12,
        lineHeight: 18,
    },
    input: {
        borderWidth: 1,
        borderColor: colors.border,
        borderRadius: radii.md,
        backgroundColor: colors.surface,
        paddingHorizontal: 14,
        paddingVertical: 14,
        color: colors.text,
        fontSize: 16,
    },
    primaryButton: {
        marginTop: spacing.md,
        minHeight: 48,
        borderRadius: radii.md,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: colors.primary,
    },
    primaryButtonText: {
        color: "#FFFFFF",
        fontWeight: "800",
        fontSize: 15,
    },
    disabled: {
        opacity: 0.6,
    },
});
