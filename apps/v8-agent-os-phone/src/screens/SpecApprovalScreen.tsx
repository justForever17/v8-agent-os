import { useCallback, useEffect, useMemo, useState } from "react";
import {
    Alert,
    Pressable,
    RefreshControl,
    ScrollView,
    StyleSheet,
    Text,
    TextInput,
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
import {
    approveSpecStage,
    editSpecStage,
    getSpecDetail,
    listSpecs,
    reviseSpecStage,
} from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { SpecDetailResponse, SpecSummary } from "@/src/types/admin";

const STAGES = ["requirements", "bugfix", "design", "tasks"];

export default function SpecApprovalScreen() {
    const { status, userAvatarUri, authorizedFetch } = useAppSession();
    const { t } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const params = useLocalSearchParams<{ workspace?: string; workspacePath?: string; specId?: string; stage?: string }>();
    const initialWorkspacePath = String(params.workspace || params.workspacePath || "").trim();
    const initialSpecId = String(params.specId || "").trim();
    const initialStage = String(params.stage || "").trim().toLowerCase();
    const [workspacePath, setWorkspacePath] = useState(initialWorkspacePath);
    const [specs, setSpecs] = useState<SpecSummary[]>([]);
    const [selectedSpecId, setSelectedSpecId] = useState(initialSpecId);
    const [selectedStage, setSelectedStage] = useState(STAGES.includes(initialStage) ? initialStage : "requirements");
    const [detail, setDetail] = useState<SpecDetailResponse | null>(null);
    const [sectionRef, setSectionRef] = useState("");
    const [comment, setComment] = useState("");
    const [content, setContent] = useState("");
    const [refreshing, setRefreshing] = useState(false);
    const [busy, setBusy] = useState(false);

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "approvals", icon: "bell-outline", onPress: () => router.push("/approvals" as Href) },
        { key: "settings", icon: "cog-outline", onPress: () => router.push("/settings" as Href) },
    ];

    const selectedSpec = useMemo(
        () => specs.find((item) => item.specId === selectedSpecId) || detail?.spec || null,
        [detail?.spec, selectedSpecId, specs],
    );
    const availableStages = useMemo(() => {
        const fromDetail = Object.keys(detail?.stages || {});
        const fromSummary = Object.keys(selectedSpec?.documents || {});
        return STAGES.filter((stage) => fromDetail.includes(stage) || fromSummary.includes(stage));
    }, [detail?.stages, selectedSpec?.documents]);
    const stageContent = detail?.stages?.[selectedStage]?.content || "";
    const stageIds = detail?.stages?.[selectedStage]?.ids || selectedSpec?.documents?.[selectedStage]?.ids || [];

    const loadSpecs = useCallback(async () => {
        if (!workspacePath.trim()) {
            Alert.alert(t("src.screens.specapprovalscreen.workspace_required"), t("src.screens.specapprovalscreen.enter_workspace_path"));
            return;
        }
        setRefreshing(true);
        try {
            const nextSpecs = await listSpecs(authorizedFetch, workspacePath.trim());
            setSpecs(nextSpecs);
            const nextSelected = selectedSpecId || nextSpecs[0]?.specId || "";
            setSelectedSpecId(nextSelected);
        } catch (error) {
            Alert.alert(t("src.screens.approvalsscreen.load_failed"), error instanceof Error ? error.message : t("src.screens.specapprovalscreen.unable_to_load_specs"));
        } finally {
            setRefreshing(false);
        }
    }, [authorizedFetch, selectedSpecId, t, workspacePath]);

    const loadDetail = useCallback(async (specId: string) => {
        if (!specId || !workspacePath.trim()) {
            setDetail(null);
            return;
        }
        setBusy(true);
        try {
            const nextDetail = await getSpecDetail(authorizedFetch, specId, workspacePath.trim());
            setDetail(nextDetail);
            const firstStage = STAGES.find((stage) => nextDetail.stages?.[stage]);
            if (firstStage && !nextDetail.stages?.[selectedStage]) {
                setSelectedStage(firstStage);
            }
        } catch (error) {
            Alert.alert(t("src.screens.approvalsscreen.load_failed"), error instanceof Error ? error.message : t("src.screens.specapprovalscreen.unable_to_load_spec_document"));
        } finally {
            setBusy(false);
        }
    }, [authorizedFetch, selectedStage, t, workspacePath]);

    useEffect(() => {
        if (selectedSpecId) {
            void loadDetail(selectedSpecId);
        }
    }, [loadDetail, selectedSpecId]);

    useEffect(() => {
        if (status === "authenticated" && workspacePath.trim()) {
            void loadSpecs();
        }
        // Run once after auth is available for deep links from chat approvals.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [status]);

    const runStageAction = async (action: "approve" | "revise" | "edit") => {
        if (!selectedSpecId) {
            Alert.alert(t("src.screens.specapprovalscreen.no_spec_selected"), t("src.screens.specapprovalscreen.select_spec_first"));
            return;
        }
        setBusy(true);
        try {
            if (action === "approve") {
                await approveSpecStage(authorizedFetch, selectedSpecId, selectedStage, workspacePath.trim(), comment);
            } else if (action === "revise") {
                if (!comment.trim()) {
                    Alert.alert(t("src.screens.specapprovalscreen.comment_required"), t("src.screens.specapprovalscreen.comment_required_detail"));
                    return;
                }
                await reviseSpecStage(authorizedFetch, selectedSpecId, selectedStage, workspacePath.trim(), sectionRef, comment);
            } else {
                if (!content.trim()) {
                    Alert.alert(t("src.screens.specapprovalscreen.content_required"), t("src.screens.specapprovalscreen.content_required_detail"));
                    return;
                }
                await editSpecStage(authorizedFetch, selectedSpecId, selectedStage, workspacePath.trim(), sectionRef, content, comment || "phone_spec_edit");
            }
            setComment("");
            setContent("");
            await loadDetail(selectedSpecId);
            await loadSpecs();
        } catch (error) {
            Alert.alert(t("src.screens.specapprovalscreen.action_failed"), error instanceof Error ? error.message : t("src.screens.specapprovalscreen.spec_action_failed"));
        } finally {
            setBusy(false);
        }
    };

    if (status === "booting") {
        return <LoadingScreen label={t("src.screens.specapprovalscreen.loading_spec_approval")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    return (
        <LinearGradient colors={[colors.background, "#FFF7ED"]} style={styles.gradient}>
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar actions={actions} userImageUri={userAvatarUri || undefined} onBrandPress={() => void goHomeToChat()} />
                <View style={styles.header}>
                    <Text style={styles.eyebrow}>SPEC APPROVAL</Text>
                    <Text style={styles.title}>{t("src.screens.specapprovalscreen.title")}</Text>
                    <Text style={styles.subtitle}>{t("src.screens.specapprovalscreen.subtitle")}</Text>
                    <View style={styles.workspaceRow}>
                        <TextInput
                            style={styles.workspaceInput}
                            value={workspacePath}
                            onChangeText={setWorkspacePath}
                            placeholder={t("src.screens.specapprovalscreen.workspace_placeholder")}
                            placeholderTextColor={colors.textSoft}
                            autoCapitalize="none"
                        />
                        <Pressable style={[styles.loadButton, refreshing ? styles.disabledButton : null]} disabled={refreshing} onPress={() => void loadSpecs()}>
                            <Text style={styles.loadButtonText}>{t("src.screens.specapprovalscreen.load")}</Text>
                        </Pressable>
                    </View>
                </View>

                <ScrollView
                    contentContainerStyle={styles.content}
                    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void loadSpecs()} />}
                >
                    {specs.length ? (
                        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.specRail}>
                            {specs.map((spec) => {
                                const active = spec.specId === selectedSpecId;
                                return (
                                    <Pressable
                                        key={spec.specId}
                                        style={[styles.specChip, active ? styles.specChipActive : null]}
                                        onPress={() => {
                                            setSelectedSpecId(spec.specId || "");
                                            setSectionRef("");
                                        }}
                                    >
                                        <Text style={[styles.specChipTitle, active ? styles.specChipTitleActive : null]} numberOfLines={2}>
                                            {spec.featureName || spec.specId}
                                        </Text>
                                        <Text style={[styles.specChipMeta, active ? styles.specChipTitleActive : null]}>
                                            {spec.currentStage || "unknown"} · {spec.pipelineControl?.runtimeExecutionAllowed ? t("src.screens.specapprovalscreen.executable") : t("src.screens.specapprovalscreen.reviewing")}
                                        </Text>
                                    </Pressable>
                                );
                            })}
                        </ScrollView>
                    ) : (
                        <GlassCard>
                            <Text style={styles.emptyText}>{t("src.screens.specapprovalscreen.empty_hint")}</Text>
                        </GlassCard>
                    )}

                    {selectedSpec ? (
                        <GlassCard>
                            <Text style={styles.detailTitle}>{selectedSpec.featureName || selectedSpec.specId}</Text>
                            <Text style={styles.detailMeta}>
                                {t("src.screens.specapprovalscreen.next_stage")}: {selectedSpec.pipelineControl?.nextStage || "unknown"} · {t("src.screens.specapprovalscreen.blocked_by")}: {selectedSpec.pipelineControl?.blockedByApproval || t("src.screens.artifactsscreen.none")}
                            </Text>
                            <View style={styles.stageRail}>
                                {availableStages.map((stage) => (
                                    <Pressable key={stage} style={[styles.stageChip, stage === selectedStage ? styles.stageChipActive : null]} onPress={() => setSelectedStage(stage)}>
                                        <Text style={[styles.stageChipText, stage === selectedStage ? styles.stageChipTextActive : null]}>{stage}</Text>
                                    </Pressable>
                                ))}
                            </View>
                        </GlassCard>
                    ) : null}

                    <View style={styles.documentPanel}>
                        <Text style={styles.documentTitle}>{selectedStage}</Text>
                        {detail?.stages?.[selectedStage]?.truncated ? <Text style={styles.warningText}>{t("src.screens.specapprovalscreen.truncated_hint")}</Text> : null}
                        <Text selectable style={styles.documentText}>{stageContent || t("src.screens.specapprovalscreen.select_stage")}</Text>
                    </View>

                    <GlassCard>
                        <Text style={styles.formLabel}>{t("src.screens.specapprovalscreen.section_id")}</Text>
                        <TextInput
                            style={styles.input}
                            value={sectionRef}
                            onChangeText={setSectionRef}
                            placeholder="REQ-001 / DES-001 / TASK-001"
                            placeholderTextColor={colors.textSoft}
                            autoCapitalize="characters"
                        />
                        {stageIds.length ? (
                            <View style={styles.idGrid}>
                                {stageIds.slice(0, 48).map((id) => (
                                    <Pressable key={id} style={[styles.idChip, sectionRef === id ? styles.idChipActive : null]} onPress={() => setSectionRef(id)}>
                                        <Text style={[styles.idChipText, sectionRef === id ? styles.idChipTextActive : null]}>{id}</Text>
                                    </Pressable>
                                ))}
                            </View>
                        ) : null}

                        <Text style={styles.formLabel}>{t("src.screens.specapprovalscreen.comment_label")}</Text>
                        <TextInput
                            style={[styles.input, styles.textArea]}
                            value={comment}
                            onChangeText={setComment}
                            placeholder={t("src.screens.specapprovalscreen.comment_placeholder")}
                            placeholderTextColor={colors.textSoft}
                            multiline
                        />

                        <Text style={styles.formLabel}>{t("src.screens.specapprovalscreen.edit_label")}</Text>
                        <TextInput
                            style={[styles.input, styles.largeTextArea]}
                            value={content}
                            onChangeText={setContent}
                            placeholder={t("src.screens.specapprovalscreen.edit_placeholder")}
                            placeholderTextColor={colors.textSoft}
                            multiline
                        />

                        <View style={styles.actionGrid}>
                            <Pressable style={[styles.primaryAction, busy ? styles.disabledButton : null]} disabled={busy} onPress={() => void runStageAction("approve")}>
                                <MaterialCommunityIcons name="check-circle-outline" color="#fff" size={18} />
                                <Text style={styles.primaryActionText}>{t("src.screens.specapprovalscreen.approve_next")}</Text>
                            </Pressable>
                            <Pressable style={[styles.secondaryAction, busy ? styles.disabledButton : null]} disabled={busy} onPress={() => void runStageAction("revise")}>
                                <Text style={styles.secondaryActionText}>{t("src.screens.specapprovalscreen.revise")}</Text>
                            </Pressable>
                            <Pressable style={[styles.secondaryAction, busy ? styles.disabledButton : null]} disabled={busy} onPress={() => void runStageAction("edit")}>
                                <Text style={styles.secondaryActionText}>{t("src.screens.specapprovalscreen.edit")}</Text>
                            </Pressable>
                        </View>
                    </GlassCard>
                </ScrollView>
            </SafeAreaView>
        </LinearGradient>
    );
}

