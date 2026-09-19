import { router, type Href } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, FlatList, KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useIsFocused } from "@react-navigation/native";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { PhoneTopbar } from "@/src/components/layout/PhoneTopbar";
import { PeerConversation } from "@/src/components/connections/PeerConversation";
import { ConfigDistributionPanel } from "@/src/components/connections/ConfigDistributionPanel";
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { useAppVisibility } from "@/src/hooks/use-app-visibility";
import { type AdminConnectionProfile, readAdminConnectionProfiles, updateAdminConnectionProfiles } from "@/src/lib/admin-connection-profiles";
import { listSupervisorPeers, updateSupervisorPeer, revokeSupervisorPeer, type SupervisorPeer } from "@/src/lib/supervisor-peers";
import { readMetadata, writeMetadata } from "@/src/lib/mobile-storage";
import { deviceExecutor } from "@/src/lib/device-executor";
import { phoneAuthorityKey } from "@/src/lib/phone-identity";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

type DeviceRow = { kind: "profile"; value: AdminConnectionProfile } | { kind: "peer"; value: SupervisorPeer };

export default function ConnectScreen() {
    const { status, adminBaseUrl, authorityKey, servingInstanceId, activeProfileId, activateProfile, signOut, authorizedFetch } = useAppSession();
    const { t, colors } = useUiPrefs();
    const home = useGoHomeToChat();
    const focused = useIsFocused();
    const visible = useAppVisibility();
    const [profiles, setProfiles] = useState<AdminConnectionProfile[]>([]);
    const [peers, setPeers] = useState<SupervisorPeer[]>([]);
    const [nextCursor, setNextCursor] = useState<string | null>(null);
    const [query, setQuery] = useState("");
    const [tab, setTab] = useState<"profile" | "peer">("profile");
    const [busyKey, setBusyKey] = useState("");
    const [error, setError] = useState("");
    const [refreshing, setRefreshing] = useState(false);
    const [selected, setSelected] = useState<DeviceRow | null>(null);
    const [nickname, setNickname] = useState("");
    const [conversation, setConversation] = useState<SupervisorPeer | null>(null);
    const [distributionOpen, setDistributionOpen] = useState(false);

    const loadProfiles = useCallback(async () => setProfiles(await readAdminConnectionProfiles()), []);
    useEffect(() => {
        if (focused) void loadProfiles().catch((failure) => setError(failure.message));
    }, [focused, activeProfileId, loadProfiles]);
    const refreshPeers = useCallback(async (signal?: AbortSignal, cursor?: string) => {
        if (status !== "authenticated") return;
        setRefreshing(true);
        try {
            const payload = await listSupervisorPeers(authorizedFetch, { signal, cursor, query });
            if (signal?.aborted) return;
            if (payload.servingInstanceId !== servingInstanceId) throw new Error("Connection identity changed. Pair again.");
            setPeers((previous) => cursor ? [...previous, ...payload.items.filter((item) => !previous.some((old) => old.linkId === item.linkId))] : payload.items);
            setNextCursor(payload.nextCursor);
            setError("");
            if (!cursor && !query) await writeMetadata(`v8.phone.peers.${authorityKey}`, JSON.stringify(payload.items));
        } catch (failure) {
            if (!signal?.aborted) setError(failure instanceof Error ? failure.message : t("phone.devices.failed"));
        } finally { if (!signal?.aborted) setRefreshing(false); }
    }, [authorizedFetch, authorityKey, query, servingInstanceId, status, t]);
    useEffect(() => {
        if (!focused || !visible || tab !== "peer" || !authorityKey) return;
        const controller = new AbortController();
        void readMetadata(`v8.phone.peers.${authorityKey}`).then((raw) => {
            if (raw && !controller.signal.aborted) setPeers(JSON.parse(raw));
        }).catch(() => undefined);
        const timer = setTimeout(() => void refreshPeers(controller.signal), query ? 200 : 0);
        return () => { clearTimeout(timer); controller.abort(); };
    }, [authorityKey, focused, visible, tab, refreshPeers]);

    const rows = useMemo<DeviceRow[]>(() => tab === "profile"
        ? profiles.filter((item) => `${item.label} ${item.instanceId}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map((value) => ({ kind: "profile", value }))
        : peers.filter((item) => `${item.displayName} ${item.peerId}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map((value) => ({ kind: "peer", value })), [profiles, peers, query, tab]);
    const act = async (key: string, operation: () => Promise<unknown>) => {
        if (busyKey) return;
        setBusyKey(key); setError("");
        try { await operation(); } catch (failure) { setError(failure instanceof Error ? failure.message : t("phone.devices.failed")); }
        finally { setBusyKey(""); }
    };
    const open = (row: DeviceRow) => {
        setSelected(row); setNickname(row.kind === "profile" ? row.value.label : row.value.displayName);
    };
    const saveName = async () => {
        if (!selected || !nickname.trim()) return;
        const target = selected;
        await act("rename", async () => {
            if (target.kind === "profile") {
                await updateAdminConnectionProfiles((current) => current.map((item) => item.id === target.value.id ? { ...item, label: nickname.trim() } : item));
                await loadProfiles();
            } else { await updateSupervisorPeer(authorizedFetch, target.value, nickname.trim()); await refreshPeers(); }
            setSelected(null);
        });
    };
    const remove = () => {
        if (!selected) return;
        const target = selected;
        const isActive = target.kind === "profile" && target.value.id === activeProfileId;
        const label = target.kind === "peer" ? t("phone.devices.revoke") : isActive ? t("phone.devices.logout") : t("phone.devices.remove");
        Alert.alert(label, target.kind === "peer" ? t("phone.devices.revokeConfirm") : undefined, [
            { text: t("phone.devices.cancel"), style: "cancel" },
            { text: label, style: "destructive", onPress: () => void act("remove", async () => {
                if (target.kind === "peer") { await revokeSupervisorPeer(authorizedFetch, target.value); await refreshPeers(); }
                else if (isActive) await signOut();
                else {
                    const instanceId = target.value.instanceId || target.value.serverId;
                    const principalId = target.value.user?.id || target.value.principalId;
                    if (instanceId && principalId) {
                        await deviceExecutor.forgetProfile(phoneAuthorityKey({ instanceId, principalId, profileId: target.value.id }));
                    }
                    await updateAdminConnectionProfiles((current) => current.filter((item) => item.id !== target.value.id));
                    await loadProfiles();
                }
                setSelected(null);
            }) },
        ]);
    };

    return <SafeAreaView style={[styles.root, { backgroundColor: colors.background }]}>
        <PhoneTopbar onBrandPress={() => void home()} actions={[
        ]} />
        <FlatList data={rows} keyExtractor={(row) => row.kind === "profile" ? row.value.id : row.value.linkId} contentContainerStyle={styles.list}
            initialNumToRender={12} maxToRenderPerBatch={8} windowSize={7} keyboardShouldPersistTaps="handled" contentInsetAdjustmentBehavior="automatic"
            ListHeaderComponent={<View style={styles.header}>
            <Text style={[styles.title, { color: colors.text }]}>{t("phone.devices.title")}</Text>
            {profiles.find((item) => item.id === activeProfileId)?.label || adminBaseUrl ? <View style={styles.currentConnection}>
                <View style={[styles.statusDot, { backgroundColor: colors.primary }]} />
                <Text style={[styles.subtitle, { color: colors.textMuted }]} numberOfLines={2}>{profiles.find((item) => item.id === activeProfileId)?.label || adminBaseUrl}</Text>
            </View> : null}
            <Pressable accessibilityRole="button" onPress={() => router.navigate("/login?add=1" as Href)} style={({ pressed }) => [styles.add, { backgroundColor: colors.userBubbleBottom, opacity: pressed ? 0.8 : 1 }]}>
                <MaterialCommunityIcons name="plus" size={20} color="#FFFFFF" /><Text style={styles.addText}>{t("phone.devices.add")}</Text>
            </Pressable>
            {status === "authenticated" ? <Pressable accessibilityRole="button" onPress={() => setDistributionOpen(true)} style={({ pressed }) => [styles.distribution, { backgroundColor: pressed ? colors.primarySoft : colors.surface, borderColor: colors.border }]}>
                <View style={[styles.smallIcon, { backgroundColor: colors.primarySoft }]}><MaterialCommunityIcons name="share-variant-outline" size={18} color={colors.primaryDeep} /></View>
                <Text style={[styles.distributionText, { color: colors.text }]}>{t("phone.configDistribution.title")}</Text>
                <MaterialCommunityIcons name="chevron-right" size={20} color={colors.textSoft} />
            </Pressable> : null}
            <View style={[styles.tabs, { backgroundColor: colors.primarySoft }]}>
                {(["profile", "peer"] as const).map((kind) => <Pressable key={kind} accessibilityRole="tab" accessibilityState={{ selected: tab === kind }}
                    onPress={() => { setTab(kind); setQuery(""); setError(""); }} style={[styles.tab, tab === kind && { backgroundColor: colors.surface }]}>
                    <Text style={[styles.tabText, { color: tab === kind ? colors.primaryDeep : colors.textMuted }]}>{t(kind === "profile" ? "phone.devices.profiles" : "phone.devices.peers")}</Text>
                </Pressable>)}
            </View>
            <View style={[styles.searchBox, { backgroundColor: colors.surfaceMuted, borderColor: colors.border }]}>
            <MaterialCommunityIcons name="magnify" size={21} color={colors.textSoft} />
            <TextInput accessibilityLabel={t("phone.devices.search")} placeholder={t("phone.devices.search")} placeholderTextColor={colors.textSoft}
                value={query} onChangeText={setQuery} style={[styles.searchInput, { color: colors.text }]} />
            </View>
            {error ? <Pressable accessibilityRole="button" style={[styles.error, { borderColor: colors.danger }]} onPress={() => void (tab === "peer" ? refreshPeers() : loadProfiles()).catch((failure) => setError(failure.message))}>
                <MaterialCommunityIcons name="alert-circle-outline" size={20} color={colors.danger} />
                <Text style={[styles.errorText, { color: colors.danger }]}>{error} · {t("phone.devices.retry")}</Text>
            </Pressable> : null}
        </View>}
            ListEmptyComponent={<View style={[styles.empty, { backgroundColor: colors.surfaceMuted, borderColor: colors.border }]}>
                <View style={[styles.emptyIcon, { backgroundColor: colors.primarySoft }]}><MaterialCommunityIcons name={tab === "profile" ? "server-network" : "access-point-network"} size={28} color={colors.primaryDeep} /></View>
                <Text style={[styles.emptyText, { color: colors.textMuted }]}>{t(tab === "profile" ? "phone.devices.emptyProfiles" : "phone.devices.emptyPeers")}</Text>
            </View>}
            ListFooterComponent={tab === "peer" && (refreshing || nextCursor) ? <Pressable disabled={refreshing} onPress={() => void refreshPeers(undefined, nextCursor || undefined)} style={styles.more}>
                {refreshing ? <ActivityIndicator color={colors.primary} /> : <Text style={{ color: colors.primary }}>{t("phone.devices.more")}</Text>}
            </Pressable> : null}
            renderItem={({ item: row }) => {
                const id = row.kind === "profile" ? row.value.id : row.value.linkId;
                const active = row.kind === "profile" && id === activeProfileId;
                const label = row.kind === "profile" ? row.value.label : row.value.displayName;
                return <View style={[styles.row, { backgroundColor: colors.surface, borderColor: active ? colors.primary : colors.border }]}>
                    <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={() => open(row)} style={styles.rowMain}>
                        <View style={[styles.deviceIcon, { backgroundColor: active ? colors.primarySoft : colors.backgroundDeep }]}>
                            <MaterialCommunityIcons name={row.kind === "profile" ? "server-network" : "access-point-network"} size={22} color={active ? colors.primaryDeep : colors.textMuted} />
                        </View>
                        <View style={styles.rowText}>
                            <Text style={[styles.deviceName, { color: colors.text }]} numberOfLines={2}>{label}</Text>
                            <Text style={[styles.deviceMeta, { color: colors.textMuted }]} numberOfLines={2}>{row.kind === "profile"
                                ? `${active ? t("phone.devices.current") : row.value.credentialRef ? t("phone.devices.last") : t("phone.devices.repair")} · ${row.value.lastUsedAt ? new Date(row.value.lastUsedAt).toLocaleDateString() : ""}`
                                : `${t(row.value.online ? "phone.devices.online" : "phone.devices.offline")} · ${row.value.lastSeenAt ? new Date(row.value.lastSeenAt).toLocaleString() : t("phone.devices.known")}`}</Text>
                        </View>
                    </Pressable>
                    <Pressable accessibilityRole="button" disabled={busyKey === id} style={({ pressed }) => [styles.rowAction, { backgroundColor: active || pressed ? colors.primarySoft : colors.backgroundDeep }]} onPress={() => row.kind === "peer" ? setConversation(row.value)
                        : void act(id, async () => { await activateProfile(row.value.id); router.dismissTo("/chat" as Href); })}>
                        {busyKey === id ? <ActivityIndicator color={colors.primary} /> : <Text style={[styles.actionText, { color: colors.primaryDeep }]}>{t(row.kind === "peer" ? "phone.devices.view" : active ? "phone.devices.current" : "phone.devices.switch")}</Text>}
                    </Pressable>
                </View>;
            }} />
        <Modal visible={Boolean(selected)} transparent animationType="fade" onRequestClose={() => setSelected(null)}>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={[styles.overlay, { backgroundColor: colors.overlay }]}>
                <ScrollView keyboardShouldPersistTaps="handled" style={[styles.sheet, { backgroundColor: colors.surface }]} contentContainerStyle={styles.sheetContent}>
                    <View style={styles.sheetHeading}>
                        <View style={[styles.deviceIcon, { backgroundColor: colors.primarySoft }]}><MaterialCommunityIcons name={selected?.kind === "peer" ? "access-point-network" : "server-network"} size={23} color={colors.primaryDeep} /></View>
                        <Text style={[styles.sheetTitle, { color: colors.text }]}>{t("phone.devices.details")}</Text>
                    </View>
                    <Text style={[styles.fieldLabel, { color: colors.textMuted }]}>{t("phone.devices.rename")}</Text>
                    <TextInput accessibilityLabel={t("phone.devices.rename")} value={nickname} onChangeText={setNickname} maxLength={80} style={[styles.search, { color: colors.text, borderColor: colors.border }]} />
                    {selected?.kind === "profile" ? <View style={[styles.endpoint, { backgroundColor: colors.backgroundDeep }]}><Text style={[styles.fieldLabel, { color: colors.textMuted }]}>{t("phone.devices.endpoint")}</Text>
                        <Text selectable style={[styles.endpointText, { color: colors.text }]}>{(selected.value.adminUrls?.length ? selected.value.adminUrls : [selected.value.adminBaseUrl]).join("\n")}</Text></View>
                        : <Text style={[styles.endpointText, { color: colors.textMuted }]}>{selected?.value.description || t("phone.devices.local")}</Text>}
                    <View style={styles.sheetActions}>
                        <Pressable accessibilityRole="button" onPress={() => void saveName()} disabled={Boolean(busyKey)} style={[styles.sheetButton, { backgroundColor: colors.userBubbleBottom }, Boolean(busyKey) && styles.disabled]}><Text style={[styles.actionText, { color: "#FFFFFF" }]}>{t("phone.devices.save")}</Text></Pressable>
                        <Pressable accessibilityRole="button" onPress={remove} disabled={Boolean(busyKey)} style={[styles.sheetButton, { borderColor: colors.danger }, Boolean(busyKey) && styles.disabled]}><Text style={[styles.actionText, { color: colors.danger }]}>{t(selected?.kind === "peer" ? "phone.devices.revoke" : selected?.value.id === activeProfileId ? "phone.devices.logout" : "phone.devices.remove")}</Text></Pressable>
                        <Pressable accessibilityRole="button" onPress={() => setSelected(null)} style={styles.sheetButton}><Text style={[styles.actionText, { color: colors.textMuted }]}>{t("phone.devices.close")}</Text></Pressable>
                    </View>
                </ScrollView>
            </KeyboardAvoidingView>
        </Modal>
        {conversation ? <PeerConversation peer={conversation} onClose={() => setConversation(null)} /> : null}
        {distributionOpen ? <ConfigDistributionPanel onClose={() => setDistributionOpen(false)} onChooseDevice={() => { setDistributionOpen(false); setTab("profile"); setQuery(""); }} /> : null}
    </SafeAreaView>;
}

const styles = StyleSheet.create({
    root: { flex: 1 }, header: { gap: 14, paddingTop: 12, paddingBottom: 12 }, title: { fontSize: 28, lineHeight: 36, fontWeight: "800" },
    currentConnection: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: -6 }, statusDot: { width: 6, height: 6, borderRadius: 3 }, subtitle: { flex: 1, fontSize: 13, lineHeight: 20 },
    tabs: { flexDirection: "row", padding: 4, borderRadius: 16, gap: 4 }, tab: { flex: 1, minHeight: 46, paddingHorizontal: 8, paddingVertical: 10, alignItems: "center", justifyContent: "center", borderRadius: 12 }, tabText: { fontSize: 13, lineHeight: 18, fontWeight: "700", textAlign: "center" },
    searchBox: { flexDirection: "row", alignItems: "center", paddingHorizontal: 14, borderWidth: 1, borderRadius: 16, gap: 8 }, searchInput: { flex: 1, minWidth: 0, minHeight: 48, fontSize: 14 },
    search: { minHeight: 48, borderWidth: 1, borderRadius: 14, paddingHorizontal: 14, fontSize: 16 },
    add: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, minHeight: 50, paddingHorizontal: 16, paddingVertical: 12, borderRadius: 16 }, addText: { flexShrink: 1, color: "#FFFFFF", fontSize: 14, lineHeight: 20, fontWeight: "700", textAlign: "center" },
    distribution: { flexDirection: "row", alignItems: "center", gap: 12, minHeight: 60, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 16, borderWidth: 1 }, distributionText: { flex: 1, fontSize: 14, lineHeight: 20, fontWeight: "600" }, smallIcon: { width: 34, height: 34, borderRadius: 11, alignItems: "center", justifyContent: "center" },
    list: { paddingHorizontal: 20, paddingBottom: 24, gap: 10, maxWidth: 600, width: "100%", alignSelf: "center" }, row: { flexDirection: "row", alignItems: "center", borderWidth: 1, borderRadius: 20, padding: 10, minHeight: 94, gap: 6 },
    rowMain: { flex: 1, minWidth: 0, flexDirection: "row", gap: 12, alignItems: "center", padding: 4, minHeight: 64 }, rowText: { flex: 1, minWidth: 0, gap: 6 }, deviceIcon: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center" }, deviceName: { fontWeight: "700", fontSize: 15, lineHeight: 21 }, deviceMeta: { fontSize: 11, lineHeight: 17 },
    rowAction: { paddingHorizontal: 12, paddingVertical: 10, minHeight: 44, maxWidth: "32%", justifyContent: "center", alignItems: "center", borderRadius: 12 }, actionText: { fontSize: 13, lineHeight: 19, fontWeight: "700", textAlign: "center" },
    empty: { padding: 28, alignItems: "center", gap: 16, borderWidth: 1, borderRadius: 20 }, emptyIcon: { width: 60, height: 60, borderRadius: 20, alignItems: "center", justifyContent: "center" }, emptyText: { fontSize: 14, lineHeight: 22, textAlign: "center" },
    more: { padding: 16, minHeight: 48, alignItems: "center" }, error: { flexDirection: "row", alignItems: "center", gap: 10, padding: 12, borderWidth: 1, borderRadius: 14 }, errorText: { flex: 1, fontSize: 13, lineHeight: 20 },
    overlay: { flex: 1, justifyContent: "center", padding: 20 }, sheet: { maxHeight: "90%", flexGrow: 0, borderRadius: 24, width: "100%", maxWidth: 480, alignSelf: "center" }, sheetContent: { padding: 20, gap: 16 }, sheetHeading: { flexDirection: "row", alignItems: "center", gap: 12 }, sheetTitle: { flex: 1, fontSize: 20, lineHeight: 28, fontWeight: "700" }, fieldLabel: { fontSize: 12, lineHeight: 18, fontWeight: "600" }, endpoint: { padding: 14, borderRadius: 14, gap: 8 }, endpointText: { fontSize: 13, lineHeight: 21 },
    sheetActions: { gap: 8, paddingTop: 4 }, sheetButton: { minHeight: 48, padding: 12, alignItems: "center", justifyContent: "center", borderRadius: 14, borderWidth: 1, borderColor: "transparent" }, disabled: { opacity: 0.5 },
});
