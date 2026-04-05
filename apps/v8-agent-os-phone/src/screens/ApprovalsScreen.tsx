import { useCallback, useEffect, useState } from "react";
import { Alert, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { Redirect, router, type Href } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";

import { ApprovalPromptCard } from "@/src/components/chat/ApprovalPromptCard";
import { GlassCard } from "@/src/components/common/GlassCard";
import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { approvePendingItem, listPendingApprovals } from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, spacing } from "@/src/theme/tokens";
import type { PendingApproval } from "@/src/types/admin";

export default function ApprovalsScreen() {
    const { status, authorizedFetch } = useAppSession();
    const { t } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const [items, setItems] = useState<PendingApproval[]>([]);
    const [refreshing, setRefreshing] = useState(false);
    const [busyId, setBusyId] = useState("");

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "connect", icon: "lan-connect", onPress: () => router.push("/connect" as Href) },
        { key: "desktop-live", icon: "monitor-dashboard", onPress: () => router.push("/desktop-live" as Href), tone: "primary" },
        { key: "rpa", icon: "robot-outline", onPress: () => router.push("/rpa" as Href), tone: "accent" },
        { key: "settings", icon: "cog-outline", onPress: () => router.push("/settings" as Href) },
    ];

    const load = useCallback(async () => {
        setRefreshing(true);
        try {
            setItems(await listPendingApprovals(authorizedFetch));
        } catch (error) {
            Alert.alert(t("读取失败", "Load failed"), error instanceof Error ? error.message : t("无法读取待处理确认", "Unable to load pending approvals"));
        } finally {
            setRefreshing(false);
        }
    }, [authorizedFetch, t]);

    useEffect(() => {
        if (status === "authenticated") {
            void load();
        }
    }, [load, status]);

    const resolve = async (approval: PendingApproval, answer: string, approve: boolean) => {
        const approvalId = approval.id || approval.approval_id;
        if (!approvalId) {
            Alert.alert(t("处理失败", "Action failed"), t("审批记录缺少 ID", "The approval record is missing its ID."));
            return;
        }
        setBusyId(approvalId);
        try {
            await approvePendingItem(authorizedFetch, approvalId, answer, approve);
            await load();
        } catch (error) {
            Alert.alert(t("处理失败", "Action failed"), error instanceof Error ? error.message : t("无法提交审批结果", "Unable to submit the approval result"));
        } finally {
            setBusyId("");
        }
    };

    if (status === "booting") {
        return <LoadingScreen label={t("正在读取待处理确认…", "Loading pending approvals...")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    return (
        <LinearGradient colors={[colors.background, "#FFF7ED"]} style={styles.gradient}>
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar actions={actions} onBrandPress={() => void goHomeToChat()} />

                <ScrollView
                    contentContainerStyle={styles.content}
                    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load()} />}
                >
                    {items.length === 0 ? (
                        <GlassCard>
                            <Text style={styles.emptyBody}>{t("当前没有待处理确认", "There are no pending approvals right now.")}</Text>
                        </GlassCard>
                    ) : null}

                    {items.map((approval, index) => {
                        const approvalId = approval.id || approval.approval_id || `approval-${index}`;
                        return (
                            <ApprovalPromptCard
                                key={`${approvalId}:${index}`}
                                approval={approval}
                                busy={busyId === approvalId}
                                onResolve={async (answer, approve) => {
                                    await resolve(approval, answer, approve);
                                }}
                            />
                        );
                    })}
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
    content: {
        paddingHorizontal: spacing.lg,
        paddingBottom: spacing.xl,
        gap: spacing.md,
    },
    emptyBody: {
        color: colors.textMuted,
        fontSize: 14,
        lineHeight: 20,
    },
});
