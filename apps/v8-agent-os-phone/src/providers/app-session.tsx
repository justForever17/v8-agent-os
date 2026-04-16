import React from "react";
import { Platform } from "react-native";

import {
    buildAdminApiUrl,
    normalizeAdminBaseUrl,
    parseJsonSafe,
    streamSse,
    streamSseWithXmlHttpRequest,
} from "@/src/lib/admin-client";
import {
    readAdminConnectionProfiles,
    upsertAdminConnectionProfile,
    writeActiveAdminConnectionProfileId,
    writeAdminConnectionProfiles,
} from "@/src/lib/admin-connection-profiles";
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
    authorizedRealtimeStream: (
        path: string,
        onEvent: (eventName: string, payload: unknown) => void,
        signal?: AbortSignal,
    ) => Promise<void>;
};

type MobileAuthPayload = {
    accessToken: string;
    refreshToken: string;
    user: PhoneUser;
};

const MIN_BOOTING_SCREEN_MS = 900;

function getBrowserAdminFallbackBaseUrls(currentBaseUrl: string) {
    if (Platform.OS !== "web" || typeof window === "undefined") {
        return [];
    }
    const protocol = window.location.protocol === "https:" ? "https:" : "http:";
    const hostname = window.location.hostname || "127.0.0.1";
    const normalizedCurrent = normalizeAdminBaseUrl(currentBaseUrl);
    return [
        `${protocol}//${hostname}:9528`,
        `${protocol}//127.0.0.1:9528`,
        `${protocol}//localhost:9528`,
    ]
        .map((candidate) => normalizeAdminBaseUrl(candidate))
        .filter((candidate, index, all) => Boolean(candidate) && candidate !== normalizedCurrent && all.indexOf(candidate) === index);
}

function getPreferredBrowserAdminBaseUrls(currentBaseUrl: string) {
    const normalizedCurrent = normalizeAdminBaseUrl(currentBaseUrl);
    if (Platform.OS !== "web" || typeof window === "undefined") {
        return normalizedCurrent ? [normalizedCurrent] : [];
    }

    const browserLocalHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
    let shouldPreferBrowserLocal = false;
    try {
        const currentUrl = new URL(normalizedCurrent);
        shouldPreferBrowserLocal = browserLocalHosts.has(window.location.hostname || "")
            && !browserLocalHosts.has(currentUrl.hostname || "");
    } catch {
        shouldPreferBrowserLocal = browserLocalHosts.has(window.location.hostname || "");
    }

    const browserFallbacks = getBrowserAdminFallbackBaseUrls(normalizedCurrent);
    const candidates = shouldPreferBrowserLocal
        ? [...browserFallbacks, normalizedCurrent]
        : [normalizedCurrent, ...browserFallbacks];

    return candidates.filter((candidate, index, all) => Boolean(candidate) && all.indexOf(candidate) === index);
}

async function readAuthPayload(response: Response) {
    return parseJsonSafe<MobileAuthPayload & { error?: string }>(response);
}

const SessionContext = React.createContext<SessionContextValue | null>(null);

async function persistSession(baseUrl: string, payload: MobileAuthPayload) {
    const profiles = await readAdminConnectionProfiles();
    const { profile, profiles: nextProfiles } = upsertAdminConnectionProfile(profiles, { adminBaseUrl: baseUrl });
    await Promise.all([
        setStoredValue("adminBaseUrl", baseUrl),
        setStoredValue("accessToken", payload.accessToken),
        setStoredValue("refreshToken", payload.refreshToken),
        setStoredValue("user", JSON.stringify(payload.user)),
        writeAdminConnectionProfiles(nextProfiles),
        writeActiveAdminConnectionProfileId(profile?.id || null),
    ]);
}

