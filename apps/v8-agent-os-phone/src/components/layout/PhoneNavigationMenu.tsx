import { useLayoutEffect, useRef, useState } from "react";
import { ActivityIndicator, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useIsFocused } from "@react-navigation/native";
import { router, type Href } from "expo-router";
import { Menu, X } from "lucide-react-native";

import { ProfileMenuOverlay } from "@/src/components/chat/ProfileMenuOverlay";
import { PhoneRpaOverlay } from "@/src/components/rpa/PhoneRpaOverlay";
import { useAppVisibility } from "@/src/hooks/use-app-visibility";
import { phoneDrafts } from "@/src/lib/phone-drafts";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

// An open surface and its pending navigation belong to one paired identity.
export function PhoneNavigationMenu() {
    const { authorityKey, servingInstanceId, status } = useAppSession();
    return <NavigationMenu key={JSON.stringify([authorityKey, servingInstanceId, status])} />;
}

function NavigationMenu() {
    const { colors, t } = useUiPrefs();
    const focused = useIsFocused();
    const appVisible = useAppVisibility();
    const [surface, setSurface] = useState<"menu" | "rpa" | "profile" | null>(null);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState("");
    const request = useRef<symbol | null>(null);
    const trigger = useRef<View>(null);
    const close = () => {
        request.current = null;
        setPending(false);
        setError("");
        setSurface(null);
        // The submenu's previous DOM focus target is removed with the menu.
        if (Platform.OS === "web" && focused && appVisible) {
            requestAnimationFrame(() => trigger.current?.focus());
        }
    };

    useLayoutEffect(() => {
        if (!focused || !appVisible) close();
        return () => { request.current = null; };
    }, [focused, appVisible]);

    const select = async (destination: string) => {
        if (request.current || !focused || !appVisible) return;
        const ticket = Symbol();
        request.current = ticket;
        setPending(true);
        setError("");
        try {
            await phoneDrafts.flushAll();
            if (request.current !== ticket) return;
            if (destination === "rpa" || destination === "profile") {
                setSurface(destination);
            } else {
                if (destination === "/chat") router.dismissTo(destination as Href);
                else router.navigate(destination as Href);
                setSurface(null);
            }
        } catch (cause) {
            if (request.current === ticket) {
                setError(cause instanceof Error ? cause.message : t("phone.devices.failed"));
            }
        } finally {
            if (request.current === ticket) {
                request.current = null;
                setPending(false);
            }
        }
    };

    const items = [
        ["/chat", t("phone.devices.returnChat")],
        ["/sessions", t("phone.navigation.sessions")],
        ["/connect", t("phone.devices.title")],
        ["rpa", t("src.screens.rpascreen.title")],
        ["profile", t("src.components.chat.profilemenuoverlay.profile_center")],
        ["/settings", t("phone.devices.settings")],
        ["/approvals", t("src.components.chat.chatwindow.approvals")],
        ["/specs", t("phone.navigation.specs")],
        ["/artifacts", t("src.screens.artifactsscreen.artifacts")],
    ];
    const closeLabel = t("phone.navigation.close");

    return <>
        <Pressable
            ref={trigger}
            accessibilityRole="button"
            accessibilityLabel={t("phone.devices.navigation")}
            accessibilityState={{ expanded: surface === "menu" }}
            onPress={() => { setError(""); setSurface("menu"); }}
            style={({ pressed }) => [styles.iconButton, { backgroundColor: pressed ? colors.surfaceMuted : "transparent", opacity: pressed ? 0.72 : 1 }]}
        >
            <Menu size={22} color={colors.textMuted} />
        </Pressable>
        <Modal visible={surface === "menu"} transparent animationType="fade" onRequestClose={close}>
            <View style={[styles.fill, { backgroundColor: colors.overlay }]}>
                {/* Siblings, so a menu press cannot bubble into the dismiss target. */}
                <Pressable accessibilityRole="button" accessibilityLabel={closeLabel} onPress={close} style={StyleSheet.absoluteFill} />
                <SafeAreaView style={styles.safeArea} pointerEvents="box-none" edges={["top", "right", "bottom", "left"]}>
                    <View accessibilityViewIsModal style={[styles.panel, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                        <View style={styles.heading}>
                            <Text accessibilityRole="header" style={[styles.title, { color: colors.text }]}>{t("phone.devices.navigation")}</Text>
                            <Pressable accessibilityRole="button" accessibilityLabel={closeLabel} onPress={close}
                                style={({ pressed }) => [styles.iconButton, { backgroundColor: pressed ? colors.surfaceMuted : "transparent" }]}>
                                <X size={22} color={colors.textMuted} />
                            </Pressable>
                        </View>
                        <ScrollView style={styles.list} contentContainerStyle={styles.listContent} keyboardShouldPersistTaps="handled">
                            {items.map(([destination, label]) => <Pressable key={destination}
                                accessibilityRole="button" accessibilityState={{ disabled: pending }} disabled={pending}
                                onPress={() => void select(destination)}
                                style={({ pressed }) => [styles.item, { backgroundColor: pressed ? colors.surfaceMuted : "transparent", opacity: pending ? 0.5 : 1 }]}>
                                <Text style={[styles.itemLabel, { color: colors.text }]}>{label}</Text>
                            </Pressable>)}
                        </ScrollView>
                        {pending ? <View accessibilityLiveRegion="polite" style={styles.feedback}>
                            <ActivityIndicator color={colors.primary} />
                            <Text style={[styles.feedbackText, { color: colors.textMuted }]}>{t("phone.navigation.savingDrafts")}</Text>
                        </View> : null}
                        {error ? <Text accessibilityRole="alert" style={[styles.error, { color: colors.danger }]}>{error}</Text> : null}
                    </View>
                </SafeAreaView>
            </View>
        </Modal>
        {surface === "rpa" ? <PhoneRpaOverlay visible onClose={close} /> : null}
        {surface === "profile" ? <Modal visible transparent animationType="fade" onRequestClose={close}>
            <SafeAreaView style={styles.fill} edges={["top", "right", "bottom", "left"]}>
                <View style={styles.fill}><ProfileMenuOverlay visible onClose={close} /></View>
            </SafeAreaView>
        </Modal> : null}
    </>;
}

const styles = StyleSheet.create({
    fill: { flex: 1 },
    safeArea: { flex: 1, alignItems: "center", justifyContent: "center", padding: 12 },
    panel: { width: "100%", maxWidth: 440, maxHeight: "100%", flexShrink: 1, borderRadius: 18, borderWidth: StyleSheet.hairlineWidth, overflow: "hidden" },
    heading: { flexDirection: "row", alignItems: "center", padding: 8, paddingLeft: 20 },
    title: { flex: 1, minWidth: 0, fontSize: 18, fontWeight: "700" },
    iconButton: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center" },
    list: { flexShrink: 1 },
    listContent: { paddingHorizontal: 8, paddingBottom: 8 },
    item: { minHeight: 48, justifyContent: "center", paddingHorizontal: 12, paddingVertical: 12, borderRadius: 10 },
    itemLabel: { fontSize: 16, fontWeight: "600" },
    feedback: { flexDirection: "row", gap: 10, padding: 12, alignItems: "center" },
    feedbackText: { flex: 1, fontSize: 14 },
    error: { padding: 12, fontSize: 14 },
});
