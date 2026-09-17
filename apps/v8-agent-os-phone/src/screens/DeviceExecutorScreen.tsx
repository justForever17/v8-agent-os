import { useEffect, useState } from "react";
import { ActivityIndicator, AppState, PermissionsAndroid, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { router } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
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
        <Pressable accessibilityRole="button" disabled={disabled} onPress={action} style={[styles.button, danger && styles.stop, disabled && styles.disabled]}>
            <Text style={[styles.buttonText, danger && styles.stopText]}>{t(`executor.${label}`)}</Text>
        </Pressable>
    );
    return <SafeAreaView style={styles.page}>
        <View style={styles.header}>{button("back", () => router.back())}<Text style={styles.title}>{t("executor.title")}</Text></View>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <Text style={styles.body}>{t("executor.description")}</Text>
            <View style={styles.card}>
                <Text style={styles.title}>{t(`executor.state.${state?.status || "loading"}`)}</Text>
                <Text selectable style={styles.body}>{state?.baseUrl || t("executor.unbound")}</Text>
                <Text style={styles.body}>{t("executor.profile_independent")}</Text>
                {button("stop", () => void run(() => deviceExecutor.stop()), false, true)}
                {state?.enabled ? button("disable", () => void run(() => deviceExecutor.disable()), busy) : null}
            </View>
            {state?.supported ? <>
                <View style={styles.card}>
                    <Text style={styles.title}>{t("executor.permissions")}</Text>
                    <Text style={styles.body}>{t(state.accessibilityGranted ? "executor.accessibility_ready" : "executor.accessibility_needed")}</Text>
                    {button("open_permissions", () => void run(() => deviceExecutor.openAccessibilitySettings()), busy)}
                    <Text style={styles.body}>{t(state.notificationGranted ? "executor.notification_ready" : "executor.notification_needed")}</Text>
                    {button("allow_notifications", () => void run(async () => {
                        if (Platform.OS === "android" && Number(Platform.Version) >= 33) await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS);
                        return deviceExecutor.getState();
                    }), busy)}
                </View>
                <View style={styles.card}>
                    <Text style={styles.title}>{t("executor.binding")}</Text>
                    {state.deviceId ? <Text selectable style={styles.body}>{state.name} · {state.authorityId}</Text> : <>
                        <Text style={styles.label}>{t("executor.origin")}</Text>
                        <TextInput accessibilityLabel={t("executor.origin")} autoCapitalize="none" value={baseUrl} onChangeText={setBaseUrl} placeholder="https://engine.example.com" placeholderTextColor={colors.textMuted} style={styles.input} />
                        <Text style={styles.label}>{t("executor.name")}</Text>
                        <TextInput accessibilityLabel={t("executor.name")} value={name} onChangeText={setName} style={styles.input} />
                    </>}
                    <Text style={styles.label}>{t("executor.allowed_apps")}</Text>
                    <Text style={styles.body}>{t("executor.scope_help")}</Text>
                    <Text style={styles.body}>{t(state.windowCaptureAvailable ? "executor.window_capture_ready" : "executor.window_capture_unavailable")}</Text>
                    <Text style={styles.body}>{t(state.gestureAvailable ? "executor.gesture_ready" : "executor.gesture_unavailable")}</Text>
                    {state.deviceId ? <>
                        <Text style={styles.label}>{t("executor.full_display_capture")}</Text>
                        <Text style={styles.body}>{t("executor.full_display_capture_help")}</Text>
                        <Switch accessibilityLabel={t("executor.full_display_capture")} value={state.fullDisplayCapture} disabled={busy}
                            onValueChange={enabled => void run(() => deviceExecutor.setFullDisplayCapture(enabled))} />
                    </> : null}
                    <TextInput accessibilityLabel={t("executor.allowed_apps")} value={appText} onChangeText={setAppText} multiline autoCapitalize="none" autoCorrect={false} placeholder="com.example.fixture" placeholderTextColor={colors.textMuted} style={styles.input} />
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
            {error ? <Text selectable style={styles.error}>{t(`executor.error.${error}`, {}) === `executor.error.${error}` ? t("executor.operation_failed") : t(`executor.error.${error}`)}</Text> : null}
            {state?.lastError ? <Text style={styles.body}>{t(`executor.error.${state.lastError}`) === `executor.error.${state.lastError}` ? t("executor.operation_failed") : t(`executor.error.${state.lastError}`)}</Text> : null}
            <View style={styles.card}>
                <Text style={styles.title}>{t("executor.recent")}</Text>
                <Text style={styles.body}>{t("executor.receipt_help")}</Text>
                {state?.recentReceipts.map(receipt => <Text key={receipt.commandId} style={styles.body}>{receipt.commandId.slice(0, 8)} · {t(`executor.receipt.${receipt.status}`)}</Text>)}
            </View>
        </ScrollView>
    </SafeAreaView>;
}

function makeStyles(colors: ReturnType<typeof useUiPrefs>["colors"]) {
    return StyleSheet.create({
        page: { flex: 1, backgroundColor: colors.background }, header: { padding: 16, flexDirection: "row", alignItems: "center", gap: 16 },
        content: { padding: 20, gap: 16, paddingBottom: 48 }, card: { padding: 18, gap: 12, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border },
        title: { fontSize: 20, fontWeight: "700", color: colors.text }, body: { fontSize: 14, lineHeight: 22, color: colors.textMuted },
        label: { fontSize: 14, fontWeight: "600", color: colors.text }, input: { borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 12, color: colors.text, fontSize: 16 },
        button: { minHeight: 44, padding: 12, borderWidth: 1, borderColor: colors.border, borderRadius: 10, alignItems: "center" },
        buttonText: { color: colors.primary, fontSize: 15, fontWeight: "600" }, disabled: { opacity: 0.4 }, stop: { backgroundColor: "#9F2525", borderColor: "#9F2525" }, stopText: { color: "#FFFFFF" }, error: { color: "#C03030" },
    });
}
