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
            Alert.alert(t("src.screens.approvalsscreen.load_failed"), error instanceof Error ? error.message : t("src.screens.rpascreen.unable_to_load_the_rpa_user_entry_state"));
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
                throw new Error(t("src.screens.rpascreen.variables_must_be_a_json_object"));
            }
            return parsed as Record<string, unknown>;
        } catch (error) {
            throw new Error(error instanceof Error ? error.message : t("src.screens.rpascreen.failed_to_parse_the_variables_json"));
        }
    }, [variablesText, t]);

    const handleCompile = useCallback(async () => {
        const runIds = compileRunIds
            .split(/[\s,]+/)
            .map((item) => item.trim())
            .filter(Boolean);
        if (runIds.length === 0) {
            Alert.alert(t("src.screens.rpascreen.unable_to_generate"), t("src.screens.rpascreen.enter_at_least_one_run_id"));
            return;
        }
        setBusyAction("compile");
        try {
            const payload = await runRpaCompile(authorizedFetch, runIds);
            setLatestResult(payload);
            await load();
        } catch (error) {
            Alert.alert(t("src.screens.rpascreen.generation_failed"), error instanceof Error ? error.message : t("src.screens.rpascreen.unable_to_generate_an_rpa_draft_from_the_run"));
        } finally {
            setBusyAction("");
        }
    }, [authorizedFetch, compileRunIds, load, t]);

    const handleRunExisting = useCallback(async () => {
        const robotFile = existingRobotFile.trim();
        if (!robotFile) {
            Alert.alert(t("src.screens.rpascreen.unable_to_execute"), t("src.screens.rpascreen.enter_an_existing_robot_file_path_or_script_id"));
            return;
        }
        let variables: Record<string, unknown>;
        try {
            variables = parseVariables();
        } catch (error) {
            Alert.alert(t("src.screens.rpascreen.invalid_variables"), error instanceof Error ? error.message : t("src.screens.rpascreen.the_variables_json_is_invalid"));
            return;
        }
        setBusyAction("run-existing");
        try {
            const payload = await runExistingRobotFlow(authorizedFetch, robotFile, variables);
            setLatestResult(payload);
            await load();
        } catch (error) {
            Alert.alert(t("src.screens.rpascreen.execution_failed"), error instanceof Error ? error.message : t("src.screens.rpascreen.unable_to_execute_the_existing_rpa_flow"));
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
        return <LoadingScreen label={t("src.screens.rpascreen.loading_rpa_status")} />;
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
                        <Text style={styles.sectionTitle}>{t("src.screens.rpascreen.runtime_availability")}</Text>
                        <View style={styles.statusRow}>
                            <View style={[styles.statusDot, availability?.robotFramework ? styles.statusOk : styles.statusMuted]} />
                            <Text style={styles.statusText}>
                                Robot Framework {availability?.robotFramework ? t("src.screens.rpascreen.ready") : t("src.screens.rpascreen.not_ready")}
                            </Text>
                        </View>
                        <View style={styles.statusRow}>
                            <View style={[styles.statusDot, availability?.rpaFramework ? styles.statusOk : styles.statusMuted]} />
                            <Text style={styles.statusText}>
                                RPA Framework {availability?.rpaFramework ? t("src.screens.rpascreen.ready") : t("src.screens.rpascreen.not_ready")}
                            </Text>
                        </View>
                        <Text style={styles.metaText}>
                            {t("src.screens.rpascreen.windows_library")}：{availability?.libraries?.["RPA.Windows"] ? t("src.screens.rpascreen.installed") : t("src.screens.rpascreen.missing")}
                            {" · "}
                            Browser：{availability?.libraries?.["RPA.Browser.Selenium"] ? t("src.screens.rpascreen.installed") : t("src.screens.rpascreen.missing")}
                        </Text>
                    </GlassCard>

                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("src.screens.rpascreen.recent_drafts")}</Text>
                        {drafts.length === 0 ? (
                            <Text style={styles.emptyBody}>{t("src.screens.rpascreen.there_are_no_rpa_drafts_ready_to_continue_right_now")}</Text>
                        ) : (
                            drafts.map((draft) => (
                                <Pressable
                                    key={draft.script_id || draft.id || draft.title}
                                    style={styles.draftItem}
                                    onPress={() => setExistingRobotFile(draft.script_id || draft.id || draft.title || "")}
                                >
                                    <View style={styles.draftBody}>
                                        <Text style={styles.draftTitle}>{draft.title || draft.script_id || draft.id || t("src.screens.rpascreen.untitled_draft")}</Text>
                                        <Text style={styles.draftMeta}>
                                            {draft.status || "draft"} · {draft.updated_at || draft.created_at || t("src.screens.rpascreen.unknown_time")}
                                        </Text>
                                    </View>
                                    <MaterialCommunityIcons name="chevron-right" size={18} color={colors.textSoft} />
                                </Pressable>
                            ))
                        )}
                    </GlassCard>

                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("src.screens.rpascreen.generate_draft_from_run")}</Text>
                        <TextInput
                            value={compileRunIds}
                            onChangeText={setCompileRunIds}
                            placeholder={t("src.screens.rpascreen.run_123456_or_separate_multiple_run_ids_with_commas_spaces")}
                            placeholderTextColor={colors.textSoft}
                            autoCapitalize="none"
                            autoCorrect={false}
                            style={styles.input}
                        />
                        <Pressable style={[styles.primaryButton, busyAction === "compile" && styles.disabled]} onPress={() => void handleCompile()}>
                            {busyAction === "compile" ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.primaryButtonText}>{t("src.screens.rpascreen.generate_draft")}</Text>}
                        </Pressable>
                    </GlassCard>

                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("src.screens.rpascreen.run_existing_flow")}</Text>
                        <TextInput
                            value={existingRobotFile}
                            onChangeText={setExistingRobotFile}
                            placeholder={t("src.screens.rpascreen.scripts_example_robot_or_an_existing_script_id")}
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
                            {busyAction === "run-existing" ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.primaryButtonText}>{t("src.screens.rpascreen.run_existing_flow")}</Text>}
                        </Pressable>
                    </GlassCard>

                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("src.screens.rpascreen.latest_result")}</Text>
                        {latestResult ? (
                            <Text style={styles.resultText}>{JSON.stringify(latestResult, null, 2)}</Text>
                        ) : (
                            <Text style={styles.emptyBody}>{t("src.screens.rpascreen.the_latest_compile_or_execution_result_appears_here_so_you_can_continue_from_phone")}</Text>
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
