import { Redirect, router, type Href } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
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
import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { getRpaAvailability, listRpaDrafts, listRpaScripts, runExistingRobotFlow, runRpaCompile, runRpaDraft } from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { RPAAvailability, RPADraftSummary, RPARobotScriptSummary } from "@/src/types/admin";

type RPAFlowOption = {
    key: string;
    kind: "draft" | "script";
    id: string;
    label: string;
    meta?: string;
};

export default function RPAScreen() {
    const { status, user, adminBaseUrl, authorizedFetch } = useAppSession();
    const { t } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const profileImageUri = resolveAdminAssetUrl(adminBaseUrl, user?.image || "");
    const [refreshing, setRefreshing] = useState(false);
    const [availability, setAvailability] = useState<RPAAvailability | null>(null);
    const [drafts, setDrafts] = useState<RPADraftSummary[]>([]);
    const [scripts, setScripts] = useState<RPARobotScriptSummary[]>([]);
    const [compileRunIds, setCompileRunIds] = useState("");
    const [selectedFlowKey, setSelectedFlowKey] = useState("");
    const [flowPickerOpen, setFlowPickerOpen] = useState(false);
    const [variablesText, setVariablesText] = useState("");
    const [latestResult, setLatestResult] = useState<Record<string, unknown> | null>(null);
    const [busyAction, setBusyAction] = useState<"" | "compile" | "run-existing" | "run-draft">("");

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "connect", icon: "lan-connect", onPress: () => router.push("/connect" as Href) },
        { key: "desktop-live", icon: "monitor-dashboard", onPress: () => router.push("/desktop-live" as Href) },
        { key: "settings", icon: "cog-outline", onPress: () => router.push("/settings" as Href) },
    ];

    const load = useCallback(async () => {
        setRefreshing(true);
        try {
            const [nextAvailability, nextDrafts, nextScripts] = await Promise.all([
                getRpaAvailability(authorizedFetch),
                listRpaDrafts(authorizedFetch, 8),
                listRpaScripts(authorizedFetch, 50),
            ]);
            setAvailability(nextAvailability);
            setDrafts(nextDrafts);
            setScripts(nextScripts);
        } catch (error) {
            Alert.alert(t("src.screens.approvalsscreen.load_failed"), error instanceof Error ? error.message : t("src.screens.rpascreen.unable_to_load_the_rpa_user_entry_state"));
        } finally {
            setRefreshing(false);
        }
    }, [authorizedFetch, t]);

    const flowOptions = useMemo<RPAFlowOption[]>(() => {
        const draftOptions: RPAFlowOption[] = [];
        for (const draft of drafts) {
            const id = String(draft.script_id || draft.id || "").trim();
            if (!id) {
                continue;
            }
            draftOptions.push({
                key: `draft:${id}`,
                kind: "draft",
                id,
                label: draft.title || id,
                meta: `${t("src.screens.rpascreen.draft_flow")} · ${draft.status || "draft"}`,
            });
        }
        const scriptOptions: RPAFlowOption[] = [];
        for (const script of scripts) {
            const id = String(script.path || script.name || "").trim();
            if (!id) {
                continue;
            }
            scriptOptions.push({
                key: `script:${id}`,
                kind: "script",
                id,
                label: script.name || id,
                meta: t("src.screens.rpascreen.robot_script"),
            });
        }
        return [...draftOptions, ...scriptOptions];
    }, [drafts, scripts, t]);

    const selectedFlow = useMemo(
        () => flowOptions.find((item) => item.key === selectedFlowKey) || null,
        [flowOptions, selectedFlowKey],
    );

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
        if (!selectedFlow) {
            Alert.alert(t("src.screens.rpascreen.unable_to_execute"), t("src.screens.rpascreen.select_flow_to_run"));
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
            const payload = selectedFlow.kind === "draft"
                ? await runRpaDraft(authorizedFetch, selectedFlow.id, variables)
                : await runExistingRobotFlow(authorizedFetch, selectedFlow.id, variables);
            setLatestResult(payload);
            await load();
        } catch (error) {
            Alert.alert(t("src.screens.rpascreen.execution_failed"), error instanceof Error ? error.message : t("src.screens.rpascreen.unable_to_execute_the_existing_rpa_flow"));
        } finally {
            setBusyAction("");
        }
    }, [authorizedFetch, load, parseVariables, selectedFlow, t]);

    const handleRunDraft = useCallback(async (draft: RPADraftSummary) => {
        const robotFile = String(draft.script_id || draft.id || "").trim();
        if (!robotFile) {
            Alert.alert(t("src.screens.rpascreen.unable_to_execute"), t("src.screens.rpascreen.enter_an_existing_robot_file_path_or_script_id"));
            return;
        }
        setSelectedFlowKey(`draft:${robotFile}`);
        setBusyAction("run-draft");
        try {
            const payload = await runRpaDraft(authorizedFetch, robotFile, {});
            setLatestResult(payload);
            await load();
        } catch (error) {
            Alert.alert(t("src.screens.rpascreen.execution_failed"), error instanceof Error ? error.message : t("src.screens.rpascreen.unable_to_execute_the_rpa_draft"));
        } finally {
            setBusyAction("");
        }
    }, [authorizedFetch, load, t]);

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
                <PhoneTopbar actions={actions} userImageUri={profileImageUri || undefined} onBrandPress={() => void goHomeToChat()} />

                <ScrollView
                    contentContainerStyle={styles.content}
                    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load()} />}
                >
                    <GlassCard style={styles.heroCard}>
                        <View style={styles.heroHeader}>
                            <View style={styles.heroIcon}>
                                <MaterialCommunityIcons name="robot-outline" size={22} color={colors.primaryDeep} />
                            </View>
                            <View style={styles.heroBody}>
                                <Text style={styles.heroTitle}>RPA</Text>
                                <Text style={styles.heroSubtitle}>{t("src.screens.rpascreen.runtime_availability")}</Text>
                            </View>
                            <View style={[styles.readyPill, (availability?.robotFramework || availability?.rpaFramework) ? styles.readyPillOk : styles.readyPillMuted]}>
                                <Text style={[styles.readyPillText, (availability?.robotFramework || availability?.rpaFramework) ? styles.readyPillTextOk : styles.readyPillTextMuted]}>
                                    {(availability?.robotFramework || availability?.rpaFramework) ? t("src.screens.rpascreen.ready") : t("src.screens.rpascreen.not_ready")}
                                </Text>
                            </View>
                        </View>
                        <View style={styles.capabilityGrid}>
                            {[
                                ["Robot Framework", availability?.robotFramework],
                                ["RPA Framework", availability?.rpaFramework],
                                [t("src.screens.rpascreen.windows_library"), availability?.libraries?.["RPA.Windows"]],
                                ["Browser", availability?.libraries?.["RPA.Browser.Selenium"]],
                            ].map(([label, ok]) => (
                                <View key={String(label)} style={styles.capabilityChip}>
                                    <View style={[styles.statusDot, ok ? styles.statusOk : styles.statusMuted]} />
                                    <Text style={styles.capabilityText} numberOfLines={1}>{String(label)}</Text>
                                </View>
                            ))}
                        </View>
                    </GlassCard>

                    <GlassCard style={styles.compactCard}>
                        <View style={styles.sectionTitleRow}>
                            <Text style={styles.sectionTitle}>{t("src.screens.rpascreen.recent_drafts")}</Text>
                            <Text style={styles.sectionMeta}>{drafts.length}</Text>
                        </View>
                        {drafts.length === 0 ? (
                            <Text style={styles.emptyBody}>{t("src.screens.rpascreen.there_are_no_rpa_drafts_ready_to_continue_right_now")}</Text>
                        ) : (
                            drafts.map((draft) => (
                                <View
                                    key={draft.script_id || draft.id || draft.title}
                                    style={styles.draftItem}
                                >
                                    <Pressable
                                        style={styles.draftBody}
                                        onPress={() => {
                                            const id = String(draft.script_id || draft.id || "").trim();
                                            if (id) {
                                                setSelectedFlowKey(`draft:${id}`);
                                            }
                                        }}
                                    >
                                        <Text style={styles.draftTitle}>{draft.title || draft.script_id || draft.id || t("src.screens.rpascreen.untitled_draft")}</Text>
                                        <Text style={styles.draftMeta}>
                                            {draft.status || "draft"} · {draft.updated_at || draft.created_at || t("src.screens.rpascreen.unknown_time")}
                                        </Text>
                                    </Pressable>
                                    <Pressable
                                        style={({ pressed }) => [styles.draftRunButton, pressed && styles.pressed]}
                                        disabled={busyAction !== ""}
                                        onPress={() => void handleRunDraft(draft)}
                                    >
                                        <MaterialCommunityIcons name="play" size={16} color={colors.primaryDeep} />
                                    </Pressable>
                                </View>
                            ))
                        )}
                    </GlassCard>

                    <GlassCard style={styles.compactCard}>
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

                    <GlassCard style={styles.compactCard}>
                        <Text style={styles.sectionTitle}>{t("src.screens.rpascreen.run_existing_flow")}</Text>
                        <Pressable
                            style={({ pressed }) => [styles.flowSelector, pressed && styles.pressed]}
                            onPress={() => setFlowPickerOpen((value) => !value)}
                        >
                            <View style={styles.flowSelectorBody}>
                                <Text style={[styles.flowSelectorLabel, !selectedFlow && styles.placeholderText]} numberOfLines={1}>
                                    {selectedFlow?.label || t("src.screens.rpascreen.select_an_rpa_flow")}
                                </Text>
                                {selectedFlow?.meta ? <Text style={styles.flowSelectorMeta} numberOfLines={1}>{selectedFlow.meta}</Text> : null}
                            </View>
                            <MaterialCommunityIcons name={flowPickerOpen ? "chevron-up" : "chevron-down"} size={22} color={colors.textMuted} />
                        </Pressable>
                        {flowPickerOpen ? (
                            <View style={styles.flowMenu}>
                                {flowOptions.length === 0 ? (
                                    <Text style={styles.emptyBody}>{t("src.screens.rpascreen.no_rpa_flows_available")}</Text>
                                ) : (
                                    flowOptions.map((option) => (
                                        <Pressable
                                            key={option.key}
                                            style={({ pressed }) => [
                                                styles.flowOption,
                                                selectedFlowKey === option.key && styles.flowOptionSelected,
                                                pressed && styles.pressed,
                                            ]}
                                            onPress={() => {
                                                setSelectedFlowKey(option.key);
                                                setFlowPickerOpen(false);
                                            }}
                                        >
                                            <Text style={styles.flowOptionTitle} numberOfLines={1}>{option.label}</Text>
                                            <Text style={styles.flowOptionMeta} numberOfLines={1}>{option.meta}</Text>
                                        </Pressable>
                                    ))
                                )}
                            </View>
                        ) : null}
                        <Text style={styles.fieldHint}>{t("src.screens.rpascreen.flow_variables")}</Text>
                        <TextInput
                            value={variablesText}
                            onChangeText={setVariablesText}
                            placeholder={t("src.screens.rpascreen.variables_optional_json_placeholder")}
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

                    <GlassCard style={styles.compactCard}>
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
        paddingHorizontal: spacing.md,
        paddingBottom: spacing.xl,
        gap: 12,
    },
    heroCard: {
        padding: 14,
        borderRadius: 24,
        backgroundColor: "rgba(255,255,255,0.82)",
        borderColor: "rgba(148,163,184,0.22)",
    },
    compactCard: {
        padding: 14,
        borderRadius: 22,
        backgroundColor: "rgba(255,255,255,0.74)",
        borderColor: "rgba(148,163,184,0.20)",
        shadowOpacity: 0.035,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 5 },
    },
    heroHeader: {
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
    },
    heroIcon: {
        width: 42,
        height: 42,
        borderRadius: 16,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(124,58,237,0.10)",
    },
    heroBody: {
        flex: 1,
        gap: 2,
    },
    heroTitle: {
        color: colors.text,
        fontSize: 22,
        fontWeight: "900",
        letterSpacing: -0.3,
    },
    heroSubtitle: {
        color: colors.textMuted,
        fontSize: 12,
        fontWeight: "700",
    },
    readyPill: {
        borderRadius: radii.pill,
        paddingHorizontal: 10,
        paddingVertical: 6,
    },
    readyPillOk: {
        backgroundColor: "rgba(16,185,129,0.12)",
    },
    readyPillMuted: {
        backgroundColor: "rgba(148,163,184,0.14)",
    },
    readyPillText: {
        fontSize: 11,
        fontWeight: "900",
    },
    readyPillTextOk: {
        color: colors.success,
    },
    readyPillTextMuted: {
        color: colors.textSoft,
    },
    capabilityGrid: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 8,
        marginTop: 14,
    },
    capabilityChip: {
        flexDirection: "row",
        alignItems: "center",
        gap: 7,
        borderRadius: radii.pill,
        paddingHorizontal: 10,
        paddingVertical: 7,
        backgroundColor: "rgba(248,250,252,0.78)",
        borderWidth: 1,
        borderColor: "rgba(148,163,184,0.18)",
        maxWidth: "48%",
    },
    capabilityText: {
        color: colors.text,
        fontSize: 12,
        fontWeight: "800",
    },
    sectionTitleRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: spacing.sm,
    },
    sectionMeta: {
        color: colors.textSoft,
        fontSize: 12,
        fontWeight: "800",
    },
    sectionTitle: {
        color: colors.text,
        fontSize: 15,
        fontWeight: "800",
        marginBottom: spacing.sm,
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
        gap: 10,
        borderRadius: 18,
        backgroundColor: "rgba(248,250,252,0.84)",
        borderWidth: 1,
        borderColor: "rgba(148,163,184,0.18)",
        paddingHorizontal: 12,
        paddingVertical: 10,
        marginBottom: 8,
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
    draftRunButton: {
        width: 34,
        height: 34,
        borderRadius: 17,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(124,58,237,0.10)",
        borderWidth: 1,
        borderColor: "rgba(124,58,237,0.16)",
    },
    pressed: {
        opacity: 0.72,
    },
    input: {
        borderWidth: 1,
        borderColor: "rgba(148,163,184,0.24)",
        borderRadius: 18,
        backgroundColor: "rgba(255,255,255,0.86)",
        paddingHorizontal: 14,
        paddingVertical: 13,
        color: colors.text,
        fontSize: 15,
        marginTop: 8,
    },
    flowSelector: {
        marginTop: 8,
        minHeight: 58,
        borderWidth: 1,
        borderColor: "rgba(148,163,184,0.24)",
        borderRadius: 18,
        backgroundColor: "rgba(255,255,255,0.86)",
        paddingHorizontal: 14,
        paddingVertical: 10,
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
    },
    flowSelectorBody: {
        flex: 1,
        gap: 3,
    },
    flowSelectorLabel: {
        color: colors.text,
        fontSize: 15,
        fontWeight: "800",
    },
    placeholderText: {
        color: colors.textSoft,
    },
    flowSelectorMeta: {
        color: colors.textMuted,
        fontSize: 12,
        fontWeight: "700",
    },
    flowMenu: {
        marginTop: 8,
        borderWidth: 1,
        borderColor: "rgba(148,163,184,0.20)",
        borderRadius: 18,
        backgroundColor: "rgba(255,255,255,0.78)",
        padding: 8,
        gap: 6,
    },
    flowOption: {
        borderRadius: 14,
        paddingHorizontal: 10,
        paddingVertical: 9,
        backgroundColor: "rgba(248,250,252,0.78)",
    },
    flowOptionSelected: {
        backgroundColor: "rgba(124,58,237,0.12)",
    },
    flowOptionTitle: {
        color: colors.text,
        fontSize: 13,
        fontWeight: "800",
    },
    flowOptionMeta: {
        color: colors.textMuted,
        fontSize: 11,
        fontWeight: "700",
        marginTop: 2,
    },
    fieldHint: {
        marginTop: spacing.sm,
        color: colors.textMuted,
        fontSize: 12,
        fontWeight: "700",
    },
    multilineInput: {
        minHeight: 124,
        marginTop: spacing.sm,
    },
    primaryButton: {
        marginTop: 10,
        minHeight: 46,
        borderRadius: 18,
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
