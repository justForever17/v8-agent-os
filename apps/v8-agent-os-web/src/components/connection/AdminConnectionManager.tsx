"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";
import {
    CheckCircle2,
    Loader2,
    PlugZap,
    RotateCcw,
    ServerCrash,
    Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { Locale, lt, pickLocalizedText } from "@/lib/locale";
import {
    AdminConnectionProfile,
    findAdminConnectionProfileByBaseUrl,
    findAdminConnectionProfileById,
    readActiveAdminConnectionProfileId,
    readAdminConnectionProfiles,
    removeAdminConnectionProfile,
    upsertAdminConnectionProfile,
    writeActiveAdminConnectionProfileId,
    writeAdminConnectionProfiles,
} from "@/lib/admin-connection-profiles";
import { AdminConnection } from "@/lib/admin-connection-utils";

type ConnectionPayload = {
    connection?: AdminConnection | null;
    error?: string;
};

const DEFAULT_LOCAL_ADMIN_BASE_URL = "http://127.0.0.1:9528";

function isLoopbackAdminUrl(value: string) {
    try {
        const parsed = new URL(value);
        return parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1" || parsed.hostname === "::1";
    } catch {
        return false;
    }
}

function formatLastUsed(value: string | undefined, locale: Locale) {
    if (!value) return pickLocalizedText(locale, lt("未知", "Unknown"));
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return pickLocalizedText(locale, lt("未知", "Unknown"));
    return new Intl.DateTimeFormat(locale, {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    }).format(date);
}

function SummaryStat({
    label,
    value,
}: {
    label: string;
    value: string;
}) {
    return (
        <div className="rounded-2xl border border-slate-200/80 bg-white/80 px-3 py-3 dark:border-slate-800/80 dark:bg-slate-900/70">
            <div className="text-[11px] uppercase tracking-[0.16em] text-slate-500 dark:text-slate-400">{label}</div>
            <div className="mt-1 break-all text-sm font-medium text-slate-900 dark:text-slate-100">{value}</div>
        </div>
    );
}

export function AdminConnectionManager({
    nextPath = "/chat",
    variant = "page",
    autoRestore = false,
}: {
    nextPath?: string;
    variant?: "page" | "panel";
    autoRestore?: boolean;
}) {
    const router = useRouter();
    const t = useT();
    const { locale } = useLocale();
    const [connection, setConnection] = useState<AdminConnection | null>(null);
    const [profiles, setProfiles] = useState<AdminConnectionProfile[]>([]);
    const [activeProfileId, setActiveProfileId] = useState<string | null>(null);
    const [adminBaseUrl, setAdminBaseUrl] = useState("");
    const [loading, setLoading] = useState(true);
    const [testing, setTesting] = useState(false);
    const [saving, setSaving] = useState(false);
    const [clearing, setClearing] = useState(false);
    const [switchingProfileId, setSwitchingProfileId] = useState<string | null>(null);
    const [deletingProfileId, setDeletingProfileId] = useState<string | null>(null);
    const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

    const profilesRef = useRef<AdminConnectionProfile[]>([]);
    const activeProfileIdRef = useRef<string | null>(null);
    const autoRestoreAttemptedRef = useRef(false);

    useEffect(() => {
        profilesRef.current = profiles;
    }, [profiles]);

    useEffect(() => {
        activeProfileIdRef.current = activeProfileId;
    }, [activeProfileId]);

    const hydrateProfiles = useCallback((nextConnection: AdminConnection | null) => {
        let nextProfiles = readAdminConnectionProfiles();
        let nextActiveProfileId = readActiveAdminConnectionProfileId();

        if (nextConnection) {
            const currentProfile =
                findAdminConnectionProfileById(nextProfiles, nextActiveProfileId)
                || findAdminConnectionProfileByBaseUrl(nextProfiles, nextConnection.adminBaseUrl);
            const upserted = upsertAdminConnectionProfile(nextProfiles, nextConnection, {
                profileId: currentProfile?.id || nextActiveProfileId,
            });
            nextProfiles = upserted.profiles;
            nextActiveProfileId = upserted.profile.id;
            writeAdminConnectionProfiles(nextProfiles);
            writeActiveAdminConnectionProfileId(nextActiveProfileId);
        }

        setProfiles(nextProfiles);
        setActiveProfileId(nextActiveProfileId);
        setConnection(nextConnection);

        if (nextConnection) {
            setAdminBaseUrl(nextConnection.adminBaseUrl);
            return;
        }

        const activeProfile = findAdminConnectionProfileById(nextProfiles, nextActiveProfileId);
        setAdminBaseUrl(activeProfile?.adminBaseUrl || "");
    }, []);

    const loadConnection = useCallback(async () => {
        setLoading(true);
        try {
            const response = await fetch("/api/connection", { cache: "no-store" });
            const payload = (await response.json().catch(() => ({}))) as ConnectionPayload;
            hydrateProfiles(payload.connection || null);
            setMessage(null);
        } finally {
            setLoading(false);
        }
    }, [hydrateProfiles]);

    useEffect(() => {
        void loadConnection();
    }, [loadConnection]);

    const persistActivatedConnection = useCallback((nextConnection: AdminConnection, preferredProfileId?: string | null) => {
        const upserted = upsertAdminConnectionProfile(profilesRef.current, nextConnection, {
            profileId: preferredProfileId,
        });
        profilesRef.current = upserted.profiles;
        activeProfileIdRef.current = upserted.profile.id;
        writeAdminConnectionProfiles(upserted.profiles);
        writeActiveAdminConnectionProfileId(upserted.profile.id);
        setProfiles(upserted.profiles);
        setActiveProfileId(upserted.profile.id);
        setConnection(nextConnection);
        setAdminBaseUrl(nextConnection.adminBaseUrl);
        return upserted.profile;
    }, []);

    const activateConnection = useCallback(async (
        nextAdminBaseUrl: string,
        options?: {
            silent?: boolean;
            redirectOnSuccess?: boolean;
            preferredProfileId?: string | null;
            localSession?: boolean;
        },
    ) => {
        setSaving(true);
        if (!options?.silent) {
            setMessage(null);
        }
        try {
            const response = await fetch("/api/connection", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ adminBaseUrl: nextAdminBaseUrl.trim(), persist: true }),
            });
            const payload = (await response.json().catch(() => ({}))) as ConnectionPayload;
            if (!response.ok || !payload.connection) {
                throw new Error(payload.error || t(lt("连接管理台失败", "Failed to connect to the admin console")));
            }

            persistActivatedConnection(payload.connection, options?.preferredProfileId || null);

            if (options?.localSession) {
                const result = await signIn("credentials", {
                    localSession: "1",
                    adminBaseUrl: payload.connection.adminBaseUrl,
                    redirect: false,
                });
                if (!result?.ok || result.error) {
                    throw new Error(t(lt("本机自动登录失败", "Local sign-in failed")));
                }
            }

            if (!options?.silent) {
                setMessage({ type: "success", text: t(lt("连接已保存。", "Connection saved.")) });
            }

            if (options?.redirectOnSuccess) {
                router.replace(nextPath);
                router.refresh();
            } else {
                router.refresh();
            }

            return payload.connection;
        } catch (error) {
            const text = error instanceof Error ? error.message : t(lt("连接失败", "Connection failed"));
            setMessage({ type: "error", text });
            throw error;
        } finally {
            setSaving(false);
        }
    }, [nextPath, persistActivatedConnection, router, t]);

    useEffect(() => {
        if (!autoRestore || loading || connection || autoRestoreAttemptedRef.current) {
            return;
        }
        const profile = findAdminConnectionProfileById(profiles, activeProfileId);
        autoRestoreAttemptedRef.current = true;
        const targetAdminBaseUrl = profile?.adminBaseUrl || DEFAULT_LOCAL_ADMIN_BASE_URL;
        void activateConnection(targetAdminBaseUrl, {
            silent: true,
            redirectOnSuccess: variant === "page",
            preferredProfileId: profile?.id,
            localSession: isLoopbackAdminUrl(targetAdminBaseUrl),
        }).catch(() => {
            setMessage({ type: "error", text: t(lt("本机自动连接失败，请确认 Admin 已启动。", "Local auto-connect failed. Make sure Admin is running.")) });
        });
    }, [activateConnection, activeProfileId, autoRestore, connection, loading, profiles, t, variant]);

    const handleTest = async () => {
        const normalized = adminBaseUrl.trim();
        if (!normalized) {
            setMessage({ type: "error", text: t(lt("请先填写管理台地址。", "Please enter the admin console URL first.")) });
            return;
        }
        setTesting(true);
        setMessage(null);
        try {
            const response = await fetch("/api/connection", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ adminBaseUrl: normalized, persist: false }),
            });
            const payload = (await response.json().catch(() => ({}))) as ConnectionPayload;
            if (!response.ok || !payload.connection) {
                throw new Error(payload.error || t(lt("连接管理台失败", "Failed to connect to the admin console")));
            }
            setMessage({ type: "success", text: t(lt("连接测试成功，可以保存为当前连接。", "Connection test passed. You can save it as the current connection.")) });
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t(lt("连接失败", "Connection failed")) });
        } finally {
            setTesting(false);
        }
    };

    const handleSave = async () => {
        const normalized = adminBaseUrl.trim();
        if (!normalized) {
            setMessage({ type: "error", text: t(lt("请先填写管理台地址。", "Please enter the admin console URL first.")) });
            return;
        }
        await activateConnection(normalized, {
            redirectOnSuccess: variant === "page",
            localSession: variant === "page" && isLoopbackAdminUrl(normalized),
        });
    };

    const handleActivateProfile = async (profile: AdminConnectionProfile) => {
        setSwitchingProfileId(profile.id);
        try {
            await activateConnection(profile.adminBaseUrl, {
                redirectOnSuccess: variant === "page",
                preferredProfileId: profile.id,
                localSession: variant === "page" && isLoopbackAdminUrl(profile.adminBaseUrl),
            });
        } finally {
            setSwitchingProfileId(null);
        }
    };

    const handleClearConnection = async () => {
        setClearing(true);
        setMessage(null);
        try {
            const response = await fetch("/api/connection", { method: "DELETE" });
            if (!response.ok) {
                throw new Error(t(lt("清除连接失败", "Failed to clear the connection")));
            }
            writeActiveAdminConnectionProfileId(null);
            activeProfileIdRef.current = null;
            setActiveProfileId(null);
            setConnection(null);
            setAdminBaseUrl("");
            setMessage({ type: "success", text: t(lt("当前连接已清除。", "Current connection cleared.")) });
            router.refresh();
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t(lt("清除连接失败", "Failed to clear the connection")) });
        } finally {
            setClearing(false);
        }
    };

    const handleDeleteProfile = async (profile: AdminConnectionProfile) => {
        setDeletingProfileId(profile.id);
        setMessage(null);
        try {
            const nextProfiles = removeAdminConnectionProfile(profilesRef.current, profile.id);
            profilesRef.current = nextProfiles;
            writeAdminConnectionProfiles(nextProfiles);
            setProfiles(nextProfiles);

            const currentConnectionMatches = connection?.adminBaseUrl === profile.adminBaseUrl;
            const isActiveProfile = activeProfileIdRef.current === profile.id;
            if (isActiveProfile) {
                writeActiveAdminConnectionProfileId(null);
                activeProfileIdRef.current = null;
                setActiveProfileId(null);
            }

            if (currentConnectionMatches || isActiveProfile) {
                const response = await fetch("/api/connection", { method: "DELETE" });
                if (!response.ok) {
                    throw new Error(t(lt("删除当前激活档案时，清理连接失败", "Failed to clear the active connection while deleting the profile")));
                }
                setConnection(null);
                setAdminBaseUrl("");
                router.refresh();
            }

            setMessage({ type: "success", text: t(lt(`已删除“${profile.label}”。`, `Deleted "${profile.label}".`)) });
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t(lt("删除档案失败", "Failed to delete the profile")) });
            hydrateProfiles(connection);
        } finally {
            setDeletingProfileId(null);
        }
    };

    const currentStatusLabel = loading
        ? t(lt("读取中...", "Loading..."))
        : connection?.adminBaseUrl
            ? connection.adminBaseUrl
            : t(lt("未激活", "Inactive"));
    const currentBridgeMode = loading
        ? t(lt("读取中...", "Loading..."))
        : connection?.bridgeMode === "admin_only"
            ? t(lt("仅通过管理台桥接", "Admin-only bridge"))
            : connection?.bridgeMode || t(lt("未激活", "Inactive"));
    const currentVersion = loading ? t(lt("读取中...", "Loading...")) : connection?.version || t(lt("未知", "Unknown"));

    const activeProfile = useMemo(
        () => findAdminConnectionProfileById(profiles, activeProfileId),
        [profiles, activeProfileId],
    );

    return (
        <div className="space-y-4">
            <div className="rounded-[1.75rem] border border-slate-200/80 bg-slate-50/80 px-4 py-4 text-sm shadow-sm dark:border-slate-800/80 dark:bg-slate-950/40">
                <div className="flex flex-wrap items-center gap-2 text-slate-900 dark:text-slate-100">
                    {loading ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : <CheckCircle2 className="h-4 w-4 text-emerald-600" />}
                    <span className="font-medium">{t(lt("当前连接", "Active"))}</span>
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <SummaryStat label={t(lt("管理台", "Admin"))} value={currentStatusLabel} />
                    <SummaryStat label={t(lt("桥接", "Bridge"))} value={currentBridgeMode} />
                    <SummaryStat label={t(lt("版本", "Version"))} value={currentVersion} />
                    <SummaryStat label={t(lt("档案", "Profile"))} value={activeProfile?.label || t(lt("未绑定", "Unbound"))} />
                </div>
            </div>

            <div className="space-y-2">
                <Label htmlFor={`admin-base-url-${variant}`}>{t(lt("你的管理台地址", "Your console URL"))}</Label>
                <Input
                    id={`admin-base-url-${variant}`}
                    value={adminBaseUrl}
                    onChange={(event) => setAdminBaseUrl(event.target.value)}
                    placeholder={t(lt("例如：http://127.0.0.1:9528", "Example: http://127.0.0.1:9528"))}
                    autoComplete="url"
                />
                <div className="text-xs leading-5 text-slate-500">
                    {t(lt("聊天与运行状态都走这条桥。", "Chat and runtime state use this bridge."))}
                </div>
            </div>

            {message ? (
                <div
                    className={`rounded-2xl border px-4 py-3 text-sm ${
                        message.type === "success"
                            ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/70 dark:bg-emerald-950/40 dark:text-emerald-300"
                            : "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/70 dark:bg-rose-950/40 dark:text-rose-300"
                    }`}
                >
                    {message.text}
                </div>
            ) : null}

            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:gap-3">
                <Button type="button" variant="outline" onClick={() => void handleTest()} disabled={testing || saving} className="w-full sm:w-auto">
                    {testing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ServerCrash className="mr-2 h-4 w-4" />}
                    {t(lt("测试", "Test"))}
                </Button>
                <Button type="button" onClick={() => void handleSave()} disabled={saving} className="w-full sm:w-auto">
                    {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                    {variant === "page" ? t(lt("保存并继续", "Save & continue")) : t(lt("保存连接", "Save"))}
                </Button>
                <Button type="button" variant="ghost" onClick={() => void handleClearConnection()} disabled={clearing || !connection} className="w-full sm:w-auto">
                    {clearing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                    {t(lt("清除", "Clear"))}
                </Button>
                <Button type="button" variant="ghost" onClick={() => void loadConnection()} disabled={loading} className="w-full sm:w-auto">
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}
                    {t(lt("刷新", "Refresh"))}
                </Button>
            </div>

            <div className="space-y-3 rounded-[1.75rem] border border-slate-200/80 bg-white/75 px-4 py-4 dark:border-slate-800/80 dark:bg-slate-950/30">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-900 dark:text-slate-100">
                    <PlugZap className="h-4 w-4 text-primary" />
                    {t(lt("已保存连接", "Saved"))}
                </div>
                {profiles.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-5 text-sm text-slate-500 dark:border-slate-800">
                        {t(lt("保存一次后可快速切换。", "Save once to switch later."))}
                    </div>
                ) : (
                    <div className="space-y-3">
                        {profiles.map((profile) => {
                            const isActive = activeProfileId === profile.id;
                            const isCurrent = connection?.adminBaseUrl === profile.adminBaseUrl;
                            const busy = switchingProfileId === profile.id || deletingProfileId === profile.id;
                            return (
                                <div
                                    key={profile.id}
                                    className={`rounded-2xl border px-4 py-4 ${
                                        isActive
                                            ? "border-primary/30 bg-primary/5"
                                            : "border-slate-200 bg-slate-50/70 dark:border-slate-800 dark:bg-slate-950/30"
                                    }`}
                                >
                                    <div className="flex flex-wrap items-start justify-between gap-3">
                                        <div className="space-y-1">
                                            <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">{profile.label}</div>
                                            <div className="text-xs break-all text-slate-500">{profile.adminBaseUrl}</div>
                                            <div className="text-[11px] text-slate-500">
                                                {t(lt("上次使用", "Last used"))}: {formatLastUsed(profile.lastUsedAt, locale)} · {t(lt("版本", "Version"))}: {profile.version || t(lt("未知", "Unknown"))}
                                            </div>
                                        </div>
                                        <div className="flex flex-wrap gap-2">
                                            {isCurrent ? (
                                                <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
                                                    {t(lt("当前", "Current"))}
                                                </span>
                                            ) : null}
                                            {isActive && !isCurrent ? (
                                                <span className="rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                                                    {t(lt("默认", "Default"))}
                                                </span>
                                            ) : null}
                                        </div>
                                    </div>
                                    <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                                        <Button
                                            type="button"
                                            size="sm"
                                            variant={isCurrent ? "outline" : "default"}
                                            onClick={() => void handleActivateProfile(profile)}
                                            disabled={busy}
                                            className="w-full sm:w-auto"
                                        >
                                            {switchingProfileId === profile.id ? (
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            ) : null}
                                            {isCurrent ? t(lt("重连", "Reconnect")) : t(lt("使用", "Use"))}
                                        </Button>
                                        <Button
                                            type="button"
                                            size="sm"
                                            variant="ghost"
                                            onClick={() => void handleDeleteProfile(profile)}
                                            disabled={busy}
                                            className="w-full sm:w-auto"
                                        >
                                            {deletingProfileId === profile.id ? (
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            ) : null}
                                            {t(lt("删除", "Delete"))}
                                        </Button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
}
