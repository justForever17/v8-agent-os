import { router, type Href } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, FlatList, Modal, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useIsFocused } from "@react-navigation/native";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { PhoneTopbar } from "@/src/components/layout/PhoneTopbar";
import { PeerConversation } from "@/src/components/connections/PeerConversation";
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { useAppVisibility } from "@/src/hooks/use-app-visibility";
import { type AdminConnectionProfile, readAdminConnectionProfiles, writeAdminConnectionProfiles, forgetProfileCredentials } from "@/src/lib/admin-connection-profiles";
import { listSupervisorPeers, updateSupervisorPeer, revokeSupervisorPeer, type SupervisorPeer } from "@/src/lib/supervisor-peers";
import { readMetadata, writeMetadata } from "@/src/lib/mobile-storage";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

type DeviceRow = { kind: "profile"; value: AdminConnectionProfile } | { kind: "peer"; value: SupervisorPeer };

export default function ConnectScreen() {
    const { status, userAvatarUri, adminBaseUrl, authorityKey, servingInstanceId, activeProfileId, activateProfile, signOut, authorizedFetch } = useAppSession();
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
                const current = await readAdminConnectionProfiles();
                const own = current.find((item) => item.id === target.value.id);
                if (own) { own.label = nickname.trim(); await writeAdminConnectionProfiles(current); await loadProfiles(); }
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
                    await forgetProfileCredentials(target.value);
                    const current = await readAdminConnectionProfiles();
                    await writeAdminConnectionProfiles(current.filter((item) => item.id !== target.value.id));
                    await loadProfiles();
                }
                setSelected(null);
            }) },
        ]);
    };

    return <SafeAreaView style={[styles.root, { backgroundColor: colors.background }]} edges={["top", "left", "right"]}>
        <PhoneTopbar userImageUri={userAvatarUri || undefined} onBrandPress={() => void home()} actions={[
            { key: "chat", icon: "chat-processing-outline", onPress: () => void home() },
            { key: "settings", icon: "cog-outline", onPress: () => router.navigate("/settings" as Href) },
        ]} />
        <View style={styles.header}>
            <Text style={[styles.title, { color: colors.text }]}>{t("phone.devices.title")}</Text>
            <Text style={{ color: colors.textMuted }} numberOfLines={1}>{profiles.find((item) => item.id === activeProfileId)?.label || adminBaseUrl}</Text>
            <Pressable accessibilityRole="button" onPress={() => router.navigate("/login?add=1" as Href)} style={[styles.add, { borderColor: colors.border }]}>
                <MaterialCommunityIcons name="plus" size={18} color={colors.primary} /><Text style={{ color: colors.primary }}>{t("phone.devices.add")}</Text>
            </Pressable>
            <View style={[styles.tabs, { backgroundColor: colors.surfaceStrong }]}>
                {(["profile", "peer"] as const).map((kind) => <Pressable key={kind} accessibilityRole="tab" accessibilityState={{ selected: tab === kind }}
                    onPress={() => { setTab(kind); setQuery(""); setError(""); }} style={[styles.tab, tab === kind && { backgroundColor: colors.surface }]}>
                    <Text style={{ color: tab === kind ? colors.primary : colors.textMuted, fontWeight: "700" }}>{t(kind === "profile" ? "phone.devices.profiles" : "phone.devices.peers")}</Text>
                </Pressable>)}
            </View>
            <TextInput accessibilityLabel={t("phone.devices.search")} placeholder={t("phone.devices.search")} placeholderTextColor={colors.textSoft}
                value={query} onChangeText={setQuery} style={[styles.search, { color: colors.text, borderColor: colors.border }]} />
            {error ? <Pressable accessibilityRole="button" onPress={() => void (tab === "peer" ? refreshPeers() : loadProfiles()).catch((failure) => setError(failure.message))}>
                <Text style={{ color: colors.danger }}>{error} · {t("phone.devices.retry")}</Text>
            </Pressable> : null}
        </View>
        <FlatList data={rows} keyExtractor={(row) => row.kind === "profile" ? row.value.id : row.value.linkId} contentContainerStyle={styles.list}
            initialNumToRender={12} maxToRenderPerBatch={8} windowSize={7} keyboardShouldPersistTaps="handled"
            ListEmptyComponent={<Text style={[styles.empty, { color: colors.textMuted }]}>{t(tab === "profile" ? "phone.devices.emptyProfiles" : "phone.devices.emptyPeers")}</Text>}
            ListFooterComponent={tab === "peer" && (refreshing || nextCursor) ? <Pressable disabled={refreshing} onPress={() => void refreshPeers(undefined, nextCursor || undefined)} style={styles.more}>
                {refreshing ? <ActivityIndicator color={colors.primary} /> : <Text style={{ color: colors.primary }}>{t("phone.devices.more")}</Text>}
            </Pressable> : null}
            renderItem={({ item: row }) => {
                const id = row.kind === "profile" ? row.value.id : row.value.linkId;
                const active = row.kind === "profile" && id === activeProfileId;
                const label = row.kind === "profile" ? row.value.label : row.value.displayName;
                return <View style={[styles.row, { backgroundColor: colors.surface, borderColor: active ? colors.primary : colors.border }]}>
                    <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={() => open(row)} style={styles.rowMain}>
                        <MaterialCommunityIcons name={row.kind === "profile" ? "server-network" : "access-point-network"} size={22} color={colors.primary} />
                        <View style={styles.rowText}>
                            <Text style={{ color: colors.text, fontWeight: "700", fontSize: 15 }} numberOfLines={1}>{label}</Text>
                            <Text style={{ color: colors.textMuted, fontSize: 11 }} numberOfLines={1}>{row.kind === "profile"
                                ? `${active ? t("phone.devices.current") : row.value.credentialRef ? t("phone.devices.last") : t("phone.devices.repair")} · ${row.value.lastUsedAt ? new Date(row.value.lastUsedAt).toLocaleDateString() : ""}`
                                : `${t(row.value.online ? "phone.devices.online" : "phone.devices.offline")} · ${row.value.lastSeenAt ? new Date(row.value.lastSeenAt).toLocaleString() : t("phone.devices.known")}`}</Text>
                        </View>
                    </Pressable>
                    <Pressable accessibilityRole="button" disabled={busyKey === id} style={styles.action} onPress={() => row.kind === "peer" ? setConversation(row.value)
                        : void act(id, async () => { await activateProfile(row.value.id); router.dismissTo("/chat" as Href); })}>
                        {busyKey === id ? <ActivityIndicator color={colors.primary} /> : <Text style={{ color: colors.primary, fontWeight: "600" }}>{t(row.kind === "peer" ? "phone.devices.view" : active ? "phone.devices.current" : "phone.devices.switch")}</Text>}
                    </Pressable>
                </View>;
            }} />
        <Modal visible={Boolean(selected)} transparent animationType="fade" onRequestClose={() => setSelected(null)}>
            <View style={[styles.overlay, { backgroundColor: colors.overlay }]}>
                <View style={[styles.sheet, { backgroundColor: colors.surface }]}>
                    <Text style={[styles.title, { color: colors.text }]}>{t("phone.devices.details")}</Text>
                    <TextInput accessibilityLabel={t("phone.devices.rename")} value={nickname} onChangeText={setNickname} maxLength={80} style={[styles.search, { color: colors.text, borderColor: colors.border }]} />
                    {selected?.kind === "profile" ? <><Text style={{ color: colors.textMuted }}>{t("phone.devices.endpoint")}</Text>
                        <Text selectable style={{ color: colors.textMuted, fontSize: 12 }}>{(selected.value.adminUrls?.length ? selected.value.adminUrls : [selected.value.adminBaseUrl]).join("\n")}</Text></>
                        : <Text style={{ color: colors.textMuted }}>{selected?.value.description || t("phone.devices.local")}</Text>}
                    <View style={styles.sheetActions}>
                        <Pressable onPress={() => void saveName()} disabled={Boolean(busyKey)} style={styles.action}><Text style={{ color: colors.primary }}>{t("phone.devices.save")}</Text></Pressable>
                        <Pressable onPress={remove} disabled={Boolean(busyKey)} style={styles.action}><Text style={{ color: colors.danger }}>{t(selected?.kind === "peer" ? "phone.devices.revoke" : selected?.value.id === activeProfileId ? "phone.devices.logout" : "phone.devices.remove")}</Text></Pressable>
                        <Pressable onPress={() => setSelected(null)} style={styles.action}><Text style={{ color: colors.text }}>{t("phone.devices.close")}</Text></Pressable>
                    </View>
                </View>
            </View>
        </Modal>
        {conversation ? <PeerConversation peer={conversation} onClose={() => setConversation(null)} /> : null}
    </SafeAreaView>;
}

