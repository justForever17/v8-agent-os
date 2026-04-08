import { memo } from "react";
import { StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { Badge } from "@/src/components/ui/badge";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export const AskUserCard = memo(function AskUserCard({
    question,
    status,
}: {
    question: string;
    status?: string;
}) {
    const { themeMode, t } = useUiPrefs();
    const isDark = themeMode === "dark";
    const cardText = isDark ? "#E2E8F0" : "#0F172A";
    const skyText = isDark ? "#BAE6FD" : "#0369A1";

    return (
        <View
            style={[
                styles.card,
                {
                    backgroundColor: isDark ? "rgba(14, 165, 233, 0.12)" : "rgba(240, 249, 255, 0.92)",
                    borderColor: isDark ? "rgba(14, 165, 233, 0.32)" : "rgba(186, 230, 253, 0.9)",
                },
            ]}
        >
            <View style={styles.row}>
                <View
                    style={[
                        styles.iconWrap,
                        {
                            backgroundColor: isDark ? "rgba(14, 165, 233, 0.16)" : "rgba(14, 165, 233, 0.12)",
                        },
                    ]}
                >
                    <MaterialCommunityIcons
                        name="message-question-outline"
                        size={18}
                        color={isDark ? "#7DD3FC" : "#0284C7"}
                    />
                </View>
                <View style={styles.body}>
                    <View style={styles.header}>
                        <View style={styles.headerLabel}>
                            <MaterialCommunityIcons
                                name="robot-outline"
                                size={14}
                                color={isDark ? "#BAE6FD" : "#0369A1"}
                            />
                            <Text selectable style={[styles.headerLabelText, { color: skyText }]}>
                                {t("等待你的输入", "Waiting for your answer")}
                            </Text>
                        </View>
                        {status ? <Badge variant="outline">{status}</Badge> : null}
                    </View>
                    <Text selectable style={[styles.copy, { color: cardText }]}>{question}</Text>
                    <View style={styles.footer}>
                        <MaterialCommunityIcons
                            name="arrow-right"
                            size={14}
                            color={isDark ? "#BAE6FD" : "#0369A1"}
                        />
                        <Text selectable style={[styles.footerText, { color: skyText }]}>
                            {t("请回答这个问题，当前运行会在收到回答后继续。", "Answer this question and the current run will continue once it receives your response.")}
                        </Text>
                    </View>
                </View>
            </View>
        </View>
    );
});

const styles = StyleSheet.create({
    card: {
        borderWidth: 1,
        borderRadius: 22,
        paddingHorizontal: 12,
        paddingVertical: 12,
        shadowColor: "#0F172A",
        shadowOpacity: 0.08,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 2 },
        elevation: 2,
    },
    row: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 12,
    },
    iconWrap: {
        width: 36,
        height: 36,
        borderRadius: 16,
        alignItems: "center",
        justifyContent: "center",
    },
    body: {
        flex: 1,
        gap: 8,
    },
    header: {
        flexDirection: "row",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 8,
    },
    headerLabel: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
    },
    headerLabelText: {
        fontSize: 11,
        fontWeight: "700",
        letterSpacing: 1.2,
        textTransform: "uppercase",
    },
    copy: {
        fontSize: 14,
        lineHeight: 22,
    },
    footer: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 6,
    },
    footerText: {
        flex: 1,
        fontSize: 12,
        lineHeight: 18,
        fontWeight: "600",
    },
});
