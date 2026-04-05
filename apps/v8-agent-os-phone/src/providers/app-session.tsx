import React from "react";
import { Platform } from "react-native";

import { buildAdminApiUrl, normalizeAdminBaseUrl, parseJsonSafe } from "@/src/lib/admin-client";
import { clearSessionStorage, getStoredValue, removeStoredValue, setStoredValue } from "@/src/lib/mobile-storage";
import { signUp as registerPhoneUser } from "@/src/lib/phone-api";
import type { PhoneUser, RegisterInput } from "@/src/types/admin";

type SessionStatus = "booting" | "anonymous" | "authenticated";

type LoginInput = {
    adminBaseUrl: string;
    login: string;
    password: string;
};

type SessionContextValue = {
    status: SessionStatus;
    user: PhoneUser | null;
    adminBaseUrl: string;
    accessToken: string;
    activeConversationId: string | null;
    setAdminBaseUrl: (next: string) => Promise<void>;
    setActiveConversationId: (next: string | null) => Promise<void>;
    signIn: (input: LoginInput) => Promise<void>;
    signUp: (input: RegisterInput) => Promise<void>;
    signOut: () => Promise<void>;
    refreshUser: () => Promise<PhoneUser | null>;
    updateCurrentUser: (next: PhoneUser | null) => Promise<void>;
    authorizedFetch: (path: string, init?: RequestInit) => Promise<Response>;
};

type MobileAuthPayload = {
    accessToken: string;
    refreshToken: string;
    user: PhoneUser;
};

const SessionContext = React.createContext<SessionContextValue | null>(null);

async function persistSession(baseUrl: string, payload: MobileAuthPayload) {
    await Promise.all([
        setStoredValue("adminBaseUrl", baseUrl),
        setStoredValue("accessToken", payload.accessToken),
        setStoredValue("refreshToken", payload.refreshToken),
        setStoredValue("user", JSON.stringify(payload.user)),
    ]);
}

