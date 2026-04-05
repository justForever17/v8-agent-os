import { Redirect, router, type Href } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
    ActivityIndicator,
    Alert,
    Pressable,
    RefreshControl,
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
import { getRpaAvailability, listRpaDrafts, runExistingRobotFlow, runRpaCompile } from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { RPAAvailability, RPADraftSummary } from "@/src/types/admin";

export default function RPAScreen() {
    const { status, authorizedFetch } = useAppSession();
    const { t } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const [refreshing, setRefreshing] = useState(false);
    const [availability, setAvailability] = useState<RPAAvailability | null>(null);
    const [drafts, setDrafts] = useState<RPADraftSummary[]>([]);
    const [compileRunIds, setCompileRunIds] = useState("");
    const [existingRobotFile, setExistingRobotFile] = useState("");
    const [variablesText, setVariablesText] = useState("{\n  \n}");
    const [latestResult, setLatestResult] = useState<Record<string, unknown> | null>(null);
    const [busyAction, setBusyAction] = useState<"" | "compile" | "run-existing">("");

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "connect", icon: "lan-connect", onPress: () => router.push("/connect" as Href) },
        { key: "desktop-live", icon: "monitor-dashboard", onPress: () => router.push("/desktop-live" as Href) },
        { key: "settings", icon: "cog-outline", onPress: () => router.push("/settings" as Href) },
    ];

    const load = useCallback(async () => {
        setRefreshing(true);
        try {
            const [nextAvailability, nextDrafts] = await Promise.all([
                getRpaAvailability(authorizedFetch),
                listRpaDrafts(authorizedFetch, 8),
            ]);
            setAvailability(nextAvailability);
            setDrafts(nextDrafts);
        } catch (error) {
            Alert.alert(t("读取失败", "Load failed"), error instanceof Error ? error.message : t("无法读取 RPA 用户入口状态", "Unable to load the RPA user entry state"));
        } finally {
            setRefreshing(false);
        }
    }, [authorizedFetch, t]);

    const parseVariables = useCallback(() => {
        const raw = variablesText.trim();
        if (!raw) {
            return {};
        }
        try {
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
                throw new Error(t("变量必须是 JSON 对象", "Variables must be a JSON object"));
            }
            return parsed as Record<string, unknown>;
        } catch (error) {
            throw new Error(error instanceof Error ? error.message : t("变量 JSON 解析失败", "Failed to parse the variables JSON"));
        }
    }, [variablesText, t]);

    const handleCompile = useCallback(async () => {
        const runIds = compileRunIds
            .split(/[\s,]+/)
            .map((item) => item.trim())
            .filter(Boolean);
        if (runIds.length === 0) {
            Alert.alert(t("无法生成", "Unable to generate"), t("请至少输入一个 Run ID。", "Enter at least one Run ID."));
            return;
        }
        setBusyAction("compile");
        try {
            const payload = await runRpaCompile(authorizedFetch, runIds);
            setLatestResult(payload);
            await load();
        } catch (error) {
            Alert.alert(t("生成失败", "Generation failed"), error instanceof Error ? error.message : t("无法从 Run 生成 RPA 草稿", "Unable to generate an RPA draft from the run."));
        } finally {
            setBusyAction("");
        }
    }, [authorizedFetch, compileRunIds, load, t]);

    const handleRunExisting = useCallback(async () => {
        const robotFile = existingRobotFile.trim();
        if (!robotFile) {
            Alert.alert(t("无法执行", "Unable to execute"), t("请填写既有的 robot 文件路径或脚本 ID。", "Enter an existing robot file path or script ID."));
            return;
        }
        let variables: Record<string, unknown>;
        try {
            variables = parseVariables();
        } catch (error) {
            Alert.alert(t("变量格式错误", "Invalid variables"), error instanceof Error ? error.message : t("变量 JSON 不正确", "The variables JSON is invalid."));
            return;
        }
        setBusyAction("run-existing");
        try {
            const payload = await runExistingRobotFlow(authorizedFetch, robotFile, variables);
            setLatestResult(payload);
            await load();
        } catch (error) {
            Alert.alert(t("执行失败", "Execution failed"), error instanceof Error ? error.message : t("无法执行既有 RPA 流程", "Unable to execute the existing RPA flow."));
        } finally {
            setBusyAction("");
        }
    }, [authorizedFetch, existingRobotFile, load, parseVariables, t]);

    useEffect(() => {
        if (status === "authenticated") {
            void load();
        }
    }, [load, status]);

    if (status === "booting") {
        return <LoadingScreen label={t("正在读取 RPA 状态…", "Loading RPA status...")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    return (
        <LinearGradient
            colors={[colors.background, "#FFF7ED"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.gradient}
        >
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar actions={actions} onBrandPress={() => void goHomeToChat()} />

                <ScrollView
                    contentContainerStyle={styles.content}
                    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load()} />}
                >
                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("运行时可用性", "Runtime availability")}</Text>
                        <View style={styles.statusRow}>
                            <View style={[styles.statusDot, availability?.robotFramework ? styles.statusOk : styles.statusMuted]} />
                            <Text style={styles.statusText}>
                                Robot Framework {availability?.robotFramework ? t("已就绪", "Ready") : t("未就绪", "Not ready")}
                            </Text>
                        </View>
                        <View style={styles.statusRow}>
                            <View style={[styles.statusDot, availability?.rpaFramework ? styles.statusOk : styles.statusMuted]} />
                            <Text style={styles.statusText}>
                                RPA Framework {availability?.rpaFramework ? t("已就绪", "Ready") : t("未就绪", "Not ready")}
                            </Text>
                        </View>
                        <Text style={styles.metaText}>
                            {t("Windows 库", "Windows library")}：{availability?.libraries?.["RPA.Windows"] ? t("已安装", "Installed") : t("缺失", "Missing")}
                            {" · "}
                            Browser：{availability?.libraries?.["RPA.Browser.Selenium"] ? t("已安装", "Installed") : t("缺失", "Missing")}
                        </Text>
                    </GlassCard>

                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("最近草稿", "Recent drafts")}</Text>
                        {drafts.length === 0 ? (
                            <Text style={styles.emptyBody}>{t("当前没有可直接继续的 RPA 草稿。", "There are no RPA drafts ready to continue right now.")}</Text>
                        ) : (
                            drafts.map((draft) => (
                                <Pressable
                                    key={draft.script_id || draft.id || draft.title}
                                    style={styles.draftItem}
                                    onPress={() => setExistingRobotFile(draft.script_id || draft.id || draft.title || "")}
                                >
                                    <View style={styles.draftBody}>
                                        <Text style={styles.draftTitle}>{draft.title || draft.script_id || draft.id || t("未命名草稿", "Untitled draft")}</Text>
                                        <Text style={styles.draftMeta}>
                                            {draft.status || "draft"} · {draft.updated_at || draft.created_at || t("时间未知", "Unknown time")}
                                        </Text>
                                    </View>
                                    <MaterialCommunityIcons name="chevron-right" size={18} color={colors.textSoft} />
                                </Pressable>
                            ))
                        )}
                    </GlassCard>

                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("从 Run 生成草稿", "Generate draft from run")}</Text>
                        <TextInput
                            value={compileRunIds}
                            onChangeText={setCompileRunIds}
                            placeholder={t("run_123456 或用逗号 / 空格分隔多个 Run ID", "run_123456, or separate multiple Run IDs with commas / spaces")}
                            placeholderTextColor={colors.textSoft}
                            autoCapitalize="none"
                            autoCorrect={false}
                            style={styles.input}
                        />
                        <Pressable style={[styles.primaryButton, busyAction === "compile" && styles.disabled]} onPress={() => void handleCompile()}>
                            {busyAction === "compile" ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.primaryButtonText}>{t("生成草稿", "Generate draft")}</Text>}
                        </Pressable>
                    </GlassCard>

                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("执行既有流程", "Run existing flow")}</Text>
                        <TextInput
                            value={existingRobotFile}
                            onChangeText={setExistingRobotFile}
                            placeholder={t("scripts/example.robot 或已有脚本 ID", "scripts/example.robot or an existing script ID")}
                            placeholderTextColor={colors.textSoft}
                            autoCapitalize="none"
                            autoCorrect={false}
                            style={styles.input}
                        />
                        <TextInput
                            value={variablesText}
                            onChangeText={setVariablesText}
                            placeholder='{"username":"demo"}'
                            placeholderTextColor={colors.textSoft}
                            multiline
                            textAlignVertical="top"
                            autoCapitalize="none"
                            autoCorrect={false}
                            style={[styles.input, styles.multilineInput]}
                        />
                        <Pressable style={[styles.primaryButton, busyAction === "run-existing" && styles.disabled]} onPress={() => void handleRunExisting()}>
                            {busyAction === "run-existing" ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.primaryButtonText}>{t("执行既有流程", "Run existing flow")}</Text>}
                        </Pressable>
                    </GlassCard>

                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("最近结果", "Latest result")}</Text>
                        {latestResult ? (
                            <Text style={styles.resultText}>{JSON.stringify(latestResult, null, 2)}</Text>
                        ) : (
                            <Text style={styles.emptyBody}>{t("这里会显示最近一次编译或执行返回的结果，便于手机端继续跟进。", "The latest compile or execution result appears here so you can continue from Phone.")}</Text>
                        )}
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
    sectionTitle: {
        color: colors.text,
        fontSize: 15,
        fontWeight: "800",
        marginBottom: spacing.md,
    },
    statusRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        marginBottom: spacing.sm,
    },
    statusDot: {
        width: 10,
        height: 10,
        borderRadius: 5,
    },
    statusOk: {
        backgroundColor: colors.success,
    },
    statusMuted: {
        backgroundColor: colors.textSoft,
    },
    statusText: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "700",
    },
    metaText: {
        marginTop: spacing.sm,
        color: colors.textMuted,
        fontSize: 13,
        lineHeight: 20,
    },
    draftItem: {
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
        borderRadius: radii.md,
        backgroundColor: colors.surface,
        borderWidth: 1,
        borderColor: colors.border,
        paddingHorizontal: 14,
        paddingVertical: 12,
        marginBottom: spacing.sm,
    },
    draftBody: {
        flex: 1,
        gap: 4,
    },
    draftTitle: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "800",
    },
    draftMeta: {
        color: colors.textMuted,
        fontSize: 12,
    },
    input: {
        borderWidth: 1,
        borderColor: colors.border,
        borderRadius: radii.md,
        backgroundColor: colors.surface,
        paddingHorizontal: 14,
        paddingVertical: 14,
        color: colors.text,
        fontSize: 15,
        marginTop: spacing.sm,
    },
    multilineInput: {
        minHeight: 124,
        marginTop: spacing.sm,
    },
    primaryButton: {
        marginTop: spacing.md,
        minHeight: 46,
        borderRadius: radii.md,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: colors.primary,
    },
    primaryButtonText: {
        color: "#FFFFFF",
        fontWeight: "800",
    },
    resultText: {
        color: colors.text,
        fontSize: 12,
        lineHeight: 18,
        fontFamily: "monospace",
    },
    disabled: {
        opacity: 0.6,
    },
    emptyBody: {
        color: colors.textMuted,
        fontSize: 14,
        lineHeight: 22,
    },
});