const styles = StyleSheet.create({
    gradient: {
        flex: 1,
    },
    safeArea: {
        flex: 1,
    },
    header: {
        paddingHorizontal: spacing.lg,
        paddingBottom: spacing.md,
        gap: spacing.xs,
    },
    eyebrow: {
        color: colors.danger,
        fontSize: 11,
        fontWeight: "800",
        letterSpacing: 2,
    },
    title: {
        color: colors.text,
        fontSize: 24,
        fontWeight: "800",
    },
    subtitle: {
        color: colors.textMuted,
        fontSize: 13,
        lineHeight: 19,
    },
    workspaceRow: {
        flexDirection: "row",
        gap: spacing.sm,
        marginTop: spacing.sm,
    },
    workspaceInput: {
        flex: 1,
        minHeight: 44,
        borderRadius: radii.md,
        borderWidth: 1,
        borderColor: colors.border,
        backgroundColor: colors.surfaceStrong,
        color: colors.text,
        paddingHorizontal: spacing.md,
    },
    loadButton: {
        minHeight: 44,
        borderRadius: radii.md,
        backgroundColor: colors.text,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: spacing.lg,
    },
    loadButtonText: {
        color: "#fff",
        fontWeight: "800",
    },
    content: {
        paddingHorizontal: spacing.lg,
        paddingBottom: spacing.xxl,
        gap: spacing.md,
    },
    specRail: {
        gap: spacing.sm,
        paddingVertical: spacing.xs,
    },
    specChip: {
        width: 220,
        minHeight: 88,
        borderRadius: radii.lg,
        borderWidth: 1,
        borderColor: colors.border,
        backgroundColor: colors.surfaceStrong,
        padding: spacing.md,
    },
    specChipActive: {
        borderColor: colors.danger,
        backgroundColor: "#FFF1F2",
    },
    specChipTitle: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "800",
        lineHeight: 19,
    },
    specChipTitleActive: {
        color: "#9F1239",
    },
    specChipMeta: {
        marginTop: spacing.xs,
        color: colors.textMuted,
        fontSize: 12,
    },
    emptyText: {
        color: colors.textMuted,
        lineHeight: 20,
    },
    detailTitle: {
        color: colors.text,
        fontSize: 18,
        fontWeight: "800",
    },
    detailMeta: {
        marginTop: spacing.xs,
        color: colors.textMuted,
        fontSize: 12,
    },
    stageRail: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: spacing.xs,
        marginTop: spacing.md,
    },
    stageChip: {
        borderRadius: radii.pill,
        backgroundColor: colors.chip,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.xs,
    },
    stageChipActive: {
        backgroundColor: colors.danger,
    },
    stageChipText: {
        color: colors.textMuted,
        fontWeight: "700",
        fontSize: 12,
    },
    stageChipTextActive: {
        color: "#fff",
    },
    documentPanel: {
        minHeight: 420,
        borderRadius: radii.lg,
        borderWidth: 1,
        borderColor: colors.border,
        backgroundColor: colors.surfaceStrong,
        padding: spacing.lg,
    },
    documentTitle: {
        color: colors.text,
        fontSize: 16,
        fontWeight: "800",
        marginBottom: spacing.sm,
    },
    warningText: {
        color: colors.warning,
        marginBottom: spacing.sm,
    },
    documentText: {
        color: colors.text,
        fontSize: 14,
        lineHeight: 22,
    },
    formLabel: {
        color: colors.textMuted,
        fontSize: 12,
        fontWeight: "800",
        marginBottom: spacing.xs,
        marginTop: spacing.sm,
    },
    input: {
        borderRadius: radii.md,
        borderWidth: 1,
        borderColor: colors.border,
        backgroundColor: colors.surfaceStrong,
        color: colors.text,
        minHeight: 44,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
    },
    textArea: {
        minHeight: 88,
        textAlignVertical: "top",
    },
    largeTextArea: {
        minHeight: 140,
        textAlignVertical: "top",
    },
    idGrid: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: spacing.xs,
        marginBottom: spacing.sm,
    },
    idChip: {
        borderRadius: radii.pill,
        backgroundColor: colors.chip,
        paddingHorizontal: spacing.sm,
        paddingVertical: spacing.xs,
    },
    idChipActive: {
        backgroundColor: colors.primary,
    },
    idChipText: {
        color: colors.textMuted,
        fontSize: 11,
        fontWeight: "700",
    },
    idChipTextActive: {
        color: "#fff",
    },
    actionGrid: {
        gap: spacing.sm,
        marginTop: spacing.md,
    },
    primaryAction: {
        minHeight: 48,
        borderRadius: radii.md,
        backgroundColor: colors.success,
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "row",
        gap: spacing.xs,
    },
    primaryActionText: {
        color: "#fff",
        fontWeight: "800",
    },
    secondaryAction: {
        minHeight: 46,
        borderRadius: radii.md,
        borderWidth: 1,
        borderColor: colors.border,
        alignItems: "center",
        justifyContent: "center",
    },
    secondaryActionText: {
        color: colors.text,
        fontWeight: "800",
    },
    disabledButton: {
        opacity: 0.55,
    },
});
