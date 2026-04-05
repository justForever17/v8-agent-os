import { useCallback, useEffect, useState } from "react";
import { Alert, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { Redirect, router, type Href } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";

import { ApprovalPromptCard } from "@/src/components/chat/ApprovalPromptCard";
import { GlassCard } from "@/src/components/common/GlassCard";
import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { approvePendingItem, listPendingApprovals } from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { colors, spacing } from "@/src/theme/tokens";
import type { PendingApproval } from "@/src/types/admin";

export default function ApprovalsScreen() {
    const { status, authorizedFetch } = useAppSession();
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
            Alert.alert("读取失败", error instanceof Error ? error.message : "无法读取待处理确认");
        } finally {
            setRefreshing(false);
        }
    }, [authorizedFetch]);

    useEffect(() => {
        if (status === "authenticated") {
            void load();
        }
    }, [load, status]);

    const resolve = async (approval: PendingApproval, answer: string, approve: boolean) => {
        const approvalId = approval.id || approval.approval_id;
        if (!approvalId) {
            Alert.alert("处理失败", "审批记录缺少 ID");
            return;
        }
        setBusyId(approvalId);
        try {
            await approvePendingItem(authorizedFetch, approvalId, answer, approve);
            await load();
        } catch (error) {
            Alert.alert("处理失败", error instanceof Error ? error.message : "无法提交审批结果");
        } finally {
            setBusyId("");
        }
    };

    if (status === "booting") {
        return <LoadingScreen label="正在读取待处理确认…" />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    return (
        <LinearGradient colors={[colors.background, "#FFF7ED"]} style={styles.gradient}>
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar actions={actions} />

                <ScrollView
                    contentContainerStyle={styles.content}
                    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load()} />}
                >
                    {items.length === 0 ? (
                        <GlassCard>
                            <Text style={styles.emptyBody}>当前没有待处理确认</Text>
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
