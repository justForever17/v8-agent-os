import { memo, useEffect, useState } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";

import { MarkdownRenderer } from "@/src/components/chat/MarkdownRenderer";
import { Button } from "@/src/components/ui/button";
import { Card, CardContent } from "@/src/components/ui/card";
import { Textarea } from "@/src/components/ui/textarea";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type AskUserModalProps = {
    visible: boolean;
    question: string;
    toolCallId: string;
    busy?: boolean;
    onSubmit: (toolCallId: string, answer: string, approve: boolean) => void | Promise<void>;
    onCancel?: () => void;
};

export const AskUserModal = memo(function AskUserModal({
    visible,
    question,
    toolCallId,
    busy = false,
    onSubmit,
    onCancel,
}: AskUserModalProps) {
    const { colors, t, themeMode } = useUiPrefs();
    const [answer, setAnswer] = useState("");

    useEffect(() => {
        if (!visible) {
            setAnswer("");
        }
    }, [visible]);

    if (!visible) {
        return null;
    }

    return (
        <Modal visible={visible} transparent animationType="fade" onRequestClose={onCancel}>
            <View style={[styles.overlay, { backgroundColor: themeMode === "dark" ? "rgba(0,0,0,0.58)" : "rgba(15,23,42,0.38)" }]}>
                <Pressable style={StyleSheet.absoluteFill} onPress={onCancel} />
                <Card style={[styles.card, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <LinearGradient
                        colors={
                            themeMode === "dark"
                                ? ["rgba(24,24,27,0.985)", "rgba(15,15,18,0.975)"]
                                : ["rgba(255,255,255,0.98)", "rgba(247,244,238,0.97)"]
                        }
                        style={StyleSheet.absoluteFill}
                    />
                    <View style={[styles.header, { borderBottomColor: colors.border }]}>
                        <View style={[styles.headerIcon, { backgroundColor: "rgba(124,58,237,0.12)" }]}>
                            <MaterialCommunityIcons name="message-processing-outline" size={18} color={colors.primary} />
                        </View>
                        <View style={styles.headerText}>
                            <Text style={[styles.eyebrow, { color: colors.primary }]}>
                                {t("Supervisor 需要你的输入", "Supervisor needs your input")}
                            </Text>
                            <Text style={[styles.title, { color: colors.text }]}>
                                {t("继续前需要你确认一件事", "One quick answer before we continue")}
                            </Text>
                            <Text style={[styles.subtitle, { color: colors.textMuted }]}>
                                {t("你的回答会回填给当前运行，不会把你带离当前页面。", "Your answer is fed back into the active run without taking you away from the current page.")}
                            </Text>
                        </View>
                    </View>

                    <CardContent style={styles.content}>
                        <View style={[styles.questionCard, { borderColor: colors.border, backgroundColor: colors.surface }]}>
                            <Text style={[styles.sectionLabel, { color: colors.textSoft }]}>
                                {t("当前问题", "Current question")}
                            </Text>
                            <MarkdownRenderer content={question} />
                        </View>

                        <View style={styles.answerSection}>
                            <Text style={[styles.sectionLabel, { color: colors.textSoft }]}>
                                {t("你的回应", "Your answer")}
                            </Text>
                            <Textarea
                                value={answer}
                                onChangeText={setAnswer}
                                placeholder={t("用一两句话直接回答，或补充继续执行所需的信息。", "Answer briefly, or provide the missing information needed to continue.")}
                                style={styles.textarea}
                                editable={!busy}
                            />
                            <Text style={[styles.hint, { color: colors.textMuted }]}>
                                {t("如果暂时不想继续，可以直接拒绝，本次运行会停在等待输入的位置。", "If you do not want to continue right now, reject it and the run will stay at the waiting-for-input point.")}
                            </Text>
                        </View>
                    </CardContent>

                    <View style={[styles.footer, { borderTopColor: colors.border, backgroundColor: themeMode === "dark" ? "rgba(24,24,27,0.48)" : "rgba(255,255,255,0.72)" }]}>
                        <Button variant="ghost" onPress={onCancel} disabled={busy}>
                            {t("先放着", "Dismiss")}
                        </Button>
                        <Button variant="outline" onPress={() => void onSubmit(toolCallId, answer, false)} disabled={busy}>
                            {busy ? t("处理中...", "Processing...") : t("拒绝继续", "Reject")}
                        </Button>
                        <Button onPress={() => void onSubmit(toolCallId, answer, true)} disabled={busy || !answer.trim()}>
                            {busy ? t("发送中...", "Sending...") : t("发送并继续", "Send and continue")}
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
    questionCard: {
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
    answerSection: {
        gap: 8,
    },
    textarea: {
        minHeight: 124,
        textAlignVertical: "top",
    },
    hint: {
        fontSize: 11,
        lineHeight: 17,
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
