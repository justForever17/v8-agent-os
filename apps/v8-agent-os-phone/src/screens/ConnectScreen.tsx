import { Redirect, router, type Href } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
    Alert,
    ActivityIndicator,
    Pressable,
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
import {
    type AdminConnectionProfile,
    readActiveAdminConnectionProfileId,
    readAdminConnectionProfiles,
    removeAdminConnectionProfile,
    upsertAdminConnectionProfile,
    writeActiveAdminConnectionProfileId,
    writeAdminConnectionProfiles,
} from "@/src/lib/admin-connection-profiles";
import { getConnectionSummary, listProjects } from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { ConnectionSummary, ProjectSummary } from "@/src/types/admin";

function formatTransportKind(kind?: string) {
    const normalized = String(kind || "manual_url").replace(/-/g, "_");
    if (normalized === "lan") return "LAN";
    if (normalized === "wireguard") return "WireGuard";
    if (normalized === "tailscale") return "Tailscale";
    if (normalized === "custom_vpn") return "Custom VPN";
    return "Manual URL";
}

export default function ConnectScreen() {
    const { status, user, adminBaseUrl, setAdminBaseUrl, signOut, authorizedFetch } = useAppSession();
    const { t } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const profileImageUri = resolveAdminAssetUrl(adminBaseUrl, user?.image || "");
    const [draftBaseUrl, setDraftBaseUrl] = useState(adminBaseUrl);
    const [busy, setBusy] = useState(false);
    const [summary, setSummary] = useState<ConnectionSummary | null>(null);
    const [projects, setProjects] = useState<ProjectSummary[]>([]);
    const [refreshing, setRefreshing] = useState(false);
    const [profiles, setProfiles] = useState<AdminConnectionProfile[]>([]);
    const [activeProfileId, setActiveProfileId] = useState("");

    useEffect(() => {
        setDraftBaseUrl(adminBaseUrl);
    }, [adminBaseUrl]);

    const refreshProfiles = useCallback(async () => {
        const [nextProfiles, nextActiveId] = await Promise.all([
            readAdminConnectionProfiles(),
            readActiveAdminConnectionProfileId(),
        ]);
        setProfiles(nextProfiles);
        setActiveProfileId(nextActiveId || "");
    }, []);

    useEffect(() => {
        void refreshProfiles();
    }, [refreshProfiles]);

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "desktop-live", icon: "monitor-dashboard", onPress: () => router.push("/desktop-live" as Href) },
        { key: "rpa", icon: "robot-outline", onPress: () => router.push("/rpa" as Href) },
        { key: "settings", icon: "cog-outline", onPress: () => router.push("/settings" as Href) },
    ];

    useEffect(() => {
        if (status !== "authenticated") {
            return;
        }
        let cancelled = false;
        const hydrate = async () => {
            setRefreshing(true);
            try {
                const [nextSummary, nextProjects] = await Promise.all([
                    getConnectionSummary(authorizedFetch),
                    listProjects(authorizedFetch).catch(() => []),
                ]);
                if (!cancelled) {
                    setSummary(nextSummary);
                    setProjects(nextProjects);
                    const storedProfiles = await readAdminConnectionProfiles();
                    const { profile, profiles: nextProfiles } = upsertAdminConnectionProfile(storedProfiles, {
                        adminBaseUrl,
                        summary: nextSummary,
                    });
                    await Promise.all([
                        writeAdminConnectionProfiles(nextProfiles),
                        writeActiveAdminConnectionProfileId(profile?.id || null),
                    ]);
                    if (!cancelled) {
                        setProfiles(nextProfiles);
                        setActiveProfileId(profile?.id || "");
                    }
                }
            } catch (error) {
                if (!cancelled) {
                    Alert.alert(t("src.screens.approvalsscreen.load_failed"), error instanceof Error ? error.message : t("src.screens.connectscreen.unable_to_load_the_connection_summary"));
                }
            } finally {
                if (!cancelled) {
                    setRefreshing(false);
                }
            }
        };
        void hydrate();
        return () => {
            cancelled = true;
        };
    }, [adminBaseUrl, authorizedFetch, status, t]);

    if (status === "booting") {
        return <LoadingScreen label={t("src.screens.connectscreen.loading_connection_details")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    const saveAndReconnect = async () => {
        const nextUrl = draftBaseUrl.trim();
        if (!nextUrl) {
            Alert.alert(t("src.components.chat.mediaviewerlightbox.save_failed"), t("src.screens.connectscreen.please_enter_a_reachable_admin_url_first"));
            return;
        }
        if (nextUrl === adminBaseUrl) {
            Alert.alert(t("src.screens.connectscreen.no_change_needed"), t("src.screens.connectscreen.phone_is_already_connected_to_this_admin_url"));
            return;
        }
        setBusy(true);
        try {
            const { profile, profiles: nextProfiles } = upsertAdminConnectionProfile(profiles, { adminBaseUrl: nextUrl });
            await Promise.all([
                writeAdminConnectionProfiles(nextProfiles),
                writeActiveAdminConnectionProfileId(profile?.id || null),
                setAdminBaseUrl(nextUrl),
            ]);
            setProfiles(nextProfiles);
            setActiveProfileId(profile?.id || "");
            await signOut();
            router.replace("/login");
        } catch (error) {
            Alert.alert(t("src.screens.connectscreen.switch_failed"), error instanceof Error ? error.message : t("src.screens.connectscreen.unable_to_switch_the_connection_url"));
        } finally {
            setBusy(false);
        }
    };

    const reconnectProfile = async (profile: AdminConnectionProfile) => {
        if (!profile.adminBaseUrl) {
            return;
        }
        setDraftBaseUrl(profile.adminBaseUrl);
        if (profile.adminBaseUrl === adminBaseUrl) {
            Alert.alert(t("src.screens.connectscreen.already_connected"), t("src.screens.connectscreen.phone_is_already_connected_to_this_target"));
            return;
        }
        setBusy(true);
        try {
            const { profile: updatedProfile, profiles: nextProfiles } = upsertAdminConnectionProfile(profiles, {
                adminBaseUrl: profile.adminBaseUrl,
                profileId: profile.id,
                label: profile.label,
            });
            await Promise.all([
                writeAdminConnectionProfiles(nextProfiles),
                writeActiveAdminConnectionProfileId(updatedProfile?.id || profile.id),
                setAdminBaseUrl(profile.adminBaseUrl),
            ]);
            setProfiles(nextProfiles);
            setActiveProfileId(updatedProfile?.id || profile.id);
            await signOut();
            router.replace("/login");
        } catch (error) {
            Alert.alert(t("src.screens.connectscreen.reconnect_failed"), error instanceof Error ? error.message : t("src.screens.connectscreen.unable_to_switch_connection_target"));
        } finally {
            setBusy(false);
        }
    };

    const deleteProfile = async (profile: AdminConnectionProfile) => {
        const nextProfiles = removeAdminConnectionProfile(profiles, profile.id);
        const nextActiveId = activeProfileId === profile.id ? "" : activeProfileId;
        await Promise.all([
            writeAdminConnectionProfiles(nextProfiles),
            writeActiveAdminConnectionProfileId(nextActiveId || null),
        ]);
        setProfiles(nextProfiles);
        setActiveProfileId(nextActiveId);
    };

    return (
        <LinearGradient
            colors={[colors.background, "#FFF7ED"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.gradient}
        >
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar actions={actions} userImageUri={profileImageUri || undefined} onBrandPress={() => void goHomeToChat()} />

                <ScrollView contentContainerStyle={styles.content}>
                    <GlassCard style={styles.heroCard}>
                        <View style={styles.connectionHeroHeader}>
                            <View style={styles.connectionIcon}>
                                <MaterialCommunityIcons name="access-point-network" size={22} color={colors.primaryDeep} />
                            </View>
                            <View style={styles.connectionHeroBody}>
                                <Text style={styles.heroTitle}>{t("src.screens.connectscreen.current_connection")}</Text>
                                <Text style={styles.heroSubtitle} numberOfLines={1}>
                                    {summary?.connection?.adminBaseUrl || adminBaseUrl || t("src.screens.connectscreen.not_connected")}
                                </Text>
                            </View>
                            {refreshing ? (
                                <ActivityIndicator color={colors.primary} size="small" />
                            ) : (
                                <View style={[styles.reachabilityPill, summary?.connection?.reachable ? styles.reachabilityOk : styles.reachabilityWarn]}>
                                    <Text style={[styles.reachabilityPillText, summary?.connection?.reachable ? styles.reachabilityTextOk : styles.reachabilityTextWarn]}>
                                        {summary?.connection?.reachable ? t("src.screens.connectscreen.reachable") : t("src.screens.connectscreen.unverified")}
                                    </Text>
                                </View>
                            )}
                        </View>
                        <View style={styles.connectionChips}>
                            <View style={styles.connectionChip}>
                                <Text style={styles.connectionChipLabel}>{t("src.screens.connectscreen.v8_link_route")}</Text>
                                <Text style={styles.connectionChipValue} numberOfLines={1}>
                                    {formatTransportKind(summary?.connection?.transportKind || summary?.linkManifest?.transportKind)}
                                </Text>
                            </View>
                            <View style={styles.connectionChip}>
                                <Text style={styles.connectionChipLabel}>{t("src.screens.connectscreen.bridge_mode")}</Text>
                                <Text style={styles.connectionChipValue} numberOfLines={1}>{summary?.connection?.bridgeMode || "unknown"}</Text>
                            </View>
                            <View style={styles.connectionChip}>
                                <Text style={styles.connectionChipLabel}>{t("src.screens.connectscreen.current_user")}</Text>
                                <Text style={styles.connectionChipValue} numberOfLines={1}>{summary?.user?.name || user?.name || user?.login || t("src.screens.connectscreen.unknown_user")}</Text>
                            </View>
                        </View>
                        {(summary?.connection?.vpnDiagnostics?.warnings || summary?.linkManifest?.diagnostics?.warnings || []).length > 0 ? (
                            <View style={styles.warningStrip}>
                                <MaterialCommunityIcons name="alert-circle-outline" size={16} color={colors.warning} />
                                <Text style={styles.warningStripText} numberOfLines={2}>
                                    {(summary?.connection?.vpnDiagnostics?.warnings || summary?.linkManifest?.diagnostics?.warnings || []).slice(0, 3).join(" · ")}
                                </Text>
                            </View>
                        ) : null}
                        {projects.length > 0 ? (
                            <View style={styles.projectInlineList}>
                                {projects.slice(0, 3).map((project) => (
                                    <View key={project.id || project.slug || project.name} style={styles.projectInlineChip}>
                                        <MaterialCommunityIcons name="briefcase-outline" size={14} color={colors.primaryDeep} />
                                        <Text style={styles.projectInlineText} numberOfLines={1}>
                                            {project.name || project.slug || project.id || t("src.screens.connectscreen.untitled_project")}
                                        </Text>
                                    </View>
                                ))}
                            </View>
                        ) : null}

                        <Text style={styles.sectionTitle}>{t("src.screens.connectscreen.switch_admin")}</Text>
                        <TextInput
                            value={draftBaseUrl}
                            onChangeText={setDraftBaseUrl}
                            autoCapitalize="none"
                            autoCorrect={false}
                            placeholder="http://127.0.0.1:9528"
                            placeholderTextColor={colors.textSoft}
                            style={styles.input}
                        />
                        <Pressable style={[styles.primaryButton, busy && styles.disabled]} onPress={() => void saveAndReconnect()}>
                            <Text style={styles.primaryButtonText}>{busy ? t("src.screens.connectscreen.switching") : t("src.screens.connectscreen.save_and_sign_in_again")}</Text>
                        </Pressable>
                    </GlassCard>

                    <GlassCard>
                        <View style={styles.sectionTitleRow}>
                            <Text style={styles.sectionTitle}>{t("src.screens.connectscreen.saved_targets")}</Text>
                            <Pressable style={styles.refreshProfilesButton} onPress={() => void refreshProfiles()}>
                                <MaterialCommunityIcons name="refresh" size={15} color={colors.textSoft} />
                            </Pressable>
                        </View>
                        {profiles.length === 0 ? (
                            <Text style={styles.emptyProfilesText}>
                                {t("src.screens.connectscreen.admin_targets_you_have_signed_into_will_appear_here")}
                            </Text>
                        ) : (
                            <View style={styles.profileList}>
                                {profiles.map((profile) => {
                                    const active = profile.id === activeProfileId || profile.adminBaseUrl === adminBaseUrl;
                                    return (
                                        <View key={profile.id} style={[styles.profileCard, active && styles.profileCardActive]}>
                                            <View style={styles.profileMain}>
                                                <View style={styles.profileTitleRow}>
                                                    <Text style={styles.profileTitle} numberOfLines={1}>
                                                        {profile.label || profile.adminBaseUrl}
                                                    </Text>
                                                    {active ? (
                                                        <View style={styles.currentBadge}>
                                                            <Text style={styles.currentBadgeText}>{t("src.screens.connectscreen.current")}</Text>
                                                        </View>
                                                    ) : null}
                                                </View>
                                                <Text style={styles.profileUrl} numberOfLines={1}>{profile.adminBaseUrl}</Text>
                                                <Text style={styles.profileMeta} numberOfLines={1}>
                                                    {[
                                                        profile.bridgeMode || "",
                                                        profile.transportKind ? formatTransportKind(profile.transportKind) : "",
                                                        profile.reachable === true ? t("src.screens.connectscreen.reachable_2") : profile.reachable === false ? t("src.screens.connectscreen.unreachable") : "",
                                                        profile.lastUsedAt ? new Date(profile.lastUsedAt).toLocaleString() : "",
                                                    ].filter(Boolean).join(" · ")}
                                                </Text>
                                            </View>
                                            <View style={styles.profileActions}>
                                                <Pressable style={styles.profileActionButton} onPress={() => void reconnectProfile(profile)} disabled={busy}>
                                                    <Text style={styles.profileActionText}>{t("src.screens.connectscreen.reconnect")}</Text>
                                                </Pressable>
                                                <Pressable style={styles.profileDeleteButton} onPress={() => void deleteProfile(profile)} disabled={busy}>
                                                    <MaterialCommunityIcons name="trash-can-outline" size={16} color={colors.textSoft} />
                                                </Pressable>
                                            </View>
                                        </View>
                                    );
                                })}
                            </View>
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
        backgroundColor: colors.surface,
        borderColor: "rgba(148,163,184,0.18)",
    },
    connectionHeroHeader: {
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
        marginBottom: 14,
    },
    connectionIcon: {
        width: 42,
        height: 42,
        borderRadius: 16,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(124,58,237,0.10)",
    },
    connectionHeroBody: {
        flex: 1,
        gap: 3,
    },
    heroTitle: {
        color: colors.text,
        fontSize: 18,
        fontWeight: "900",
        letterSpacing: -0.2,
    },
    heroSubtitle: {
        color: colors.textMuted,
        fontSize: 12,
        fontWeight: "700",
    },
    reachabilityPill: {
        borderRadius: radii.pill,
        paddingHorizontal: 9,
        paddingVertical: 6,
    },
    reachabilityOk: {
        backgroundColor: "rgba(16,185,129,0.12)",
    },
    reachabilityWarn: {
        backgroundColor: "rgba(245,158,11,0.13)",
    },
    reachabilityPillText: {
        fontSize: 11,
        fontWeight: "900",
    },
    reachabilityTextOk: {
        color: colors.success,
    },
    reachabilityTextWarn: {
        color: colors.warning,
    },
    connectionChips: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 8,
        marginBottom: 12,
    },
    connectionChip: {
        minWidth: "31%",
        flexGrow: 1,
        borderRadius: 18,
        paddingHorizontal: 10,
        paddingVertical: 9,
        backgroundColor: colors.surface,
        borderWidth: 1,
        borderColor: "rgba(148,163,184,0.18)",
    },
    connectionChipLabel: {
        color: colors.textSoft,
        fontSize: 10,
        fontWeight: "900",
        marginBottom: 3,
    },
    connectionChipValue: {
        color: colors.text,
        fontSize: 12,
        fontWeight: "800",
    },
    warningStrip: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        borderRadius: 18,
        paddingHorizontal: 11,
        paddingVertical: 10,
        backgroundColor: "rgba(245,158,11,0.09)",
        borderWidth: 1,
        borderColor: "rgba(245,158,11,0.18)",
        marginBottom: 10,
    },
    warningStripText: {
        flex: 1,
        color: colors.warning,
        fontSize: 12,
        lineHeight: 17,
        fontWeight: "700",
    },
    sectionTitleRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: spacing.md,
    },
    sectionTitle: {
        color: colors.text,
        fontSize: 15,
        fontWeight: "800",
        marginBottom: spacing.md,
    },
    summaryRow: {
        gap: 4,
        marginBottom: spacing.sm,
    },
    summaryLabel: {
        color: colors.textSoft,
        fontSize: 11,
        fontWeight: "800",
        textTransform: "uppercase",
        letterSpacing: 0.8,
    },
    summaryValue: {
        color: colors.text,
        fontSize: 14,
        lineHeight: 20,
    },
    okText: {
        color: colors.success,
    },
    warnText: {
        color: colors.warning,
    },
    projectList: {
        gap: spacing.sm,
    },
    projectInlineList: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 8,
        marginTop: spacing.sm,
        marginBottom: spacing.md,
    },
    projectInlineChip: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderRadius: radii.pill,
        paddingHorizontal: 10,
        paddingVertical: 7,
        backgroundColor: "rgba(124,58,237,0.08)",
        borderWidth: 1,
        borderColor: "rgba(124,58,237,0.14)",
        maxWidth: "100%",
    },
    projectInlineText: {
        color: colors.primaryDeep,
        fontSize: 12,
        fontWeight: "700",
    },
    projectCard: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: spacing.sm,
        borderRadius: radii.md,
        backgroundColor: colors.surface,
        borderWidth: 1,
        borderColor: colors.border,
        paddingHorizontal: 12,
        paddingVertical: 12,
    },
    projectIcon: {
        width: 34,
        height: 34,
        borderRadius: 17,
        backgroundColor: colors.primarySoft,
        alignItems: "center",
        justifyContent: "center",
    },
    projectBody: {
        flex: 1,
        gap: 4,
    },
    projectTitle: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "800",
    },
    projectSummary: {
        color: colors.textMuted,
        fontSize: 12,
        lineHeight: 18,
    },
    input: {
        borderWidth: 1,
        borderColor: "rgba(148,163,184,0.24)",
        borderRadius: 18,
        backgroundColor: colors.surface,
        paddingHorizontal: 14,
        paddingVertical: 13,
        color: colors.text,
        fontSize: 15,
    },
    primaryButton: {
        marginTop: 10,
        minHeight: 48,
        borderRadius: 18,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: colors.primary,
    },
    primaryButtonText: {
        color: "#FFFFFF",
        fontWeight: "800",
        fontSize: 15,
    },
    refreshProfilesButton: {
        width: 32,
        height: 32,
        borderRadius: 16,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: colors.surface,
    },
    emptyProfilesText: {
        color: colors.textSoft,
        fontSize: 13,
        lineHeight: 19,
    },
    profileList: {
        gap: spacing.sm,
    },
    profileCard: {
        borderWidth: 1,
        borderColor: "rgba(148,163,184,0.20)",
        borderRadius: 20,
        padding: 12,
        backgroundColor: colors.surface,
        gap: spacing.sm,
    },
    profileCardActive: {
        borderColor: "rgba(124,58,237,0.34)",
        backgroundColor: "rgba(124,58,237,0.035)",
    },
    profileMain: {
        gap: 4,
    },
    profileTitleRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
    },
    profileTitle: {
        flex: 1,
        color: colors.text,
        fontSize: 14,
        fontWeight: "800",
    },
    currentBadge: {
        borderRadius: radii.pill,
        paddingHorizontal: 8,
        paddingVertical: 3,
        backgroundColor: "rgba(16,185,129,0.12)",
    },
    currentBadgeText: {
        color: colors.success,
        fontSize: 10,
        fontWeight: "800",
    },
    profileUrl: {
        color: colors.text,
        fontSize: 12,
    },
    profileMeta: {
        color: colors.textSoft,
        fontSize: 11,
    },
    profileActions: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
    },
    profileActionButton: {
        minHeight: 34,
        borderRadius: radii.pill,
        paddingHorizontal: 14,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(124,58,237,0.10)",
    },
    profileActionText: {
        color: colors.primaryDeep,
        fontSize: 12,
        fontWeight: "800",
    },
    profileDeleteButton: {
        width: 34,
        height: 34,
        borderRadius: 17,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(148,163,184,0.10)",
    },
    disabled: {
        opacity: 0.6,
    },
});
