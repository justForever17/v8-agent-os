import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, FlatList, Modal, Pressable, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useIsFocused } from "@react-navigation/native";
import { useAppVisibility } from "@/src/hooks/use-app-visibility";
import { loadPeerTimeline, type PeerMessage, type SupervisorPeer } from "@/src/lib/supervisor-peers";
import { phoneSessionKey } from "@/src/lib/phone-identity";
import { readMetadata, writeMetadata } from "@/src/lib/mobile-storage";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export function PeerConversation({ peer, onClose }: { peer: SupervisorPeer; onClose: () => void }) {
    const { authorityKey, servingInstanceId, authorizedFetch } = useAppSession();
    const { colors, t } = useUiPrefs();
    const focused = useIsFocused();
    const visible = useAppVisibility();
    const key = phoneSessionKey(authorityKey, peer.servingInstanceId, peer.sessionId);
    const [messages, setMessages] = useState<PeerMessage[]>([]);
    const [before, setBefore] = useState<string | undefined>();
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const controllerRef = useRef<AbortController | null>(null);
    const load = useCallback(async (older?: string) => {
        controllerRef.current?.abort();
        const controller = new AbortController();
        controllerRef.current = controller;
        setBusy(true);
        try {
            if (peer.servingInstanceId !== servingInstanceId || peer.sessionKind !== "local_neighbor") throw new Error("Conversation location changed.");
            const page = await loadPeerTimeline(authorizedFetch, peer, controller.signal, older);
            if (controller.signal.aborted) return;
            if (page.peer.sessionId !== peer.sessionId || page.peer.servingInstanceId !== peer.servingInstanceId || page.peer.linkId !== peer.linkId) throw new Error("Conversation location changed.");
            setMessages((current) => older ? [...page.items.filter((item) => !current.some((old) => old.id === item.id)), ...current] : page.items);
            setBefore(page.previousCursor); setError("");
            if (!older) await writeMetadata(`v8.phone.peerTimeline.${key}`, JSON.stringify(page.items));
        } catch (failure) { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : t("phone.devices.failed")); }
        finally { if (!controller.signal.aborted) setBusy(false); }
    }, [authorizedFetch, key, peer, servingInstanceId, t]);
    useEffect(() => {
        if (!focused || !visible) { controllerRef.current?.abort(); return; }
        let cancelled = false;
        void readMetadata(`v8.phone.peerTimeline.${key}`).then((raw) => { if (!cancelled && raw) setMessages(JSON.parse(raw)); }).catch(() => undefined);
        void load();
        return () => { cancelled = true; controllerRef.current?.abort(); };
    }, [focused, visible, key, load]);
    return <Modal visible animationType="slide" onRequestClose={onClose}>
        <SafeAreaView style={{ flex: 1, backgroundColor: colors.background, padding: 16, gap: 12 }}>
            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                <Text style={{ color: colors.text, fontSize: 20, fontWeight: "700" }}>{peer.displayName}</Text>
                <Pressable accessibilityRole="button" onPress={onClose} style={{ minHeight: 44, justifyContent: "center", paddingHorizontal: 12 }}><Text style={{ color: colors.primary }}>{t("phone.devices.close")}</Text></Pressable>
            </View>
            <Text style={{ color: colors.textMuted, fontSize: 12 }}>{t("phone.devices.location", { instance: peer.servingInstanceId, peer: peer.displayName })}</Text>
            {error ? <Pressable onPress={() => void load()}><Text style={{ color: colors.danger }}>{error} · {t("phone.devices.retry")}</Text></Pressable> : null}
            <FlatList data={messages} keyExtractor={(item) => item.id || String(item.seq)} maintainVisibleContentPosition={{ minIndexForVisible: 0 }}
                initialNumToRender={12} maxToRenderPerBatch={8} windowSize={7} contentContainerStyle={{ gap: 8, paddingBottom: 20 }}
                ListHeaderComponent={busy ? <ActivityIndicator color={colors.primary} /> : before ? <Pressable style={{ padding: 12 }} onPress={() => void load(before)}><Text style={{ color: colors.primary }}>{t("phone.devices.older")}</Text></Pressable> : null}
                ListEmptyComponent={<Text style={{ color: colors.textMuted, padding: 20 }}>{t("phone.devices.emptyMessages")}</Text>}
                renderItem={({ item }) => <View style={{ padding: 14, borderRadius: 12, backgroundColor: colors.surface, gap: 6 }}>
                    <Text style={{ color: colors.textMuted, fontSize: 11 }}>{item.fromNickname} · {item.status}</Text>
                    <Text selectable style={{ color: colors.text, lineHeight: 22 }}>{item.body}</Text>
                </View>} />
        </SafeAreaView>
    </Modal>;
}
