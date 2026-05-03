import { memo, useMemo, useState } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";

import { MarkdownRenderer } from "@/src/components/chat/MarkdownRenderer";
import { Button } from "@/src/components/ui/button";
import { Card, CardContent } from "@/src/components/ui/card";
import { Textarea } from "@/src/components/ui/textarea";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { PendingApproval } from "@/src/types/admin";

function readFirstString(...values: unknown[]) {
    for (const value of values) {
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
    }
    return "";
}

function stringifyCommand(value: unknown) {
    if (typeof value === "string" && value.trim()) {
        return value.trim();
    }
    if (value && typeof value === "object") {
        try {
            return JSON.stringify(value, null, 2);
        } catch {
            return "";
        }
    }
    return "";
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function extractEventSummary(request: Record<string, unknown>, safety: Record<string, unknown>) {
    const direct = asRecord(request.eventSummary);
    if (Object.keys(direct).length) return direct;
    const safetySummary = asRecord(safety.eventSummary);
    if (Object.keys(safetySummary).length) return safetySummary;
    const details = asRecord(safety.details);
    return asRecord(details.eventSummary);
}

function eventSummaryRows(summary: Record<string, unknown>) {
    return ["operation", "target", "host", "providerId", "credentialClass", "riskCode", "matchedRule", "nextAction"]
        .map((key) => {
            const value = summary[key];
            return typeof value === "string" && value.trim() ? { key, value: value.trim() } : null;
        })
        .filter((item): item is { key: string; value: string } => Boolean(item))
        .slice(0, 8);
}

function extractApprovalDetails(approval: PendingApproval) {
    const request = approval.request || {};
    const safety = request.safety && typeof request.safety === "object"
        ? request.safety as Record<string, unknown>
        : {};
    const prompt = readFirstString(request.prompt, request.question);
    const reason = readFirstString(
        safety.reason,
        safety.summary,
        request.reason,
        request.summary,
        prompt,
    );
    const command = stringifyCommand(
        safety.command
        ?? request.command
        ?? request.args
        ?? request.payload,
    );
    const riskSummary = readFirstString(
        safety.riskSummary,
        safety.risk_summary,
        safety.reason,
        request.reason,
        request.summary,
    );

    return {
        prompt: prompt || reason,
        reason,
        command,
        riskSummary,
        eventSummary: extractEventSummary(request as Record<string, unknown>, safety),
    };
}

export const GovernanceApprovalModal = memo(function GovernanceApprovalModal({
    visible,
    approval,
    busy = false,
    onApprove,
    onReject,
    onViewDetails,
    onClose,
}: {
    visible: boolean;
    approval: PendingApproval | null;
    busy?: boolean;
    onApprove: (answer: string) => void | Promise<void>;
    onReject: (answer: string) => void | Promise<void>;
    onViewDetails: () => void;
    onClose: () => void;
}) {
    const { colors, t, themeMode } = useUiPrefs();
    const [answer, setAnswer] = useState("");
    const details = useMemo(() => approval ? extractApprovalDetails(approval) : null, [approval]);
    const summaryRows = useMemo(() => details ? eventSummaryRows(details.eventSummary) : [], [details]);

    if (!visible) {
        return null;
    }

    const resolvedPrompt = details?.prompt || t("src.components.chat.governanceapprovalmodal.syncing_governance_approval_details_please_wait");
    const resolvedReason = details?.riskSummary || t("src.components.chat.governanceapprovalmodal.the_current_run_is_paused_while_approval_details_are_being_synchronized");
    const resolvedCommand = details?.command || "";
    const actionsDisabled = busy || !approval;

    return (
        <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
            <View style={[styles.overlay, { backgroundColor: themeMode === "dark" ? "rgba(0,0,0,0.62)" : "rgba(15,23,42,0.40)" }]}>
                <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
                <Card style={[styles.card, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <LinearGradient
                        colors={
                            themeMode === "dark"
                                ? ["rgba(24,24,27,0.99)", "rgba(15,15,18,0.98)"]
                                : ["rgba(255,255,255,0.985)", "rgba(247,244,238,0.975)"]
                        }
                        style={StyleSheet.absoluteFill}
                    />
                    <View style={[styles.header, { borderBottomColor: colors.border }]}>
                        <View style={[styles.headerIcon, { backgroundColor: "rgba(245,158,11,0.14)" }]}>
                            <MaterialCommunityIcons name="shield-alert-outline" size={18} color={colors.warning} />
                        </View>
                        <View style={styles.headerText}>
                            <Text style={[styles.eyebrow, { color: colors.warning }]}>
                                {t("src.components.chat.governanceapprovalmodal.governance_approval")}
                            </Text>
                            <Text style={[styles.title, { color: colors.text }]}>
                                {t("src.components.chat.governanceapprovalmodal.safety_guardian_needs_your_approval")}
                            </Text>
                            <Text style={[styles.subtitle, { color: colors.textMuted }]}>
                                {approval
                                    ? t("src.components.chat.governanceapprovalmodal.the_current_run_is_paused_and_will_resume_the_original_command_after_approval")
                                    : t("src.components.chat.governanceapprovalmodal.the_current_run_is_paused_while_approval_details_are_loading")}
                            </Text>
                        </View>
                    </View>

                    <CardContent style={styles.content}>
                        <View style={[styles.detailCard, { borderColor: colors.border, backgroundColor: colors.surface }]}>
                            <Text style={[styles.sectionLabel, { color: colors.textSoft }]}>
                                {t("src.components.chat.governanceapprovalmodal.approval_reason")}
                            </Text>
                            <MarkdownRenderer content={resolvedPrompt} />
                        </View>

                        {resolvedReason ? (
                            <View style={[styles.detailCard, { borderColor: colors.border, backgroundColor: colors.surface }]}>
                                <Text style={[styles.sectionLabel, { color: colors.textSoft }]}>
                                    {t("src.components.chat.governanceapprovalmodal.risk_summary")}
                                </Text>
                                <Text style={[styles.detailText, { color: colors.text }]}>{resolvedReason}</Text>
                            </View>
                        ) : null}

                        {summaryRows.length ? (
                            <View style={[styles.detailCard, styles.summaryCard, { borderColor: "rgba(245,158,11,0.30)", backgroundColor: themeMode === "dark" ? "rgba(245,158,11,0.10)" : "rgba(255,251,235,0.92)" }]}>
                                <Text style={[styles.sectionLabel, { color: colors.warning }]}>
                                    {t("src.components.chat.governanceapprovalmodal.event_summary")}
                                </Text>
                                <View style={styles.summaryRows}>
                                    {summaryRows.map((row) => (
                                        <View key={row.key} style={styles.summaryRow}>
                                            <Text style={[styles.summaryKey, { color: colors.textSoft }]}>{row.key}</Text>
                                            <Text style={[styles.summaryValue, { color: colors.text }]}>{row.value}</Text>
                                        </View>
                                    ))}
                                </View>
                            </View>
                        ) : null}

                        {resolvedCommand ? (
                            <View style={[styles.detailCard, { borderColor: colors.border, backgroundColor: colors.surface }]}>
                                <Text style={[styles.sectionLabel, { color: colors.textSoft }]}>
                                    {t("src.components.chat.governanceapprovalmodal.command")}
                                </Text>
                                <Text style={[styles.commandText, { color: colors.text }]}>{resolvedCommand}</Text>
                            </View>
                        ) : null}

                        <View style={styles.answerSection}>
                            <Text style={[styles.sectionLabel, { color: colors.textSoft }]}>
                                {t("src.components.chat.governanceapprovalmodal.optional_note")}
                            </Text>
                            <Textarea
                                value={answer}
                                onChangeText={setAnswer}
                                placeholder={t("src.components.chat.governanceapprovalmodal.you_can_add_context_for_the_approval_or_approve_directly")}
                                style={styles.textarea}
                                editable={!actionsDisabled}
                            />
                        </View>
                    </CardContent>

                    <View style={[styles.footer, { borderTopColor: colors.border, backgroundColor: themeMode === "dark" ? "rgba(24,24,27,0.50)" : "rgba(255,255,255,0.72)" }]}>
                        <Button variant="ghost" onPress={onClose} disabled={busy}>
                            {t("src.components.chat.governanceapprovalmodal.dismiss")}
                        </Button>
                        <Button variant="outline" onPress={onViewDetails} disabled={busy}>
                            {t("src.components.chat.governanceapprovalmodal.view_details")}
                        </Button>
                        <Button variant="outline" onPress={() => void onReject(answer.trim())} disabled={actionsDisabled}>
                            {busy ? t("src.components.chat.askusermodal.processing") : t("src.components.chat.approvalpromptcard.reject")}
                        </Button>
                        <Button onPress={() => void onApprove(answer.trim())} disabled={actionsDisabled}>
                            {busy ? t("src.components.chat.askusermodal.processing") : t("src.components.chat.governanceapprovalmodal.approve_and_continue")}
                        </Button>
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
        paddingHorizontal: 16,
        paddingVertical: 24,
    },
    card: {
        width: "100%",
        maxWidth: 560,
        borderRadius: 28,
        borderWidth: 1,
        overflow: "hidden",
        shadowColor: "#0F172A",
        shadowOpacity: 0.18,
        shadowRadius: 28,
        shadowOffset: { width: 0, height: 16 },
        elevation: 22,
    },
    header: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: spacing.md,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.md,
        borderBottomWidth: StyleSheet.hairlineWidth,
    },
    headerIcon: {
        width: 40,
        height: 40,
        borderRadius: 16,
        alignItems: "center",
        justifyContent: "center",
    },
    headerText: {
        flex: 1,
        gap: 4,
    },
    eyebrow: {
        fontSize: 11,
        fontWeight: "800",
        letterSpacing: 1.2,
        textTransform: "uppercase",
    },
    title: {
        fontSize: 18,
        fontWeight: "800",
        lineHeight: 24,
        letterSpacing: -0.3,
    },
    subtitle: {
        fontSize: 12,
        lineHeight: 18,
    },
    content: {
        gap: spacing.md,
        paddingTop: spacing.md,
    },
    detailCard: {
        borderRadius: radii.lg,
        borderWidth: 1,
        paddingHorizontal: 14,
        paddingVertical: 14,
    },
    sectionLabel: {
        marginBottom: 8,
        fontSize: 10,
        fontWeight: "800",
        letterSpacing: 1.4,
        textTransform: "uppercase",
    },
    detailText: {
        fontSize: 13,
        lineHeight: 20,
    },
    commandText: {
        fontSize: 12,
        lineHeight: 18,
        fontFamily: "monospace",
    },
    summaryCard: {
        gap: 0,
    },
    summaryRows: {
        gap: 7,
    },
    summaryRow: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 8,
    },
    summaryKey: {
        width: 112,
        fontSize: 11,
        fontWeight: "800",
    },
    summaryValue: {
        flex: 1,
        fontSize: 11,
        lineHeight: 16,
    },
    answerSection: {
        gap: 8,
    },
    textarea: {
        minHeight: 110,
        textAlignVertical: "top",
    },
    footer: {
        flexDirection: "row",
        flexWrap: "wrap",
        justifyContent: "flex-end",
        gap: spacing.sm,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.md,
        borderTopWidth: StyleSheet.hairlineWidth,
    },
});
