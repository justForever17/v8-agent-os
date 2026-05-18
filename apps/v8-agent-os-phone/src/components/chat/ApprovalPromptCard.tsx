import { useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { GlassCard } from "@/src/components/common/GlassCard";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { PendingApproval } from "@/src/types/admin";

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function extractSafetySummary(approval: PendingApproval) {
    const request = asRecord(approval.request);
    const safety = asRecord(request.safety);
    const details = asRecord(safety.details);
    const summary = asRecord(request.eventSummary);
    const safetySummary = Object.keys(summary).length ? summary : asRecord(safety.eventSummary);
    const nested = Object.keys(safetySummary).length ? safetySummary : asRecord(details.eventSummary);
    const rows = ["operation", "target", "host", "providerId", "credentialClass", "riskCode", "matchedRule", "nextAction"]
        .map((key) => {
            const value = nested[key];
            return typeof value === "string" && value.trim() ? { key, value: value.trim() } : null;
        })
        .filter((item): item is { key: string; value: string } => Boolean(item))
        .slice(0, 6);
    const reason = String(safety.reason || nested.reason || request.reason || "").trim();
    return { rows, reason };
}

export function ApprovalPromptCard({
    approval,
    busy = false,
    onResolve,
}: {
    approval: PendingApproval;
    busy?: boolean;
    onResolve: (answer: string, approve: boolean) => Promise<void>;
}) {
    const { t } = useUiPrefs();
    const [answer, setAnswer] = useState("");

    const title = approval.request?.question || approval.request?.prompt || t("src.components.chat.approvalpromptcard.this_run_is_waiting_for_human_confirmation");
    const kind = approval.approval_kind || "human_input_required";
    const isSafety = String(kind || approval.request?.approvalKind || "").trim().toLowerCase().startsWith("safety");
    const safetySummary = extractSafetySummary(approval);

    return (
        <GlassCard style={isSafety ? styles.safetyCard : undefined}>
            <View style={styles.header}>
                <View style={[styles.iconShell, isSafety && styles.safetyIconShell]}>
                    <MaterialCommunityIcons
                        name={isSafety ? "shield-alert-outline" : "account-question-outline"}
                        size={16}
                        color={isSafety ? colors.danger : colors.accent}
                    />
                </View>
                <View style={styles.headerBody}>
                    <Text style={[styles.headerTitle, isSafety && styles.safetyTitle]}>
                        {isSafety ? t("src.components.chat.approvalpromptcard.safety_guardian_review") : t("src.components.chat.approvalpromptcard.waiting_for_review")}
                    </Text>
                    <Text style={styles.headerSubtitle}>{kind}</Text>
                </View>
            </View>

            <Text style={styles.question}>{title}</Text>
            {isSafety && (safetySummary.reason || safetySummary.rows.length) ? (
                <ScrollView
                    style={styles.safetyDetailScroll}
                    nestedScrollEnabled
                    showsVerticalScrollIndicator={Boolean(safetySummary.reason.length > 180 || safetySummary.rows.length > 4)}
                >
                    {safetySummary.reason ? (
                        <Text style={styles.safetyReason}>{safetySummary.reason}</Text>
                    ) : null}

                    {safetySummary.rows.length ? (
                        <View style={styles.summaryBox}>
                            {safetySummary.rows.map((row) => (
                                <View key={row.key} style={styles.summaryRow}>
                                    <Text style={styles.summaryKey}>{row.key}</Text>
                                    <Text style={styles.summaryValue}>{row.value}</Text>
                                </View>
                            ))}
                        </View>
                    ) : null}
                </ScrollView>
            ) : null}

            <TextInput
                value={answer}
                onChangeText={setAnswer}
                placeholder={t("src.components.chat.approvalpromptcard.add_context_if_needed_or_approve_directly")}
                placeholderTextColor={colors.textSoft}
                multiline
                style={styles.input}
            />

            <View style={styles.actions}>
                <Pressable
                    disabled={busy}
                    onPress={() => onResolve(answer.trim(), false)}
                    style={[styles.button, styles.rejectButton, busy && styles.disabled]}
                >
                    <Text style={[styles.buttonText, styles.rejectText]}>{t("src.components.chat.approvalpromptcard.reject")}</Text>
                </Pressable>
                <Pressable
                    disabled={busy}
                    onPress={() => onResolve(answer.trim(), true)}
                    style={[styles.button, styles.approveButton, busy && styles.disabled]}
                >
                    <Text style={[styles.buttonText, styles.approveText]}>
                        {busy ? t("src.components.chat.approvalpromptcard.processing") : t("src.components.chat.approvalpromptcard.approve_and_continue")}
                    </Text>
                </Pressable>
            </View>
        </GlassCard>
    );
}

const styles = StyleSheet.create({
    header: {
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
        marginBottom: 8,
    },
    safetyCard: {
        borderColor: "rgba(239, 68, 68, 0.35)",
        backgroundColor: "rgba(255, 241, 242, 0.92)",
    },
    iconShell: {
        width: 30,
        height: 30,
        borderRadius: 15,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: colors.accentSoft,
    },
    safetyIconShell: {
        backgroundColor: "rgba(239, 68, 68, 0.12)",
    },
    headerBody: {
        flex: 1,
        gap: 2,
    },
    headerTitle: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "800",
    },
    safetyTitle: {
        color: colors.danger,
    },
    headerSubtitle: {
        color: colors.textMuted,
        fontSize: 11,
    },
    question: {
        color: colors.text,
        fontSize: 14,
        lineHeight: 20,
        marginBottom: 10,
    },
    safetyReason: {
        color: colors.textMuted,
        fontSize: 13,
        lineHeight: 19,
        marginBottom: 10,
    },
    safetyDetailScroll: {
        maxHeight: 190,
        marginBottom: 10,
    },
    summaryBox: {
        borderWidth: 1,
        borderColor: "rgba(239, 68, 68, 0.18)",
        backgroundColor: "rgba(255,255,255,0.56)",
        borderRadius: 14,
        paddingHorizontal: 10,
        paddingVertical: 8,
        gap: 6,
    },
    summaryRow: {
        flexDirection: "row",
        gap: 8,
        alignItems: "flex-start",
    },
    summaryKey: {
        width: 104,
        color: colors.textSoft,
        fontSize: 11,
        fontWeight: "800",
    },
    summaryValue: {
        flex: 1,
        color: colors.text,
        fontSize: 11,
        lineHeight: 16,
    },
    input: {
        minHeight: 64,
        borderRadius: 16,
        borderWidth: 1,
        borderColor: colors.border,
        backgroundColor: colors.surface,
        paddingHorizontal: 12,
        paddingVertical: 10,
        color: colors.text,
        textAlignVertical: "top",
    },
    actions: {
        flexDirection: "row",
        gap: spacing.sm,
        marginTop: 10,
    },
    button: {
        flex: 1,
        borderRadius: 16,
        paddingVertical: 10,
        alignItems: "center",
        justifyContent: "center",
    },
    rejectButton: {
        backgroundColor: "rgba(239, 68, 68, 0.12)",
    },
    approveButton: {
        backgroundColor: colors.primary,
    },
    buttonText: {
        fontSize: 13,
        fontWeight: "800",
    },
    rejectText: {
        color: colors.danger,
    },
    approveText: {
        color: "#FFFFFF",
    },
    disabled: {
        opacity: 0.6,
    },
});
