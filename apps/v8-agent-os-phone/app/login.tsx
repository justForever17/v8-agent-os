import { useEffect, useMemo, useState } from "react";
import {
    ActivityIndicator,
    Image,
    KeyboardAvoidingView,
    Platform,
    Pressable,
    StyleSheet,
    Text,
    TextInput,
    View,
} from "react-native";
import { Redirect, router, type Href } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { GlassCard } from "@/src/components/common/GlassCard";
import { useAppSession } from "@/src/providers/app-session";
import { colors, radii, spacing } from "@/src/theme/tokens";

const BRAND_MARK = require("../assets/images/brand-mark.png");

type Mode = "login" | "register";

export default function LoginScreen() {
    const { status, adminBaseUrl, signIn, signUp, user } = useAppSession();
    const [mode, setMode] = useState<Mode>("login");
    const [baseUrl, setBaseUrl] = useState(adminBaseUrl || "");
    const [login, setLogin] = useState("");
    const [password, setPassword] = useState("");
    const [name, setName] = useState("");
    const [email, setEmail] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (adminBaseUrl) {
            setBaseUrl(adminBaseUrl);
        }
    }, [adminBaseUrl]);

    const pageTitle = useMemo(
        () => (mode === "login" ? "欢迎回来" : "创建账号"),
        [mode],
    );
    const pageSubtitle = useMemo(
        () =>
            mode === "login"
                ? "使用和 Web 端完全一致的用户账号进入 V8 OS Phone。手机端直接连接 Admin 作为用户面 BFF。"
                : "这里复刻 Web 端注册链路。创建完成后会自动登录，并进入与 Web 同一条用户主链。",
        [mode],
    );

    if (status === "authenticated") {
        return <Redirect href={(user?.mustChangePassword ? "/settings" : "/chat") as Href} />;
    }

    const resetError = () => setError("");

    const validate = () => {
        if (!baseUrl.trim()) {
            setError("请填写可访问的 Admin 地址");
            return false;
        }
        if (!login.trim() || !password.trim()) {
            setError("请填写登录名和密码");
            return false;
        }
        if (mode === "register" && !name.trim()) {
            setError("注册时需要填写昵称");
            return false;
        }
        return true;
    };

    const submit = async () => {
        if (!validate()) {
            return;
        }
        setBusy(true);
        setError("");
        try {
            if (mode === "login") {
                await signIn({ adminBaseUrl: baseUrl, login, password });
                return;
            }
            await signUp({
                adminBaseUrl: baseUrl,
                login,
                password,
                name,
                email: email.trim() || undefined,
            });
        } catch (nextError) {
            setError(nextError instanceof Error ? nextError.message : mode === "login" ? "登录失败" : "注册失败");
        } finally {
            setBusy(false);
        }
    };

    return (
        <LinearGradient
            colors={["#EEF2FF", "#FFF7ED"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.gradient}
        >
            <SafeAreaView style={styles.safeArea}>
                <KeyboardAvoidingView
                    style={styles.keyboard}
                    behavior={Platform.OS === "ios" ? "padding" : undefined}
                >
                    <View style={styles.header}>
                        <View style={styles.brandRow}>
                            <Image source={BRAND_MARK} style={styles.brandMark} />
                            <View style={styles.brandTextWrap}>
                                <Text style={styles.brand}>V8 OS</Text>
                                <Text style={styles.brandSub}>Phone</Text>
                            </View>
                        </View>
                        <Text style={styles.title}>{pageTitle}</Text>
                        <Text style={styles.subtitle}>{pageSubtitle}</Text>
                    </View>

                    <GlassCard>
                        <View style={styles.modeSwitch}>
                            <Pressable
                                style={[styles.modeButton, mode === "login" && styles.modeButtonActive]}
                                onPress={() => {
                                    setMode("login");
                                    resetError();
                                }}
                            >
                                <Text style={[styles.modeText, mode === "login" && styles.modeTextActive]}>登录</Text>
                            </Pressable>
                            <Pressable
                                style={[styles.modeButton, mode === "register" && styles.modeButtonActive]}
                                onPress={() => {
                                    setMode("register");
                                    resetError();
                                }}
                            >
                                <Text style={[styles.modeText, mode === "register" && styles.modeTextActive]}>注册</Text>
                            </Pressable>
                        </View>

                        <View style={styles.form}>
                            <View style={styles.field}>
                                <Text style={styles.label}>Admin 地址</Text>
                                <TextInput
                                    autoCapitalize="none"
                                    autoCorrect={false}
                                    value={baseUrl}
                                    onChangeText={(next) => {
                                        setBaseUrl(next);
                                        resetError();
                                    }}
                                    placeholder="http://192.168.x.x:9528"
                                    placeholderTextColor={colors.textSoft}
                                    style={styles.input}
                                />
                            </View>

                            {mode === "register" ? (
                                <View style={styles.field}>
                                    <Text style={styles.label}>昵称</Text>
                                    <TextInput
                                        value={name}
                                        onChangeText={(next) => {
                                            setName(next);
                                            resetError();
                                        }}
                                        placeholder="怎么称呼你？"
                                        placeholderTextColor={colors.textSoft}
                                        style={styles.input}
                                    />
                                </View>
                            ) : null}

                            <View style={styles.field}>
                                <Text style={styles.label}>登录名</Text>
                                <TextInput
                                    autoCapitalize="none"
                                    autoCorrect={false}
                                    value={login}
                                    onChangeText={(next) => {
                                        setLogin(next);
                                        resetError();
                                    }}
                                    placeholder={mode === "login" ? "输入 Web 端同一登录名或邮箱" : "设置一个登录名"}
                                    placeholderTextColor={colors.textSoft}
                                    style={styles.input}
                                />
                            </View>

                            {mode === "register" ? (
                                <View style={styles.field}>
                                    <Text style={styles.label}>邮箱</Text>
                                    <TextInput
                                        autoCapitalize="none"
                                        autoCorrect={false}
                                        keyboardType="email-address"
                                        value={email}
                                        onChangeText={(next) => {
                                            setEmail(next);
                                            resetError();
                                        }}
                                        placeholder="可选，便于同步头像与通知"
                                        placeholderTextColor={colors.textSoft}
                                        style={styles.input}
                                    />
                                </View>
                            ) : null}

                            <View style={styles.field}>
                                <Text style={styles.label}>{mode === "login" ? "密码" : "设置密码"}</Text>
                                <TextInput
                                    secureTextEntry
                                    value={password}
                                    onChangeText={(next) => {
                                        setPassword(next);
                                        resetError();
                                    }}
                                    placeholder="••••••"
                                    placeholderTextColor={colors.textSoft}
                                    style={styles.input}
                                />
                            </View>

                            {error ? (
                                <View style={styles.errorRow}>
                                    <MaterialCommunityIcons name="alert-circle-outline" size={16} color={colors.danger} />
                                    <Text style={styles.error}>{error}</Text>
                                </View>
                            ) : null}

                            <Pressable disabled={busy} onPress={() => void submit()} style={[styles.submit, busy && styles.disabled]}>
                                <LinearGradient
                                    colors={[colors.primary, colors.primaryDeep]}
                                    start={{ x: 0, y: 0 }}
                                    end={{ x: 1, y: 1 }}
                                    style={styles.submitGradient}
                                >
                                    {busy ? (
                                        <ActivityIndicator color="#FFFFFF" />
                                    ) : (
                                        <Text style={styles.submitText}>
                                            {mode === "login" ? "登录并进入 V8 OS Phone" : "创建账号并进入 V8 OS Phone"}
                                        </Text>
                                    )}
                                </LinearGradient>
                            </Pressable>
                        </View>
                    </GlassCard>

                    <Pressable style={styles.connectHint} onPress={() => router.push("/connect" as Href)}>
                        <MaterialCommunityIcons name="lan-connect" size={16} color={colors.textMuted} />
                        <Text style={styles.connectHintText}>先检查连接地址</Text>
                    </Pressable>
                </KeyboardAvoidingView>
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
    keyboard: {
        flex: 1,
        justifyContent: "center",
        paddingHorizontal: spacing.xl,
        gap: spacing.xl,
    },
    header: {
        gap: 10,
    },
    brandRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
    },
    brandMark: {
        width: 42,
        height: 42,
        borderRadius: 14,
    },
    brandTextWrap: {
        flexDirection: "row",
        alignItems: "baseline",
        gap: 6,
    },
    brand: {
        color: colors.primary,
        fontSize: 28,
        fontWeight: "900",
    },
    brandSub: {
        color: colors.textMuted,
        fontSize: 16,
        fontWeight: "800",
    },
    title: {
        color: colors.text,
        fontSize: 28,
        lineHeight: 36,
        fontWeight: "900",
    },
    subtitle: {
        color: colors.textMuted,
        fontSize: 15,
        lineHeight: 22,
    },
    modeSwitch: {
        flexDirection: "row",
        alignSelf: "stretch",
        borderRadius: radii.lg,
        backgroundColor: colors.surfaceMuted,
        borderWidth: 1,
        borderColor: colors.border,
        padding: 4,
        marginBottom: spacing.lg,
    },
    modeButton: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: radii.md,
        minHeight: 42,
    },
    modeButtonActive: {
        backgroundColor: colors.surface,
    },
    modeText: {
        color: colors.textMuted,
        fontSize: 14,
        fontWeight: "700",
    },
    modeTextActive: {
        color: colors.text,
    },
    form: {
        gap: spacing.lg,
    },
    field: {
        gap: 8,
    },
    label: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "700",
    },
    input: {
        borderWidth: 1,
        borderColor: colors.border,
        borderRadius: radii.md,
        backgroundColor: colors.surface,
        paddingHorizontal: 14,
        paddingVertical: 14,
        color: colors.text,
        fontSize: 16,
    },
    errorRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
    },
    error: {
        flex: 1,
        color: colors.danger,
        fontSize: 13,
        lineHeight: 20,
    },
    submit: {
        borderRadius: radii.lg,
        overflow: "hidden",
    },
    submitGradient: {
        minHeight: 54,
        borderRadius: radii.lg,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 18,
    },
    submitText: {
        color: "#FFFFFF",
        fontSize: 16,
        fontWeight: "800",
        textAlign: "center",
    },
    disabled: {
        opacity: 0.7,
    },
    connectHint: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
    },
    connectHintText: {
        color: colors.textMuted,
        fontSize: 13,
        fontWeight: "700",
    },
});