export function AppSessionProvider({ children }: { children: React.ReactNode }) {
    const [status, setStatus] = React.useState<SessionStatus>("booting");
    const [adminBaseUrl, setAdminBaseUrlState] = React.useState("");
    const [accessToken, setAccessToken] = React.useState("");
    const [refreshToken, setRefreshToken] = React.useState("");
    const [user, setUser] = React.useState<PhoneUser | null>(null);
    const [activeConversationId, setActiveConversationIdState] = React.useState<string | null>(null);
    const bootStartedAtRef = React.useRef(Date.now());
    const statusRef = React.useRef<SessionStatus>("booting");

    React.useEffect(() => {
        statusRef.current = status;
    }, [status]);

    const awaitMinimumBootScreen = React.useCallback(async () => {
        if (statusRef.current !== "booting") {
            return;
        }
        const elapsed = Date.now() - bootStartedAtRef.current;
        const remaining = MIN_BOOTING_SCREEN_MS - elapsed;
        if (remaining > 0) {
            await new Promise((resolve) => setTimeout(resolve, remaining));
        }
    }, []);

    const refreshSessionWithBaseUrl = React.useCallback(async (baseUrlInput: string, refreshTokenInput: string) => {
        const baseUrl = normalizeAdminBaseUrl(baseUrlInput);
        if (!baseUrl || !refreshTokenInput) {
            return false;
        }

        const candidateBaseUrls = getPreferredBrowserAdminBaseUrls(baseUrl);
        for (const candidateBaseUrl of candidateBaseUrls) {
            try {
                const response = await fetch(buildAdminApiUrl(candidateBaseUrl, "/api/client/auth/refresh"), {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        refreshToken: refreshTokenInput,
                        deviceName: `v8-phone-${Platform.OS}`,
                    }),
                });
                if (!response.ok) {
                    continue;
                }

                const payload = await parseJsonSafe<MobileAuthPayload>(response);
                if (!payload?.accessToken || !payload?.refreshToken || !payload.user) {
                    continue;
                }

                setAdminBaseUrlState(candidateBaseUrl);
                setAccessToken(payload.accessToken);
                setRefreshToken(payload.refreshToken);
                setUser(payload.user);
                await awaitMinimumBootScreen();
                setStatus("authenticated");
                await persistSession(candidateBaseUrl, payload);
                return true;
            } catch {
                // Try next browser-reachable candidate.
            }
        }
        return false;
    }, [awaitMinimumBootScreen]);

    const refreshSession = React.useCallback(async () => {
        return refreshSessionWithBaseUrl(adminBaseUrl, refreshToken);
    }, [adminBaseUrl, refreshToken, refreshSessionWithBaseUrl]);

    const hydrate = React.useCallback(async () => {
        const [storedBaseUrl, storedAccessToken, storedRefreshToken, storedUser, storedConversationId] = await Promise.all([
            getStoredValue("adminBaseUrl"),
            getStoredValue("accessToken"),
            getStoredValue("refreshToken"),
            getStoredValue("user"),
            getStoredValue("activeConversationId"),
        ]);

        const normalizedBaseUrl = normalizeAdminBaseUrl(storedBaseUrl || "");
        const preferredBaseUrl = getPreferredBrowserAdminBaseUrls(normalizedBaseUrl)[0] || normalizedBaseUrl;
        setAdminBaseUrlState(preferredBaseUrl);
        setAccessToken(storedAccessToken || "");
        setRefreshToken(storedRefreshToken || "");
        setActiveConversationIdState(storedConversationId || null);
        if (preferredBaseUrl && preferredBaseUrl !== normalizedBaseUrl) {
            await setStoredValue("adminBaseUrl", preferredBaseUrl);
        }

        if (storedUser) {
            try {
                setUser(JSON.parse(storedUser) as PhoneUser);
            } catch {
                setUser(null);
            }
        }

        if (!preferredBaseUrl || !storedAccessToken) {
            await awaitMinimumBootScreen();
            setStatus("anonymous");
            return;
        }

        try {
            const candidateBaseUrls = getPreferredBrowserAdminBaseUrls(preferredBaseUrl);
            for (const candidateBaseUrl of candidateBaseUrls) {
                try {
                    const meResponse = await fetch(buildAdminApiUrl(candidateBaseUrl, "/api/client/auth/me"), {
                        headers: { Authorization: `Bearer ${storedAccessToken}` },
                    });
                    if (!meResponse.ok) {
                        continue;
                    }
                    const mePayload = await parseJsonSafe<{ user: PhoneUser }>(meResponse);
                    if (mePayload?.user) {
                        setAdminBaseUrlState(candidateBaseUrl);
                        setUser(mePayload.user);
                        await awaitMinimumBootScreen();
                        setStatus("authenticated");
                        if (candidateBaseUrl !== preferredBaseUrl) {
                            await setStoredValue("adminBaseUrl", candidateBaseUrl);
                        }
                        return;
                    }
                } catch {
                    // Try next candidate.
                }
            }
            const refreshed = await refreshSessionWithBaseUrl(preferredBaseUrl, storedRefreshToken || "");
            if (!refreshed) {
                await clearSessionStorage();
                setAccessToken("");
                setRefreshToken("");
                setUser(null);
                setActiveConversationIdState(null);
                await awaitMinimumBootScreen();
                setStatus("anonymous");
            }
        } catch {
            await awaitMinimumBootScreen();
            setStatus("anonymous");
        }
    }, [awaitMinimumBootScreen, refreshSessionWithBaseUrl]);

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
        const candidateBaseUrls = getPreferredBrowserAdminBaseUrls(nextBaseUrl);
        let lastError = "登录失败";

        for (const candidateBaseUrl of candidateBaseUrls) {
            try {
                const response = await fetch(buildAdminApiUrl(candidateBaseUrl, "/api/client/auth/login"), {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        login,
                        password,
                        deviceName: `v8-phone-${Platform.OS}`,
                    }),
                });

                const payload = await readAuthPayload(response);
                if (!response.ok || !payload?.accessToken || !payload?.refreshToken || !payload.user) {
                    lastError = payload?.error || "登录失败";
                    continue;
                }

                setAdminBaseUrlState(candidateBaseUrl);
                setAccessToken(payload.accessToken);
                setRefreshToken(payload.refreshToken);
                setUser(payload.user);
                setStatus("authenticated");
                await persistSession(candidateBaseUrl, payload);
                return;
            } catch (error) {
                lastError = error instanceof Error ? error.message : "登录失败";
            }
        }

        throw new Error(lastError);
    }, []);

    const signUp = React.useCallback(async (input: RegisterInput) => {
        const candidateBaseUrls = getPreferredBrowserAdminBaseUrls(input.adminBaseUrl);
        let lastError = "注册失败";

        for (const candidateBaseUrl of candidateBaseUrls) {
            try {
                const payload = await registerPhoneUser(candidateBaseUrl, {
                    ...input,
                    adminBaseUrl: candidateBaseUrl,
                });
                setAdminBaseUrlState(candidateBaseUrl);
                setAccessToken(payload.accessToken);
                setRefreshToken(payload.refreshToken);
                setUser(payload.user);
                setStatus("authenticated");
                await persistSession(candidateBaseUrl, payload);
                return;
            } catch (error) {
                lastError = error instanceof Error ? error.message : "注册失败";
            }
        }

        throw new Error(lastError);
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

    const performAuthorizedRequest = React.useCallback(async (
        path: string,
        init?: RequestInit,
    ) => {
        const baseUrl = normalizeAdminBaseUrl(adminBaseUrl);
        if (!baseUrl || !accessToken) {
            throw new Error("当前尚未连接到 Admin");
        }

        const doFetch = async (token: string, targetBaseUrl: string) =>
            fetch(buildAdminApiUrl(targetBaseUrl, path), {
                ...init,
                headers: {
                    ...(init?.headers || {}),
                    Authorization: `Bearer ${token}`,
                },
            }) as Promise<Response>;
        const attemptFetch = async (token: string) => {
            const candidates = getPreferredBrowserAdminBaseUrls(baseUrl);
            let lastError: unknown = null;
            for (const candidateBaseUrl of candidates) {
                try {
                    const response = await doFetch(token, candidateBaseUrl);
                    if (candidateBaseUrl !== baseUrl) {
                        setAdminBaseUrlState(candidateBaseUrl);
                        await setStoredValue("adminBaseUrl", candidateBaseUrl);
                    }
                    return response;
                } catch (error) {
                    lastError = error;
                }
            }
            throw lastError instanceof Error ? new Error(`无法连接 Admin：${baseUrl}`) : new Error("无法连接 Admin");
        };

        let response = await attemptFetch(accessToken);
        if (response.status !== 401) {
            return response;
        }

        const refreshed = await refreshSession();
        if (!refreshed) {
            await signOut();
            throw new Error("登录状态已失效，请重新登录");
        }

        const nextAccessToken = (await getStoredValue("accessToken")) || accessToken;
        response = await attemptFetch(nextAccessToken);
        return response;
    }, [accessToken, adminBaseUrl, refreshSession, signOut]);

    const authorizedFetch = React.useCallback((path: string, init?: RequestInit) => {
        return performAuthorizedRequest(path, init);
    }, [performAuthorizedRequest]);

    const authorizedRealtimeStream = React.useCallback(async (
        path: string,
        onEvent: (eventName: string, payload: unknown) => void,
        signal?: AbortSignal,
    ) => {
        const baseUrl = normalizeAdminBaseUrl(adminBaseUrl);
        if (!baseUrl || !accessToken) {
            throw new Error("当前尚未连接到 Admin");
        }

        const candidateBaseUrls = getPreferredBrowserAdminBaseUrls(baseUrl);

        const openStream = async (token: string) => {
            let lastError: unknown = null;
            for (const candidateBaseUrl of candidateBaseUrls) {
                try {
                    if (Platform.OS === "web") {
                        const response = await fetch(buildAdminApiUrl(candidateBaseUrl, path), {
                            method: "GET",
                            headers: {
                                Authorization: `Bearer ${token}`,
                                Accept: "text/event-stream",
                            },
                            signal,
                        });
                        if (!response.ok) {
                            const error = new Error(`连接实时流失败（${response.status}）`) as Error & { status?: number };
                            error.status = response.status;
                            throw error;
                        }
                        await streamSse(response, onEvent);
                    } else {
                        await streamSseWithXmlHttpRequest({
                            url: buildAdminApiUrl(candidateBaseUrl, path),
                            headers: {
                                Authorization: `Bearer ${token}`,
                                Accept: "text/event-stream",
                            },
                            signal,
                            onEvent,
                        });
                    }
                    if (candidateBaseUrl !== baseUrl) {
                        setAdminBaseUrlState(candidateBaseUrl);
                        await setStoredValue("adminBaseUrl", candidateBaseUrl);
                    }
                    return;
                } catch (error) {
                    lastError = error;
                    const status = typeof error === "object" && error && "status" in error
                        ? Number((error as { status?: number }).status || 0)
                        : 0;
                    if (status === 401) {
                        throw error;
                    }
                }
            }
            throw lastError instanceof Error ? lastError : new Error(`无法连接 Admin：${baseUrl}`);
        };

        try {
            await openStream(accessToken);
            return;
        } catch (error) {
            const status = typeof error === "object" && error && "status" in error
                ? Number((error as { status?: number }).status || 0)
                : 0;
            if (status !== 401) {
                throw error instanceof Error ? error : new Error("实时流连接失败");
            }
        }

        const refreshed = await refreshSession();
        if (!refreshed) {
            await signOut();
            throw new Error("登录状态已失效，请重新登录");
        }

        const nextAccessToken = (await getStoredValue("accessToken")) || accessToken;
        await openStream(nextAccessToken);
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
        authorizedRealtimeStream,
    }), [status, user, adminBaseUrl, accessToken, activeConversationId, setAdminBaseUrl, setActiveConversationId, signIn, signUp, signOut, refreshUser, updateCurrentUser, authorizedFetch, authorizedRealtimeStream]);

    return <SessionContext.Provider value={contextValue}>{children}</SessionContext.Provider>;
}

export function useAppSession() {
    const context = React.useContext(SessionContext);
    if (!context) {
        throw new Error("useAppSession 必须在 AppSessionProvider 内使用");
    }
    return context;
}
