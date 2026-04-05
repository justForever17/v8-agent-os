import { useEffect, useState } from "react";
import {
    ActivityIndicator,
    Alert,
    Image,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    TextInput,
    View,
} from "react-native";
import { Redirect, router, type Href } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";

import { GlassCard } from "@/src/components/common/GlassCard";
import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { getCurrentProfile, updatePassword, updateProfile, uploadUserAvatar } from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";

export default function SettingsScreen() {
    const { status, user, adminBaseUrl, signOut, authorizedFetch, refreshUser, updateCurrentUser } = useAppSession();
    const { t } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const [name, setName] = useState(user?.name || "");
    const [email, setEmail] = useState(user?.email || "");
    const [image, setImage] = useState(user?.image || "");
    const [currentPassword, setCurrentPassword] = useState("");
    const [nextPassword, setNextPassword] = useState("");
    const [confirmPassword, setConfirmPassword] = useState("");
    const [profileBusy, setProfileBusy] = useState(false);
    const [avatarBusy, setAvatarBusy] = useState(false);
    const [passwordBusy, setPasswordBusy] = useState(false);
    const [profileMessage, setProfileMessage] = useState("");
    const [passwordMessage, setPasswordMessage] = useState("");
    const [loadingProfile, setLoadingProfile] = useState(false);

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "connect", icon: "lan-connect", onPress: () => router.push("/connect" as Href) },
        { key: "desktop-live", icon: "monitor-dashboard", onPress: () => router.push("/desktop-live" as Href) },
        { key: "rpa", icon: "robot-outline", onPress: () => router.push("/rpa" as Href) },
        { key: "approvals", icon: "bell-outline", onPress: () => router.push("/approvals" as Href) },
    ];

    useEffect(() => {
        setName(user?.name || "");
        setEmail(user?.email || "");
        setImage(user?.image || "");
    }, [user?.email, user?.image, user?.name]);

    useEffect(() => {
        if (status !== "authenticated") {
            return;
        }
        let cancelled = false;
        const hydrateProfile = async () => {
            setLoadingProfile(true);
            try {
                const profile = await getCurrentProfile(authorizedFetch);
                if (!cancelled && profile) {
                    setName(profile.name || "");
                    setEmail(profile.email || "");
                    setImage(profile.image || "");
                    await updateCurrentUser(profile);
                }
            } catch {
                // 页面继续使用已有会话里的用户信息，避免硬失败。
            } finally {
                if (!cancelled) {
                    setLoadingProfile(false);
                }
            }
        };
        void hydrateProfile();
        return () => {
            cancelled = true;
        };
    }, [authorizedFetch, status, updateCurrentUser]);

    const avatarUri = image ? resolveAdminAssetUrl(adminBaseUrl, image) : "";

    const saveProfile = async () => {
        setProfileBusy(true);
        setProfileMessage("");
        try {
            const updated = await updateProfile(authorizedFetch, {
                name: name.trim() || undefined,
                email: email.trim() || undefined,
                image: image || undefined,
            });
            if (updated) {
                await updateCurrentUser(updated);
            } else {
                await refreshUser();
            }
            setProfileMessage(t("资料已同步到当前用户主链。", "Profile synced to the current user runtime lane."));
        } catch (error) {
            Alert.alert(t("保存失败", "Save failed"), error instanceof Error ? error.message : t("无法更新资料", "Unable to update the profile."));
        } finally {
            setProfileBusy(false);
        }
    };

    const pickAvatar = async () => {
        setAvatarBusy(true);
        setProfileMessage("");
        try {
            const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
            if (!permission.granted) {
                throw new Error(t("需要相册权限才能上传头像", "Photo library permission is required to upload an avatar."));
            }
            const result = await ImagePicker.launchImageLibraryAsync({
                mediaTypes: ["images"],
                quality: 0.9,
                allowsEditing: true,
                aspect: [1, 1],
            });
            if (result.canceled || !result.assets[0]) {
                return;
            }
            const asset = result.assets[0];
            const uploaded = await uploadUserAvatar(authorizedFetch, {
                uri: asset.uri,
                name: asset.fileName || `avatar-${Date.now()}.jpg`,
                type: asset.mimeType || "image/jpeg",
            });
            if (!uploaded.url) {
                throw new Error(t("头像上传成功，但未返回地址", "The avatar upload succeeded, but no URL was returned."));
            }
            setImage(uploaded.url);
            const updated = await updateProfile(authorizedFetch, {
                name: name.trim() || undefined,
                email: email.trim() || undefined,
                image: uploaded.url,
            });
            if (updated) {
                await updateCurrentUser(updated);
            } else {
                await refreshUser();
            }
            setProfileMessage(t("头像已更新。", "Avatar updated."));
        } catch (error) {
            Alert.alert(t("头像更新失败", "Avatar update failed"), error instanceof Error ? error.message : t("无法更新头像", "Unable to update the avatar."));
        } finally {
            setAvatarBusy(false);
        }
    };

    const savePassword = async () => {
        if (!currentPassword.trim() || !nextPassword.trim()) {
            Alert.alert(t("修改失败", "Update failed"), t("请填写当前密码和新密码", "Please enter both the current password and the new password."));
            return;
        }
        if (nextPassword !== confirmPassword) {
            Alert.alert(t("修改失败", "Update failed"), t("两次输入的新密码不一致", "The new passwords do not match."));
            return;
        }
        setPasswordBusy(true);
        setPasswordMessage("");
        try {
            await updatePassword(authorizedFetch, {
                currentPassword,
                nextPassword,
            });
            const refreshedUser = await refreshUser();
            if (refreshedUser) {
                await updateCurrentUser(refreshedUser);
            }
            setCurrentPassword("");
            setNextPassword("");
            setConfirmPassword("");
            setPasswordMessage(t("密码已更新。", "Password updated."));
        } catch (error) {
            Alert.alert(t("修改失败", "Update failed"), error instanceof Error ? error.message : t("无法修改密码", "Unable to update the password."));
        } finally {
            setPasswordBusy(false);
        }
    };

    if (status === "booting") {
        return <LoadingScreen label={t("正在读取手机端设置…", "Loading phone settings...")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    return (
        <LinearGradient colors={[colors.background, "#FFF7ED"]} style={styles.gradient}>
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar actions={actions} onBrandPress={() => void goHomeToChat()} />

                <ScrollView contentContainerStyle={styles.content}>
                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("当前账号", "Current account")}</Text>
                        <View style={styles.userRow}>
                            {avatarUri ? (
                                <Image source={{ uri: avatarUri }} style={styles.userAvatarImage} />
                            ) : (
                                <View style={styles.userAvatar}>
                                    <Text style={styles.userAvatarText}>{(user?.name || user?.login || "V").slice(0, 1).toUpperCase()}</Text>
                                </View>
                            )}
                            <View style={styles.userBody}>
                                <Text style={styles.userName}>{user?.name || user?.login || t("未知用户", "Unknown user")}</Text>
                                <Text style={styles.userMeta}>{user?.email || t("未提供邮箱", "No email provided")}</Text>
                                <Text style={styles.userTag}>{t("角色", "Role")}：{user?.role || "USER"}</Text>
                            </View>
                        </View>
                        {user?.mustChangePassword ? (
                            <View style={styles.noticeBox}>
                                <MaterialCommunityIcons name="shield-key-outline" size={16} color={colors.warning} />
                                <Text style={styles.noticeText}>{t("当前账号要求先修改密码，修改完成后会与 Web 端保持同一用户态。", "This account must change its password first. After that, Phone and Web will share the same user session.")}</Text>
                            </View>
                        ) : null}
                    </GlassCard>

                    <GlassCard>
                        <View style={styles.sectionTitleRow}>
                            <Text style={styles.sectionTitle}>{t("个人资料", "Profile")}</Text>
                            {loadingProfile ? <ActivityIndicator color={colors.primary} size="small" /> : null}
                        </View>
                        <View style={styles.profileCard}>
                            <Pressable style={styles.avatarPicker} onPress={() => void pickAvatar()} disabled={avatarBusy}>
                                {avatarUri ? (
                                    <Image source={{ uri: avatarUri }} style={styles.avatarPickerImage} />
                                ) : (
                                    <View style={styles.avatarPickerFallback}>
                                        <Text style={styles.avatarPickerText}>{(name || user?.login || "V").slice(0, 1).toUpperCase()}</Text>
                                    </View>
                                )}
                                <View style={styles.avatarPickerBadge}>
                                    {avatarBusy ? (
                                        <ActivityIndicator color="#FFFFFF" size="small" />
                                    ) : (
                                        <MaterialCommunityIcons name="camera-plus-outline" size={16} color="#FFFFFF" />
                                    )}
                                </View>
                            </Pressable>
                            <View style={styles.profileFields}>
                                <View style={styles.field}>
                                    <Text style={styles.fieldLabel}>{t("昵称", "Display name")}</Text>
                                    <TextInput
                                        value={name}
                                        onChangeText={setName}
                                        placeholder={t("你的显示名称", "Your display name")}
                                        placeholderTextColor={colors.textSoft}
                                        style={styles.input}
                                    />
                                </View>
                                <View style={styles.field}>
                                    <Text style={styles.fieldLabel}>{t("邮箱", "Email")}</Text>
                                    <TextInput
                                        value={email}
                                        onChangeText={setEmail}
                                        autoCapitalize="none"
                                        autoCorrect={false}
                                        keyboardType="email-address"
                                        placeholder="name@example.com"
                                        placeholderTextColor={colors.textSoft}
                                        style={styles.input}
                                    />
                                </View>
                            </View>
                        </View>
                        {profileMessage ? <Text style={styles.successText}>{profileMessage}</Text> : null}
                        <Pressable style={[styles.primaryButton, profileBusy && styles.disabled]} onPress={() => void saveProfile()}>
                            {profileBusy ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.primaryButtonText}>{t("保存资料", "Save profile")}</Text>}
                        </Pressable>
                    </GlassCard>

                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("安全与密码", "Security & password")}</Text>
                        <View style={styles.field}>
                            <Text style={styles.fieldLabel}>{t("当前密码", "Current password")}</Text>
                            <TextInput
                                secureTextEntry
                                value={currentPassword}
                                onChangeText={setCurrentPassword}
                                placeholder={t("输入当前密码", "Enter current password")}
                                placeholderTextColor={colors.textSoft}
                                style={styles.input}
                            />
                        </View>
                        <View style={styles.field}>
                            <Text style={styles.fieldLabel}>{t("新密码", "New password")}</Text>
                            <TextInput
                                secureTextEntry
                                value={nextPassword}
                                onChangeText={setNextPassword}
                                placeholder={t("输入新密码", "Enter new password")}
                                placeholderTextColor={colors.textSoft}
                                style={styles.input}
                            />
                        </View>
                        <View style={styles.field}>
                            <Text style={styles.fieldLabel}>{t("确认新密码", "Confirm new password")}</Text>
                            <TextInput
                                secureTextEntry
                                value={confirmPassword}
                                onChangeText={setConfirmPassword}
                                placeholder={t("再次输入新密码", "Enter the new password again")}
                                placeholderTextColor={colors.textSoft}
                                style={styles.input}
                            />
                        </View>
                        {passwordMessage ? <Text style={styles.successText}>{passwordMessage}</Text> : null}
                        <Pressable style={[styles.primaryButton, passwordBusy && styles.disabled]} onPress={() => void savePassword()}>
                            {passwordBusy ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.primaryButtonText}>{t("更新密码", "Update password")}</Text>}
                        </Pressable>
                    </GlassCard>

                    <GlassCard>
                        <Text style={styles.sectionTitle}>{t("连接摘要", "Connection summary")}</Text>
                        <View style={styles.summaryGroup}>
                            <Text style={styles.summaryLabel}>Admin BFF</Text>
                            <Text style={styles.summaryValue}>{adminBaseUrl || t("未连接", "Not connected")}</Text>
                        </View>
                        <View style={styles.summaryGroup}>
                            <Text style={styles.summaryLabel}>{t("用户面语义", "User-surface contract")}</Text>
                            <Text style={styles.summaryValue}>{t("Phone 与 Web 使用同一条用户主链，不下放后台治理权限。", "Phone and Web share the same user-facing runtime lane without exposing background governance controls.")}</Text>
                        </View>
                        <View style={styles.buttonRow}>
                            <Pressable style={styles.primaryButton} onPress={() => router.push("/connect" as Href)}>
                                <MaterialCommunityIcons name="lan-connect" size={16} color="#FFFFFF" />
                                <Text style={styles.primaryButtonText}>{t("打开连接页", "Open connection page")}</Text>
                            </Pressable>
                            <Pressable style={styles.secondaryButton} onPress={() => void signOut()}>
                                <MaterialCommunityIcons name="logout" size={16} color={colors.text} />
                                <Text style={styles.secondaryButtonText}>{t("退出登录", "Sign out")}</Text>
                            </Pressable>
                        </View>
                    </GlassCard>
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
    userRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.md,
    },
    userAvatar: {
        width: 44,
        height: 44,
        borderRadius: 22,
        backgroundColor: colors.primarySoft,
        alignItems: "center",
        justifyContent: "center",
    },
    userAvatarImage: {
        width: 44,
        height: 44,
        borderRadius: 22,
        backgroundColor: colors.surface,
    },
    userAvatarText: {
        color: colors.primaryDeep,
        fontSize: 16,
        fontWeight: "900",
    },
    userBody: {
        flex: 1,
        gap: 2,
    },
    userName: {
        color: colors.text,
        fontSize: 16,
        fontWeight: "800",
    },
    userMeta: {
        color: colors.textMuted,
        fontSize: 13,
    },
    userTag: {
        color: colors.textSoft,
        fontSize: 12,
        fontWeight: "700",
        marginTop: 2,
    },
    noticeBox: {
        marginTop: spacing.md,
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        borderRadius: radii.md,
        borderWidth: 1,
        borderColor: "rgba(245,158,11,0.24)",
        backgroundColor: "rgba(245,158,11,0.12)",
        paddingHorizontal: 12,
        paddingVertical: 10,
    },
    noticeText: {
        flex: 1,
        color: colors.text,
        fontSize: 13,
        lineHeight: 20,
    },
    profileCard: {
        flexDirection: "row",
        gap: spacing.md,
        alignItems: "flex-start",
        marginBottom: spacing.md,
    },
    avatarPicker: {
        width: 86,
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
    },
    avatarPickerImage: {
        width: 86,
        height: 86,
        borderRadius: 28,
        backgroundColor: colors.surface,
    },
    avatarPickerFallback: {
        width: 86,
        height: 86,
        borderRadius: 28,
        backgroundColor: colors.primarySoft,
        alignItems: "center",
        justifyContent: "center",
    },
    avatarPickerText: {
        color: colors.primaryDeep,
        fontSize: 28,
        fontWeight: "900",
    },
    avatarPickerBadge: {
        position: "absolute",
        right: 4,
        bottom: 4,
        width: 28,
        height: 28,
        borderRadius: 14,
        backgroundColor: colors.primary,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 2,
        borderColor: "#FFFFFF",
    },
    profileFields: {
        flex: 1,
        gap: spacing.md,
    },
    field: {
        gap: 8,
        marginBottom: spacing.md,
    },
    fieldLabel: {
        color: colors.text,
        fontSize: 13,
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
    successText: {
        color: colors.success,
        fontSize: 13,
        fontWeight: "700",
        marginBottom: spacing.md,
    },
    summaryGroup: {
        gap: 4,
        marginBottom: spacing.md,
    },
    summaryLabel: {
        color: colors.textSoft,
        fontSize: 11,
        fontWeight: "800",
        letterSpacing: 0.8,
        textTransform: "uppercase",
    },
    summaryValue: {
        color: colors.text,
        fontSize: 14,
        lineHeight: 21,
    },
    buttonRow: {
        flexDirection: "row",
        gap: spacing.sm,
    },
    primaryButton: {
        flex: 1,
        minHeight: 46,
        borderRadius: radii.md,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: colors.primary,
        flexDirection: "row",
        gap: 8,
    },
    primaryButtonText: {
        color: "#FFFFFF",
        fontWeight: "800",
    },
    secondaryButton: {
        flex: 1,
        minHeight: 46,
        borderRadius: radii.md,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: colors.surface,
        borderWidth: 1,
        borderColor: colors.border,
        flexDirection: "row",
        gap: 8,
    },
    secondaryButtonText: {
        color: colors.text,
        fontWeight: "800",
    },
    disabled: {
        opacity: 0.6,
    },
});
