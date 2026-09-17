import { useEffect, useState } from "react";
import { ActivityIndicator, AppState, PermissionsAndroid, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { router } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { deviceExecutor, enrollExecutor, updateExecutorGrants, type ExecutorState } from "@/src/lib/device-executor";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export default function DeviceExecutorScreen() {
    const { t, colors } = useUiPrefs();
    const { authorizedFetch, authorityKey, status: chatStatus } = useAppSession();
    const [state, setState] = useState<ExecutorState | null>(null);
    const [baseUrl, setBaseUrl] = useState("");
    const [name, setName] = useState("Android");
    const [appText, setAppText] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const styles = makeStyles(colors);
    useEffect(() => {
        let alive = true;
        void deviceExecutor.getState().then(next => {
            if (!alive) return;
            setState(next); setBaseUrl(next.baseUrl || ""); setName(next.name || "Android"); setAppText(next.allowedApps.join("\n"));
        }).catch(() => { if (alive) setError("native_build_required"); });
        const subscription = deviceExecutor.subscribe(next => { if (alive) setState(next); });
        const foreground = AppState.addEventListener("change", next => {
            if (next === "active") void deviceExecutor.getState().then(value => { if (alive) setState(value); });
        });
        return () => { alive = false; subscription.remove(); foreground.remove(); };
    }, []);
    const run = async (action: () => Promise<ExecutorState | void>) => {
        setBusy(true); setError("");
        try { const result = await action(); if (result) setState(result); }
        catch (cause) { setError(cause instanceof Error ? cause.message : "native_action_error"); }
        finally { setBusy(false); }
    };
    const apps = () => appText.split(/[\s,]+/).filter(Boolean);
    const button = (label: string, action: () => void, disabled = false, danger = false) => (
        <Pressable accessibilityRole="button" disabled={disabled} onPress={action} style={({ pressed }) => [styles.button,
            (label === "enroll" || label === "enable") && styles.primaryButton, label === "revoke" && styles.revokeButton,
            danger && styles.stop, disabled && styles.disabled, pressed && styles.pressed]}>
            {danger ? <MaterialCommunityIcons name="stop-circle-outline" size={22} color="#FFFFFF" /> : null}
            <Text style={[styles.buttonText, (danger || label === "enroll" || label === "enable") && styles.onPrimaryText, label === "revoke" && styles.revokeText]}>{t(`executor.${label}`)}</Text>
        </Pressable>
    );
    return <SafeAreaView style={styles.page}>
        <View style={styles.header}>
            <Pressable accessibilityRole="button" accessibilityLabel={t("executor.back")} onPress={() => router.back()} style={({ pressed }) => [styles.backButton, pressed && styles.pressed]}>
                <MaterialCommunityIcons name="arrow-left" size={23} color={colors.text} />
            </Pressable>
            <Text style={styles.pageTitle}>{t("executor.title")}</Text>
            <View style={styles.headerIcon}><MaterialCommunityIcons name="cellphone-cog" size={23} color={colors.primaryDeep} /></View>
        </View>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" contentInsetAdjustmentBehavior="automatic">
            <Text style={styles.body}>{t("executor.description")}</Text>
            <View style={[styles.card, styles.statusCard]}>
                <View style={styles.statusHeading}>
                    <View style={styles.statusIcon}><MaterialCommunityIcons name={state?.enabled ? "access-point" : "power-standby"} size={26} color={colors.primaryDeep} /></View>
                    <Text style={styles.statusTitle}>{t(`executor.state.${state?.status || "loading"}`)}</Text>
                </View>
                <Text selectable style={styles.body}>{state?.baseUrl || t("executor.unbound")}</Text>
                <View style={styles.note}><MaterialCommunityIcons name="information-outline" size={18} color={colors.textMuted} /><Text style={styles.noteText}>{t("executor.profile_independent")}</Text></View>
                {state?.enabled ? button("disable", () => void run(() => deviceExecutor.disable()), busy) : null}
            </View>
            {state?.supported ? <>
                <View style={styles.card}>
                    <View style={styles.sectionHeading}><MaterialCommunityIcons name="shield-check-outline" size={21} color={colors.primaryDeep} /><Text style={styles.title}>{t("executor.permissions")}</Text></View>
                    <View style={styles.permissionRow}><MaterialCommunityIcons name={state.accessibilityGranted ? "check-circle-outline" : "circle-outline"} size={19} color={state.accessibilityGranted ? colors.success : colors.textSoft} /><Text style={styles.permissionText}>{t(state.accessibilityGranted ? "executor.accessibility_ready" : "executor.accessibility_needed")}</Text></View>
                    {button("open_permissions", () => void run(() => deviceExecutor.openAccessibilitySettings()), busy)}
                    <View style={styles.divider} />
                    <View style={styles.permissionRow}><MaterialCommunityIcons name={state.notificationGranted ? "check-circle-outline" : "circle-outline"} size={19} color={state.notificationGranted ? colors.success : colors.textSoft} /><Text style={styles.permissionText}>{t(state.notificationGranted ? "executor.notification_ready" : "executor.notification_needed")}</Text></View>
                    {button("allow_notifications", () => void run(async () => {
                        if (Platform.OS === "android" && Number(Platform.Version) >= 33) await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
                        return deviceExecutor.getState();
                    }), busy)}
                </View>
                <View style={styles.card}>
                    <View style={styles.sectionHeading}><MaterialCommunityIcons name="server-security" size={21} color={colors.primaryDeep} /><Text style={styles.title}>{t("executor.binding")}</Text></View>
                    {state.deviceId ? <Text selectable style={styles.body}>{state.name} · {state.authorityId}</Text> : <>
                        <Text style={styles.label}>{t("executor.origin")}</Text>
                        <TextInput accessibilityLabel={t("executor.origin")} autoCapitalize="none" value={baseUrl} onChangeText={setBaseUrl} placeholder="https://engine.example.com" placeholderTextColor={colors.textMuted} style={styles.input} />
                        <Text style={styles.label}>{t("executor.name")}</Text>
                        <TextInput accessibilityLabel={t("executor.name")} value={name} onChangeText={setName} style={styles.input} />
                    </>}
                    <Text style={styles.label}>{t("executor.allowed_apps")}</Text>
                    <Text style={styles.body}>{t("executor.scope_help")}</Text>
                    <View style={styles.capabilities}>
                        <View style={styles.permissionRow}><MaterialCommunityIcons name="image-outline" size={18} color={colors.textMuted} /><Text style={styles.noteText}>{t(state.windowCaptureAvailable ? "executor.window_capture_ready" : "executor.window_capture_unavailable")}</Text></View>
                        <View style={styles.permissionRow}><MaterialCommunityIcons name="gesture-tap" size={18} color={colors.textMuted} /><Text style={styles.noteText}>{t(state.gestureAvailable ? "executor.gesture_ready" : "executor.gesture_unavailable")}</Text></View>
                    </View>
                    {state.deviceId ? <>
                        <View style={styles.switchRow}><Text style={[styles.label, styles.switchLabel]}>{t("executor.full_display_capture")}</Text>
                        <Switch accessibilityLabel={t("executor.full_display_capture")} value={state.fullDisplayCapture} disabled={busy}
                            trackColor={{ false: colors.border, true: colors.primary }}
                            onValueChange={enabled => void run(() => deviceExecutor.setFullDisplayCapture(enabled))} /></View>
                        <Text style={styles.body}>{t("executor.full_display_capture_help")}</Text>
                    </> : null}
                    <TextInput accessibilityLabel={t("executor.allowed_apps")} value={appText} onChangeText={setAppText} multiline autoCapitalize="none" autoCorrect={false} placeholder="com.example.fixture" placeholderTextColor={colors.textMuted} style={[styles.input, styles.appsInput]} />
                    {!state.deviceId ? button("enroll", () => void run(async () => {
                        const enrolled = await enrollExecutor(authorizedFetch, { baseUrl, name, authorityKey, allowedApps: apps() });
                        setState(enrolled);
                        return updateExecutorGrants(authorizedFetch, enrolled, authorityKey, apps());
                    }), busy || chatStatus !== "authenticated") : <>
                        {button("save_scope", () => void run(() => updateExecutorGrants(authorizedFetch, state, authorityKey, apps())), busy || chatStatus !== "authenticated" || state.profileAuthorityKey !== authorityKey)}
                        {button("enable", () => void run(() => deviceExecutor.enable()), busy || state.enabled)}
                        {button("revoke", () => void run(() => deviceExecutor.revoke()), busy)}
                    </>}
                    {chatStatus !== "authenticated" ? <Text style={styles.body}>{t("executor.login_for_enrollment")}</Text> : null}
                </View>
            </> : <Text style={styles.body}>{t("executor.unsupported")}</Text>}
            {busy ? <ActivityIndicator color={colors.primary} /> : null}
            {error ? <View style={styles.errorBox}><MaterialCommunityIcons name="alert-circle-outline" size={20} color={colors.danger} /><Text selectable style={styles.error}>{t(`executor.error.${error}`, {}) === `executor.error.${error}` ? t("executor.operation_failed") : t(`executor.error.${error}`)}</Text></View> : null}
            {state?.lastError ? <Text style={styles.body}>{t(`executor.error.${state.lastError}`) === `executor.error.${state.lastError}` ? t("executor.operation_failed") : t(`executor.error.${state.lastError}`)}</Text> : null}
            <View style={styles.card}>
                <View style={styles.sectionHeading}><MaterialCommunityIcons name="history" size={21} color={colors.primaryDeep} /><Text style={styles.title}>{t("executor.recent")}</Text></View>
                <Text style={styles.body}>{t("executor.receipt_help")}</Text>
                {state?.recentReceipts.map(receipt => <View key={receipt.commandId} style={styles.receipt}>
                    <Text selectable style={styles.receiptId}>{receipt.commandId.slice(0, 8)}</Text>
                    <Text style={styles.receiptStatus}>{t(`executor.receipt.${receipt.status}`)}</Text>
                </View>)}
            </View>
        </ScrollView>
        <View style={styles.stopBar}>
            <View style={styles.stopContent}>{button("stop", () => void run(() => deviceExecutor.stop()), false, true)}</View>
        </View>
    </SafeAreaView>;
}

function makeStyles(colors: ReturnType<typeof useUiPrefs>["colors"]) {
    return StyleSheet.create({
        page: { flex: 1, backgroundColor: colors.background }, header: { paddingHorizontal: 20, paddingVertical: 12, flexDirection: "row", alignItems: "center", gap: 12 },
        backButton: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }, pageTitle: { flex: 1, fontSize: 22, lineHeight: 30, fontWeight: "800", color: colors.text }, headerIcon: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
        content: { padding: 20, paddingTop: 8, gap: 18, paddingBottom: 24, maxWidth: 600, width: "100%", alignSelf: "center" }, card: { padding: 18, gap: 14, borderRadius: 22, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border },
        statusCard: { borderColor: colors.primary }, statusHeading: { flexDirection: "row", alignItems: "center", gap: 12 }, statusIcon: { width: 48, height: 48, borderRadius: 16, backgroundColor: colors.primarySoft, alignItems: "center", justifyContent: "center" }, statusTitle: { flex: 1, fontSize: 21, lineHeight: 29, fontWeight: "800", color: colors.text },
        sectionHeading: { flexDirection: "row", alignItems: "center", gap: 10, paddingBottom: 2 }, title: { flex: 1, fontSize: 17, lineHeight: 25, fontWeight: "700", color: colors.text }, body: { fontSize: 13, lineHeight: 21, color: colors.textMuted },
        note: { flexDirection: "row", alignItems: "flex-start", gap: 8, paddingTop: 10, borderTopWidth: StyleSheet.hairlineWidth, borderColor: colors.border }, noteText: { flex: 1, fontSize: 12, lineHeight: 19, color: colors.textMuted },
        permissionRow: { flexDirection: "row", alignItems: "flex-start", gap: 9 }, permissionText: { flex: 1, fontSize: 13, lineHeight: 20, color: colors.textMuted }, divider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.border }, capabilities: { gap: 12, backgroundColor: colors.backgroundDeep, borderRadius: 14, padding: 14 }, switchRow: { flexDirection: "row", alignItems: "center", gap: 14 }, switchLabel: { flex: 1 },
        label: { fontSize: 13, lineHeight: 20, fontWeight: "700", color: colors.text }, input: { minHeight: 48, borderWidth: 1, borderColor: colors.border, borderRadius: 14, padding: 14, backgroundColor: colors.backgroundDeep, color: colors.text, fontSize: 15, lineHeight: 22 }, appsInput: { minHeight: 108, textAlignVertical: "top" },
        button: { minHeight: 48, padding: 12, borderWidth: 1, borderColor: colors.border, borderRadius: 14, flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 8, backgroundColor: colors.primarySoft },
        buttonText: { flexShrink: 1, color: colors.primaryDeep, fontSize: 14, lineHeight: 21, fontWeight: "700", textAlign: "center" }, primaryButton: { backgroundColor: colors.userBubbleBottom, borderColor: colors.userBubbleBottom }, disabled: { opacity: 0.4 }, pressed: { opacity: 0.76 },
        stopBar: { borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, paddingHorizontal: 20, paddingVertical: 12 }, stopContent: { width: "100%", maxWidth: 560, alignSelf: "center" }, stop: { minHeight: 52, backgroundColor: "#B42332", borderColor: "#B42332" }, onPrimaryText: { color: "#FFFFFF" }, revokeButton: { backgroundColor: "transparent", borderColor: colors.danger }, revokeText: { color: colors.danger },
        errorBox: { flexDirection: "row", alignItems: "flex-start", gap: 10, padding: 14, borderWidth: 1, borderColor: colors.danger, borderRadius: 14 }, error: { flex: 1, color: colors.danger, fontSize: 13, lineHeight: 21 },
        receipt: { gap: 5, borderTopWidth: StyleSheet.hairlineWidth, borderColor: colors.border, paddingTop: 12 }, receiptId: { color: colors.textSoft, fontSize: 11, fontVariant: ["tabular-nums"] }, receiptStatus: { fontSize: 13, lineHeight: 20, color: colors.text },
    });
}