const styles = StyleSheet.create({
    root: { flex: 1 }, header: { paddingHorizontal: 16, gap: 10, paddingBottom: 12 }, title: { fontSize: 22, fontWeight: "700" },
    tabs: { flexDirection: "row", padding: 4, borderRadius: 12 }, tab: { flex: 1, minHeight: 42, alignItems: "center", justifyContent: "center", borderRadius: 9 },
    search: { minHeight: 44, borderWidth: 1, borderRadius: 12, paddingHorizontal: 12, fontSize: 14 },
    add: { flexDirection: "row", alignItems: "center", gap: 8, minHeight: 44, paddingHorizontal: 12, borderRadius: 12, borderWidth: 1 },
    list: { paddingHorizontal: 16, paddingBottom: 32, gap: 8 }, row: { flexDirection: "row", alignItems: "center", borderWidth: 1, borderRadius: 14, padding: 8, minHeight: 74 },
    rowMain: { flex: 1, flexDirection: "row", gap: 10, alignItems: "center", padding: 6 }, rowText: { flex: 1, gap: 5 }, action: { paddingHorizontal: 10, minHeight: 44, justifyContent: "center" },
    empty: { paddingVertical: 32, textAlign: "center" }, more: { padding: 16, alignItems: "center" }, overlay: { flex: 1, justifyContent: "center", padding: 20 },
    sheet: { padding: 18, borderRadius: 20, gap: 14 }, sheetActions: { flexDirection: "row", flexWrap: "wrap", justifyContent: "flex-end" },
});
