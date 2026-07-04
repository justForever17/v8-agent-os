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
import { Locale, resolveText } from "@/lib/locale";
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
    if (!value) return resolveText(locale, "web.generated.5e1302f90c");
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return resolveText(locale, "web.generated.5e1302f90c");
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
                throw new Error(payload.error || t("web.generated.db20ef6c78"));
            }

            persistActivatedConnection(payload.connection, options?.preferredProfileId || null);

            if (options?.localSession) {
                const result = await signIn("credentials", {
                    localSession: "1",
                    adminBaseUrl: payload.connection.adminBaseUrl,
                    redirect: false,
                });
                if (!result?.ok || result.error) {
                    throw new Error(t("web.generated.3780776128"));
                }
            }

            if (!options?.silent) {
                setMessage({ type: "success", text: t("web.generated.ad865710b7") });
            }

            if (options?.redirectOnSuccess) {
                router.replace(nextPath);
                router.refresh();
            } else {
                router.refresh();
            }

            return payload.connection;
        } catch (error) {
            const text = error instanceof Error ? error.message : t("web.generated.7010fef2f5");
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
            setMessage({ type: "error", text: t("web.generated.08057577f8") });
        });
    }, [activateConnection, activeProfileId, autoRestore, connection, loading, profiles, t, variant]);

    const handleTest = async () => {
        const normalized = adminBaseUrl.trim();
        if (!normalized) {
            setMessage({ type: "error", text: t("web.generated.1927a83e79") });
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
                throw new Error(payload.error || t("web.generated.db20ef6c78"));
            }
            setMessage({ type: "success", text: t("web.generated.d6006e54fb") });
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t("web.generated.7010fef2f5") });
        } finally {
            setTesting(false);
        }
    };

    const handleSave = async () => {
        const normalized = adminBaseUrl.trim();
        if (!normalized) {
            setMessage({ type: "error", text: t("web.generated.1927a83e79") });
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
                throw new Error(t("web.generated.a396c7cff3"));
            }
            writeActiveAdminConnectionProfileId(null);
            activeProfileIdRef.current = null;
            setActiveProfileId(null);
            setConnection(null);
            setAdminBaseUrl("");
            setMessage({ type: "success", text: t("web.generated.e2fd190ce0") });
            router.refresh();
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t("web.generated.a396c7cff3") });
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
                    throw new Error(t("web.generated.53995188e6"));
                }
                setConnection(null);
                setAdminBaseUrl("");
                router.refresh();
            }

            setMessage({ type: "success", text: t("web.generated.e911649f15", { value0: profile.label }) });
        } catch (error) {
            setMessage({ type: "error", text: error instanceof Error ? error.message : t("web.generated.c6d0b31763") });
            hydrateProfiles(connection);
        } finally {
            setDeletingProfileId(null);
        }
    };

    const currentStatusLabel = loading
        ? t("web.generated.bd18a8845f")
        : connection?.adminBaseUrl
            ? connection.adminBaseUrl
            : t("web.generated.402ad6c058");
    const currentBridgeMode = loading
        ? t("web.generated.bd18a8845f")
        : connection?.bridgeMode === "admin_only"
            ? t("web.generated.5e67456515")
            : connection?.bridgeMode || t("web.generated.402ad6c058");
    const currentVersion = loading ? t("web.generated.bd18a8845f") : connection?.version || t("web.generated.5e1302f90c");

    const activeProfile = useMemo(
        () => findAdminConnectionProfileById(profiles, activeProfileId),
        [profiles, activeProfileId],
    );

    return (
        <div className="space-y-4">
            <div className="rounded-[1.75rem] border border-slate-200/80 bg-slate-50/80 px-4 py-4 text-sm shadow-sm dark:border-slate-800/80 dark:bg-slate-950/40">
                <div className="flex flex-wrap items-center gap-2 text-slate-900 dark:text-slate-100">
                    {loading ? <Loader2 className="h-4 w-4 animate-spin text-slate-400" /> : <CheckCircle2 className="h-4 w-4 text-emerald-600" />}
                    <span className="font-medium">{t("web.generated.f58aacdbd9")}</span>
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <SummaryStat label={t("web.generated.b5a479b63a")} value={currentStatusLabel} />
                    <SummaryStat label={t("web.generated.10e64b5869")} value={currentBridgeMode} />
                    <SummaryStat label={t("web.generated.6145b7bb9a")} value={currentVersion} />
                    <SummaryStat label={t("web.generated.af37af76f9")} value={activeProfile?.label || t("web.generated.b4f93aa760")} />
                </div>
            </div>

            <div className="space-y-2">
                <Label htmlFor={`admin-base-url-${variant}`}>{t("web.generated.f875b5a988")}</Label>
                <Input
                    id={`admin-base-url-${variant}`}
                    value={adminBaseUrl}
                    onChange={(event) => setAdminBaseUrl(event.target.value)}
                    placeholder={t("web.generated.7cc920cdd4")}
                    autoComplete="url"
                />
                <div className="text-xs leading-5 text-slate-500">
                    {t("web.generated.5d72a08774")}
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
                    {t("web.generated.d2d526c419")}
                </Button>
                <Button type="button" onClick={() => void handleSave()} disabled={saving} className="w-full sm:w-auto">
                    {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                    {variant === "page" ? t("web.generated.cc75867d3b") : t("web.generated.a7089afeb1")}
                </Button>
                <Button type="button" variant="ghost" onClick={() => void handleClearConnection()} disabled={clearing || !connection} className="w-full sm:w-auto">
                    {clearing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                    {t("web.generated.7164bdadb3")}
                </Button>
                <Button type="button" variant="ghost" onClick={() => void loadConnection()} disabled={loading} className="w-full sm:w-auto">
                    {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-2 h-4 w-4" />}
                    {t("web.generated.140abb8251")}
                </Button>
            </div>

            <div className="space-y-3 rounded-[1.75rem] border border-slate-200/80 bg-white/75 px-4 py-4 dark:border-slate-800/80 dark:bg-slate-950/30">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-900 dark:text-slate-100">
                    <PlugZap className="h-4 w-4 text-primary" />
                    {t("web.generated.b9d97e2b1a")}
                </div>
                {profiles.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-5 text-sm text-slate-500 dark:border-slate-800">
                        {t("web.generated.99c9b7121d")}
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
                                                {t("web.generated.b65427d514")}: {formatLastUsed(profile.lastUsedAt, locale)} · {t("web.generated.6145b7bb9a")}: {profile.version || t("web.generated.5e1302f90c")}
                                            </div>
                                        </div>
                                        <div className="flex flex-wrap gap-2">
                                            {isCurrent ? (
                                                <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
                                                    {t("web.generated.0bcb8dacb7")}
                                                </span>
                                            ) : null}
                                            {isActive && !isCurrent ? (
                                                <span className="rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                                                    {t("web.generated.58cd596ffa")}
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
                                            {isCurrent ? t("web.generated.d1ee39bd85") : t("web.generated.15ec7a3f90")}
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
                                            {t("web.generated.6cba6a2c08")}
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
