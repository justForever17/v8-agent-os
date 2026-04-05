import { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { GlassCard } from "@/src/components/common/GlassCard";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { PendingApproval } from "@/src/types/admin";

export function ApprovalPromptCard({
    approval,
    busy = false,
    onResolve,
}: {
    approval: PendingApproval;
    busy?: boolean;
    onResolve: (answer: string, approve: boolean) => Promise<void>;
}) {
    const [answer, setAnswer] = useState("");

    const title = approval.request?.question || approval.request?.prompt || "运行正在等待人工确认";
    const kind = approval.approval_kind || "human_input_required";

    return (
        <GlassCard>
            <View style={styles.header}>
                <View style={styles.iconShell}>
                    <MaterialCommunityIcons name="account-question-outline" size={16} color={colors.accent} />
                </View>
                <View style={styles.headerBody}>
                    <Text style={styles.headerTitle}>等待人工确认</Text>
                    <Text style={styles.headerSubtitle}>{kind}</Text>
                </View>
            </View>

            <Text style={styles.question}>{title}</Text>

            <TextInput
                value={answer}
                onChangeText={setAnswer}
                placeholder="可以输入补充说明，也可以直接批准"
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
                    <Text style={[styles.buttonText, styles.rejectText]}>拒绝</Text>
                </Pressable>
                <Pressable
                    disabled={busy}
                    onPress={() => onResolve(answer.trim(), true)}
                    style={[styles.button, styles.approveButton, busy && styles.disabled]}
                >
                    <Text style={[styles.buttonText, styles.approveText]}>
                        {busy ? "处理中…" : "批准并继续"}
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
    iconShell: {
        width: 30,
        height: 30,
        borderRadius: 15,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: colors.accentSoft,
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