export function AppSessionProvider({ children }: { children: React.ReactNode }) {
    const [status, setStatus] = React.useState<SessionStatus>("booting");
    const [adminBaseUrl, setAdminBaseUrlState] = React.useState("");
    const [accessToken, setAccessToken] = React.useState("");
    const [refreshToken, setRefreshToken] = React.useState("");
    const [user, setUser] = React.useState<PhoneUser | null>(null);
    const [activeConversationId, setActiveConversationIdState] = React.useState<string | null>(null);

    const refreshSession = React.useCallback(async () => {
        const baseUrl = normalizeAdminBaseUrl(adminBaseUrl);
        if (!baseUrl || !refreshToken) {
            return false;
        }

        const response = await fetch(buildAdminApiUrl(baseUrl, "/api/client/auth/refresh"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                refreshToken,
                deviceName: `v8-phone-${Platform.OS}`,
            }),
        });
        if (!response.ok) {
            return false;
        }

        const payload = await parseJsonSafe<MobileAuthPayload>(response);
        if (!payload?.accessToken || !payload?.refreshToken || !payload.user) {
            return false;
        }

        setAccessToken(payload.accessToken);
        setRefreshToken(payload.refreshToken);
        setUser(payload.user);
        setStatus("authenticated");
        await persistSession(baseUrl, payload);
        return true;
    }, [adminBaseUrl, refreshToken]);

    const hydrate = React.useCallback(async () => {
        const [storedBaseUrl, storedAccessToken, storedRefreshToken, storedUser, storedConversationId] = await Promise.all([
            getStoredValue("adminBaseUrl"),
            getStoredValue("accessToken"),
            getStoredValue("refreshToken"),
            getStoredValue("user"),
            getStoredValue("activeConversationId"),
        ]);

        const normalizedBaseUrl = normalizeAdminBaseUrl(storedBaseUrl || "");
        setAdminBaseUrlState(normalizedBaseUrl);
        setAccessToken(storedAccessToken || "");
        setRefreshToken(storedRefreshToken || "");
        setActiveConversationIdState(storedConversationId || null);

        if (storedUser) {
            try {
                setUser(JSON.parse(storedUser) as PhoneUser);
            } catch {
                setUser(null);
            }
        }

        if (!normalizedBaseUrl || !storedAccessToken) {
            setStatus("anonymous");
            return;
        }

        try {
            const meResponse = await fetch(buildAdminApiUrl(normalizedBaseUrl, "/api/client/auth/me"), {
                headers: { Authorization: `Bearer ${storedAccessToken}` },
            });
            if (meResponse.ok) {
                const mePayload = await parseJsonSafe<{ user: PhoneUser }>(meResponse);
                if (mePayload?.user) {
                    setUser(mePayload.user);
                    setStatus("authenticated");
                    return;
                }
            }
            const refreshed = await refreshSession();
            if (!refreshed) {
                await clearSessionStorage();
                setAccessToken("");
                setRefreshToken("");
                setUser(null);
                setActiveConversationIdState(null);
                setStatus("anonymous");
            }
        } catch {
            setStatus("anonymous");
        }
    }, [refreshSession]);

    React.useEffect(() => {
        void hydrate();
    }, [hydrate]);

    const setAdminBaseUrl = React.useCallback(async (next: string) => {
        const normalized = normalizeAdminBaseUrl(next);
        setAdminBaseUrlState(normalized);
        if (normalized) {
            await setStoredValue("adminBaseUrl", normalized);
        } else {
            await removeStoredValue("adminBaseUrl");
        }
    }, []);

    const setActiveConversationId = React.useCallback(async (next: string | null) => {
        setActiveConversationIdState(next);
        if (next) {
            await setStoredValue("activeConversationId", next);
        } else {
            await removeStoredValue("activeConversationId");
        }
    }, []);

    const signIn = React.useCallback(async ({ adminBaseUrl: nextBaseUrl, login, password }: LoginInput) => {
        const normalizedBaseUrl = normalizeAdminBaseUrl(nextBaseUrl);
        const response = await fetch(buildAdminApiUrl(normalizedBaseUrl, "/api/client/auth/login"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                login,
                password,
                deviceName: `v8-phone-${Platform.OS}`,
            }),
        });

        const payload = await parseJsonSafe<MobileAuthPayload & { error?: string }>(response);
        if (!response.ok || !payload?.accessToken || !payload?.refreshToken || !payload.user) {
            throw new Error(payload?.error || "登录失败");
        }

        setAdminBaseUrlState(normalizedBaseUrl);
        setAccessToken(payload.accessToken);
        setRefreshToken(payload.refreshToken);
        setUser(payload.user);
        setStatus("authenticated");
        await persistSession(normalizedBaseUrl, payload);
    }, []);

    const signUp = React.useCallback(async (input: RegisterInput) => {
        const normalizedBaseUrl = normalizeAdminBaseUrl(input.adminBaseUrl);
        const payload = await registerPhoneUser(normalizedBaseUrl, input);
        setAdminBaseUrlState(normalizedBaseUrl);
        setAccessToken(payload.accessToken);
        setRefreshToken(payload.refreshToken);
        setUser(payload.user);
        setStatus("authenticated");
        await persistSession(normalizedBaseUrl, payload);
    }, []);

    const signOut = React.useCallback(async () => {
        const baseUrl = normalizeAdminBaseUrl(adminBaseUrl);
        if (baseUrl && refreshToken) {
            try {
                await fetch(buildAdminApiUrl(baseUrl, "/api/client/auth/logout"), {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ refreshToken }),
                });
            } catch {
                // Best-effort logout.
            }
        }
        await clearSessionStorage();
        setAccessToken("");
        setRefreshToken("");
        setUser(null);
        setActiveConversationIdState(null);
        setStatus("anonymous");
    }, [adminBaseUrl, refreshToken]);

    const refreshUser = React.useCallback(async () => {
        const baseUrl = normalizeAdminBaseUrl(adminBaseUrl);
        if (!baseUrl || !accessToken) {
            return null;
        }
        const response = await fetch(buildAdminApiUrl(baseUrl, "/api/client/auth/me"), {
            headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (!response.ok) {
            return null;
        }
        const payload = await parseJsonSafe<{ user?: PhoneUser }>(response);
        if (!payload?.user) {
            return null;
        }
        setUser(payload.user);
        await setStoredValue("user", JSON.stringify(payload.user));
        return payload.user;
    }, [accessToken, adminBaseUrl]);

    const updateCurrentUser = React.useCallback(async (next: PhoneUser | null) => {
        setUser(next);
        if (next) {
            await setStoredValue("user", JSON.stringify(next));
        } else {
            await removeStoredValue("user");
        }
    }, []);

    const authorizedFetch = React.useCallback(async (path: string, init?: RequestInit) => {
        const baseUrl = normalizeAdminBaseUrl(adminBaseUrl);
        if (!baseUrl || !accessToken) {
            throw new Error("当前尚未连接到 Admin");
        }

        const doFetch = async (token: string) =>
            fetch(buildAdminApiUrl(baseUrl, path), {
                ...init,
                headers: {
                    ...(init?.headers || {}),
                    Authorization: `Bearer ${token}`,
                },
            });

        let response = await doFetch(accessToken);
        if (response.status !== 401) {
            return response;
        }

        const refreshed = await refreshSession();
        if (!refreshed) {
            await signOut();
            throw new Error("登录状态已失效，请重新登录");
        }

        const nextAccessToken = (await getStoredValue("accessToken")) || accessToken;
        response = await doFetch(nextAccessToken);
        return response;
    }, [accessToken, adminBaseUrl, refreshSession, signOut]);

    const contextValue = React.useMemo<SessionContextValue>(() => ({
        status,
        user,
        adminBaseUrl,
        accessToken,
        activeConversationId,
        setAdminBaseUrl,
        setActiveConversationId,
        signIn,
        signUp,
        signOut,
        refreshUser,
        updateCurrentUser,
        authorizedFetch,
    }), [status, user, adminBaseUrl, accessToken, activeConversationId, setAdminBaseUrl, setActiveConversationId, signIn, signUp, signOut, refreshUser, updateCurrentUser, authorizedFetch]);

    return <SessionContext.Provider value={contextValue}>{children}</SessionContext.Provider>;
}

export function useAppSession() {
    const context = React.useContext(SessionContext);
    if (!context) {
        throw new Error("useAppSession 必须在 AppSessionProvider 内使用");
    }
    return context;
}
