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
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { GlassCard } from "@/src/components/common/GlassCard";
import { getRpaAvailability, listRpaDrafts, listRpaScripts, listRpaTemplates, runExistingRobotFlow, runRpaCompile, runRpaDraft } from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { RPAAvailability, RPADraftSummary, RPARobotScriptSummary, RPATemplateSummary } from "@/src/types/admin";

type RPAFlowOption = {
    key: string;
    kind: "template" | "script";
    id: string;
    sourceDraftId?: string;
    label: string;
    meta?: string;
};

export function RpaMenuOverlay({
    visible,
    onClose,
}: {
    visible: boolean;
    onClose: () => void;
}) {
    const { status, authorizedFetch } = useAppSession();
    const { t } = useUiPrefs();
    const [refreshing, setRefreshing] = useState(false);
    const [availability, setAvailability] = useState<RPAAvailability | null>(null);
    const [drafts, setDrafts] = useState<RPADraftSummary[]>([]);
    const [scripts, setScripts] = useState<RPARobotScriptSummary[]>([]);
    const [templates, setTemplates] = useState<RPATemplateSummary[]>([]);
    const [compileRunIds, setCompileRunIds] = useState("");
    const [selectedFlowKey, setSelectedFlowKey] = useState("");
    const [flowPickerOpen, setFlowPickerOpen] = useState(false);
    const [variablesText, setVariablesText] = useState("");
    const [latestResult, setLatestResult] = useState<Record<string, unknown> | null>(null);
    const [busyAction, setBusyAction] = useState<"" | "compile" | "run-existing">("");

    const load = useCallback(async () => {
        if (status !== "authenticated") return;
        setRefreshing(true);
        try {
            const [nextAvailability, nextDrafts, nextTemplates, nextScripts] = await Promise.all([
                getRpaAvailability(authorizedFetch),
                listRpaDrafts(authorizedFetch, 4), // 限制数量以适应菜单展示
                listRpaTemplates(authorizedFetch, 50, "approved"),
                listRpaScripts(authorizedFetch, 50),
            ]);
            setAvailability(nextAvailability);
            setDrafts(nextDrafts);
            setTemplates(nextTemplates);
            setScripts(nextScripts);
        } catch (error) {
            Alert.alert(t("src.screens.approvalsscreen.load_failed"), error instanceof Error ? error.message : t("src.screens.rpascreen.unable_to_load_the_rpa_user_entry_state"));
        } finally {
            setRefreshing(false);
        }
    }, [authorizedFetch, status, t]);

    useEffect(() => {
        if (visible && status === "authenticated") {
            void load();
        }
    }, [visible, load, status]);

    const flowOptions = useMemo<RPAFlowOption[]>(() => {
        const templateOptions: RPAFlowOption[] = [];
        for (const template of templates) {
            const id = String(template.id || "").trim();
            if (!id) continue;
            const sourceDraftId = String(template.source?.draftId || "").trim();
            templateOptions.push({
                key: `template:${id}`,
                kind: "template",
                id,
                sourceDraftId,
                label: template.name || id,
                meta: `${t("src.screens.rpascreen.approved_template")} · ${template.view?.statusLabel || template.status || "approved"}`,
            });
        }
        const scriptOptions: RPAFlowOption[] = [];
        for (const script of scripts) {
            const id = String(script.path || script.name || "").trim();
            if (!id) continue;
            scriptOptions.push({
                key: `script:${id}`,
                kind: "script",
                id,
                label: script.name || id,
                meta: t("src.screens.rpascreen.robot_script"),
            });
        }
        return [...templateOptions, ...scriptOptions];
    }, [scripts, templates, t]);

    const selectedFlow = useMemo(
        () => flowOptions.find((item) => item.key === selectedFlowKey) || null,
        [flowOptions, selectedFlowKey],
    );

    const parseVariables = useCallback(() => {
        const raw = variablesText.trim();
        if (!raw) return {};
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
            if (selectedFlow.kind === "template" && !selectedFlow.sourceDraftId) {
                Alert.alert(t("src.screens.rpascreen.unable_to_execute"), t("src.screens.rpascreen.template_missing_source_draft"));
                return;
            }
            const payload = selectedFlow.kind === "template"
                ? await runRpaDraft(authorizedFetch, selectedFlow.sourceDraftId || selectedFlow.id, variables)
                : await runExistingRobotFlow(authorizedFetch, selectedFlow.id, variables);
            setLatestResult(payload);
            await load();
        } catch (error) {
            Alert.alert(t("src.screens.rpascreen.execution_failed"), error instanceof Error ? error.message : t("src.screens.rpascreen.unable_to_execute_the_existing_rpa_flow"));
        } finally {
            setBusyAction("");
        }
    }, [authorizedFetch, load, parseVariables, selectedFlow, t]);

    const handleSelectDraft = useCallback((draft: RPADraftSummary) => {
        const name = String(draft.title || draft.script_id || draft.id || t("src.screens.rpascreen.untitled_draft"));
        Alert.alert(name, t("src.screens.rpascreen.draft_requires_admin_approval"));
    }, [t]);

    const describeLatestResult = useCallback((payload: Record<string, unknown>) => {
        const statusText = String(payload.status || payload.outcome || payload.outcomeFamily || payload.result || "");
        const runId = String(payload.runId || payload.run_id || payload.id || "");
        const sessionId = String(payload.sessionId || payload.session_id || "");
        const errorText = String(payload.error || payload.detail || payload.message || "");
        const sideEffect = Boolean(payload.startedApp || payload.launchedApp || payload.appStarted || payload.fallbackStarted);
        const lines = [
            statusText ? `${t("src.screens.rpascreen.rpa_result_status")}: ${statusText}` : t("src.screens.rpascreen.rpa_result_received"),
            runId ? `run: ${runId}` : "",
            sessionId ? `session: ${sessionId}` : "",
            sideEffect ? t("src.screens.rpascreen.execution_started_but_failed") : "",
            errorText ? `${t("src.screens.rpascreen.execution_failed")}: ${errorText}` : "",
        ].filter(Boolean);
        return lines.join("\n");
    }, [t]);

    if (!visible) return null;

    return (
        <View style={StyleSheet.absoluteFillObject} pointerEvents="auto">
            {/* 半透明背景点击遮罩 */}
            <Pressable style={styles.backdrop} onPress={onClose} />
            <View style={styles.modalContainer} pointerEvents="box-none">
                <GlassCard style={styles.card}>
                    <View style={styles.header}>
                        <MaterialCommunityIcons name="robot-outline" size={20} color={colors.primaryDeep} />
                        <Text style={styles.title}>RPA 控制台</Text>
                        <Pressable style={styles.closeButton} onPress={onClose}>
                            <MaterialCommunityIcons name="close" size={20} color={colors.textMuted} />
                        </Pressable>
                    </View>

                    <ScrollView
                        style={styles.scrollView}
                        contentContainerStyle={styles.scrollContent}
                        showsVerticalScrollIndicator={false}
                        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load()} />}
                    >
                        <View style={styles.infoRow}>
                            <Text style={styles.statusLabel}>RPA 运行时状态:</Text>
                            <View style={[styles.readyPill, (availability?.robotFramework || availability?.rpaFramework) ? styles.readyPillOk : styles.readyPillMuted]}>
                                <Text style={[styles.readyPillText, (availability?.robotFramework || availability?.rpaFramework) ? styles.readyPillTextOk : styles.readyPillTextMuted]}>
                                    {(availability?.robotFramework || availability?.rpaFramework) ? t("src.screens.rpascreen.ready") : t("src.screens.rpascreen.not_ready")}
                                </Text>
                            </View>
                        </View>

                        {/* 从 Run 生成 Draft */}
                        <View style={styles.section}>
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
                        </View>

                        {/* 执行已有流程 */}
                        <View style={styles.section}>
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
                                <MaterialCommunityIcons name={flowPickerOpen ? "chevron-up" : "chevron-down"} size={20} color={colors.textMuted} />
                            </Pressable>

                            {flowPickerOpen && (
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
                            )}

                            <Text style={styles.fieldHint}>{t("src.screens.rpascreen.flow_variables")}</Text>
                            <TextInput
                                value={variablesText}
                                onChangeText={setVariablesText}
                                placeholder={t("src.screens.rpascreen.variables_debug_json_placeholder")}
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
                        </View>

                        {/* 最近的 Drafts */}
                        <View style={styles.section}>
                            <Text style={styles.sectionTitle}>{t("src.screens.rpascreen.recent_drafts")}</Text>
                            {drafts.length === 0 ? (
                                <Text style={styles.emptyBody}>{t("src.screens.rpascreen.there_are_no_rpa_drafts_ready_to_continue_right_now")}</Text>
                            ) : (
                                drafts.map((draft) => (
                                    <View key={draft.script_id || draft.id || draft.title} style={styles.draftItem}>
                                        <Pressable style={styles.draftBody} onPress={() => handleSelectDraft(draft)}>
                                            <Text style={styles.draftTitle} numberOfLines={1}>{draft.title || draft.script_id || draft.id || t("src.screens.rpascreen.untitled_draft")}</Text>
                                            <Text style={styles.draftMeta}>
                                                {draft.status || "draft"} · {draft.updated_at || draft.created_at || t("src.screens.rpascreen.unknown_time")}
                                            </Text>
                                        </Pressable>
                                        <View style={styles.draftLockedBadge}>
                                            <MaterialCommunityIcons name="lock-outline" size={14} color={colors.textMuted} />
                                        </View>
                                    </View>
                                ))
                            )}
                        </View>

                        {/* 执行结果 */}
                        <View style={styles.section}>
                            <Text style={styles.sectionTitle}>{t("src.screens.rpascreen.latest_result")}</Text>
                            {latestResult ? (
                                <Text style={styles.resultText}>{describeLatestResult(latestResult)}</Text>
                            ) : (
                                <Text style={styles.emptyBody}>{t("src.screens.rpascreen.the_latest_compile_or_execution_result_appears_here_so_you_can_continue_from_phone")}</Text>
                            )}
                        </View>
                    </ScrollView>
                </GlassCard>
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    backdrop: {
        ...StyleSheet.absoluteFillObject,
        backgroundColor: "rgba(15, 23, 42, 0.42)",
        zIndex: 100,
    },
    modalContainer: {
        ...StyleSheet.absoluteFillObject,
        justifyContent: "flex-end",
        zIndex: 101,
    },
    card: {
        backgroundColor: "rgba(255, 255, 255, 0.95)",
        borderTopLeftRadius: 24,
        borderTopRightRadius: 24,
        padding: 16,
        maxHeight: "85%",
        shadowOpacity: 0.15,
        shadowRadius: 15,
        shadowOffset: { width: 0, height: -5 },
        elevation: 10,
    },
    header: {
        flexDirection: "row",
        alignItems: "center",
        paddingBottom: 12,
        borderBottomWidth: 1,
        borderColor: "rgba(148, 163, 184, 0.12)",
    },
    title: {
        flex: 1,
        color: colors.text,
        fontSize: 16,
        fontWeight: "800",
        marginLeft: 8,
    },
    closeButton: {
        padding: 4,
    },
    scrollView: {
        marginTop: 10,
    },
    scrollContent: {
        paddingBottom: 24,
        gap: 16,
    },
    infoRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        backgroundColor: "rgba(148, 163, 184, 0.06)",
        padding: 10,
        borderRadius: 12,
    },
    statusLabel: {
        fontSize: 13,
        fontWeight: "700",
        color: colors.text,
    },
    readyPill: {
        borderRadius: radii.pill,
        paddingHorizontal: 8,
        paddingVertical: 4,
    },
    readyPillOk: {
        backgroundColor: "rgba(16,185,129,0.12)",
    },
    readyPillMuted: {
        backgroundColor: "rgba(148, 163, 184, 0.14)",
    },
    readyPillText: {
        fontSize: 11,
        fontWeight: "900",
    },
    readyPillTextOk: {
        color: "#10B981",
    },
    readyPillTextMuted: {
        color: colors.textMuted,
    },
    section: {
        backgroundColor: "rgba(255, 255, 255, 0.5)",
        borderRadius: 16,
        padding: 12,
        borderWidth: 1,
        borderColor: "rgba(148, 163, 184, 0.08)",
        gap: 8,
    },
    sectionTitle: {
        color: colors.text,
        fontSize: 13,
        fontWeight: "800",
        marginBottom: 2,
    },
    input: {
        borderWidth: 1,
        borderColor: "rgba(148, 163, 184, 0.2)",
        borderRadius: 10,
        paddingHorizontal: 12,
        paddingVertical: 8,
        fontSize: 13,
        color: colors.text,
        backgroundColor: "#FFFFFF",
    },
    multilineInput: {
        height: 60,
    },
    primaryButton: {
        backgroundColor: colors.primary,
        borderRadius: 10,
        height: 36,
        alignItems: "center",
        justifyContent: "center",
    },
    primaryButtonText: {
        color: "#FFFFFF",
        fontSize: 13,
        fontWeight: "800",
    },
    disabled: {
        opacity: 0.6,
    },
    pressed: {
        opacity: 0.8,
    },
    flowSelector: {
        flexDirection: "row",
        alignItems: "center",
        borderWidth: 1,
        borderColor: "rgba(148, 163, 184, 0.2)",
        borderRadius: 10,
        paddingHorizontal: 12,
        paddingVertical: 8,
        backgroundColor: "#FFFFFF",
    },
    flowSelectorBody: {
        flex: 1,
        gap: 2,
    },
    flowSelectorLabel: {
        fontSize: 13,
        fontWeight: "700",
        color: colors.text,
    },
    flowSelectorMeta: {
        fontSize: 10,
        color: colors.textMuted,
    },
    placeholderText: {
        color: colors.textSoft,
        fontWeight: "500",
    },
    flowMenu: {
        borderWidth: 1,
        borderColor: "rgba(148, 163, 184, 0.15)",
        borderRadius: 10,
        backgroundColor: "#FFFFFF",
        maxHeight: 180,
        overflow: "scroll",
        padding: 4,
    },
    flowOption: {
        padding: 8,
        borderRadius: 8,
        gap: 2,
    },
    flowOptionSelected: {
        backgroundColor: "rgba(245, 158, 11, 0.08)",
    },
    flowOptionTitle: {
        fontSize: 12,
        fontWeight: "700",
        color: colors.text,
    },
    flowOptionMeta: {
        fontSize: 10,
        color: colors.textMuted,
    },
    fieldHint: {
        fontSize: 11,
        fontWeight: "700",
        color: colors.textMuted,
        marginTop: 4,
    },
    draftItem: {
        flexDirection: "row",
        alignItems: "center",
        paddingVertical: 8,
        borderBottomWidth: 1,
        borderColor: "rgba(148, 163, 184, 0.06)",
    },
    draftBody: {
        flex: 1,
        gap: 2,
    },
    draftTitle: {
        fontSize: 12,
        fontWeight: "700",
        color: colors.text,
    },
    draftMeta: {
        fontSize: 10,
        color: colors.textMuted,
    },
    draftLockedBadge: {
        padding: 4,
    },
    emptyBody: {
        fontSize: 11,
        color: colors.textMuted,
        fontStyle: "italic",
        paddingVertical: 4,
    },
    resultText: {
        fontSize: 12,
        color: colors.text,
        backgroundColor: "rgba(148, 163, 184, 0.06)",
        padding: 8,
        borderRadius: 8,
        lineHeight: 16,
    },
});
