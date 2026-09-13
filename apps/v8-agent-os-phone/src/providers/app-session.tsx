import React from "react";
import { AppState, Platform, Pressable, Text, View } from "react-native";
import { buildAdminApiUrl, normalizeAdminBaseUrl, parseJsonSafe, resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { type AdminConnectionProfile, type ProfileCredentials, orderAdminBaseUrlCandidates,
    readActiveAdminConnectionProfileId, readAdminConnectionProfiles, readProfileCredentials,
    forgetProfileCredentials, upsertAdminConnectionProfile, writeActiveAdminConnectionProfileId,
    writeAdminConnectionProfiles } from "@/src/lib/admin-connection-profiles";
import { getEngineNowMs as resolveEngineNowMs, toEngineClockOffsetMs } from "@/src/lib/engine-time";
import { clearSessionStorage, getStoredValue, readMetadata, writeMetadata } from "@/src/lib/mobile-storage";
import { cacheProfileAvatar } from "@/src/lib/profile-avatar-cache";
import { cacheProfileBackground } from "@/src/lib/profile-background-cache";
import { pairDevice as consumeDevicePairing, parseDevicePairingUri } from "@/src/lib/phone-api";
import { phoneAuthorityKey } from "@/src/lib/phone-identity";
import { PhoneTransport, abortError } from "@/src/lib/phone-transport";
import { phoneDrafts } from "@/src/lib/phone-drafts";
import type { DevicePairingInput, PhoneUser } from "@/src/types/admin";

type SessionStatus = "booting" | "anonymous" | "authenticated";
type SessionContextValue = {
    status: SessionStatus; user: PhoneUser | null; userAvatarUri: string; userBackgroundUri: string;
    userBackgroundMediaType: "image" | "video"; adminBaseUrl: string; accessToken: string;
    authorityKey: string; servingInstanceId: string; activeProfileId: string; newDraftId: string;
    activeConversationId: string | null; sessionActivityVersion: number; connectionError: string;
    setAdminBaseUrl: (next: string) => Promise<void>;
    setActiveConversationId: (next: string | null) => Promise<void>;
    createNewDraft: () => Promise<void>;
    activateProfile: (profileId: string) => Promise<void>;
    pairDevice: (input: DevicePairingInput) => Promise<void>;
    signOut: () => Promise<void>;
    refreshUser: () => Promise<PhoneUser | null>;
    updateCurrentUser: (next: PhoneUser | null) => Promise<void>;
    authorizedFetch: (path: string, init?: RequestInit) => Promise<Response>;
    authorizedRealtimeStream: (path: string, onEvent: (name: string, payload: unknown) => void, signal?: AbortSignal) => Promise<void>;
    engineClockOffsetMs: number; getEngineNowMs: () => number;
};
type ActiveSession = { profile: AdminConnectionProfile; credentials: ProfileCredentials; authorityKey: string;
    transport: PhoneTransport; conversationId: string | null; draftId: string };
const SessionContext = React.createContext<SessionContextValue | null>(null);
const newDraftId = () => `draft-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const viewKey = (authority: string) => `v8.phone.view.v2.${authority}`;
const unavailableFetch = async (): Promise<Response> => { throw new Error("Pair a connection first."); };
const unavailableStream = async () => { throw new Error("Pair a connection first."); };

export function AppSessionProvider({ children }: { children: React.ReactNode }) {
    const [status, setStatus] = React.useState<SessionStatus>("booting");
    const [active, setActive] = React.useState<ActiveSession | null>(null);
    const activeRef = React.useRef<ActiveSession | null>(null);
    const activationSeq = React.useRef(0);
    const [baseUrl, setBaseUrl] = React.useState("");
    const [connectionError, setConnectionError] = React.useState("");
    const [userAvatarUri, setUserAvatarUri] = React.useState("");
    const [userBackgroundUri, setUserBackgroundUri] = React.useState("");
    const [sessionActivityVersion, setSessionActivityVersion] = React.useState(0);
    const [engineClockOffsetMs, setEngineClockOffsetMs] = React.useState(0);
    const [foreground, setForeground] = React.useState(AppState.currentState !== "background" && AppState.currentState !== "inactive");
    const refreshUserInFlight = React.useRef<{ key: string; request: Promise<PhoneUser | null> } | null>(null);

    const publish = React.useCallback((next: ActiveSession | null) => { activeRef.current = next; setActive(next); }, []);
    const activateProfile = React.useCallback(async (profileId: string) => {
        if (activeRef.current?.profile.id === profileId) return;
        const seq = ++activationSeq.current;
        await phoneDrafts.flushAll();
        const profiles = await readAdminConnectionProfiles();
        const profile = profiles.find((item) => item.id === profileId);
        if (!profile) throw new Error("Connection no longer exists.");
        const credentials = await readProfileCredentials(profile);
        if (!credentials) throw new Error("This connection needs pairing again.");
        const instanceId = profile.instanceId || profile.serverId || "";
        if (!instanceId) throw new Error("The saved connection has no verified instance. Pair it again.");
        if (!profile.user?.id) {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 4_000);
            try {
                const response = await fetch(buildAdminApiUrl(profile.adminBaseUrl, "/api/client/auth/me"), {
                    headers: { Authorization: `Bearer ${credentials.accessToken}` }, signal: controller.signal,
                });
                const payload = response.ok ? await parseJsonSafe<{ user: PhoneUser }>(response) : null;
                if (!payload?.user?.id) throw new Error("This connection needs pairing again.");
                profile.user = payload.user;
                profile.principalId = payload.user.id;
                await writeAdminConnectionProfiles(profiles);
            } finally { clearTimeout(timer); }
        }
        const authorityKey = phoneAuthorityKey({ instanceId, principalId: profile.user.id, profileId });
        const rawView = await readMetadata(viewKey(authorityKey));
        const view = rawView ? JSON.parse(rawView) as { conversationId?: string; draftId?: string } : {};
        if (seq !== activationSeq.current) throw abortError();
        let transport: PhoneTransport;
        transport = new PhoneTransport({
            endpoints: orderAdminBaseUrlCandidates({ primary: profile.adminBaseUrl, adminUrls: profile.adminUrls,
                lanUrls: profile.lanUrls, tailscaleUrls: profile.tailscaleUrls, cloudflareUrls: profile.cloudflareUrls, endpoints: profile.endpoints }),
            credentials, principalId: profile.user.id, native: Platform.OS !== "web",
            persistRefresh: async (nextCredentials, user) => {
                if (activeRef.current?.transport !== transport) throw abortError();
                const latest = await readAdminConnectionProfiles();
                const own = latest.find((item) => item.id === profileId);
                if (!own) throw abortError();
                Object.assign(own, nextCredentials, { user, principalId: user.id });
                await writeAdminConnectionProfiles(latest);
                if (activeRef.current?.transport !== transport) throw abortError();
                publish({ ...activeRef.current, profile: own, credentials: nextCredentials });
            },
            onEndpoint: (endpoint) => {
                if (activeRef.current?.transport !== transport) return;
                setBaseUrl(endpoint);
            },
            onClock: (value) => {
                if (activeRef.current?.transport !== transport) return;
                const offset = toEngineClockOffsetMs(value);
                if (offset !== null) setEngineClockOffsetMs((previous) => Math.abs(previous - offset) < 500 ? previous : offset);
            },
        });
        try {
            await writeActiveAdminConnectionProfileId(profileId);
            if (seq !== activationSeq.current) throw abortError();
        } catch (error) { transport.dispose(); throw error; }
        activeRef.current?.transport.dispose();
        const next: ActiveSession = { profile, credentials, authorityKey, transport,
            conversationId: view.conversationId || null, draftId: view.draftId || newDraftId() };
        publish(next);
        setBaseUrl(profile.adminBaseUrl);
        setConnectionError("");
        setEngineClockOffsetMs(0);
        setStatus("authenticated");
    }, [publish]);

    React.useEffect(() => {
        let cancelled = false;
        void (async () => {
            try {
                const profiles = await readAdminConnectionProfiles();
                const id = await readActiveAdminConnectionProfileId();
                const profile = profiles.find((item) => item.id === id);
                if (cancelled) return;
                if (profile?.credentialRef) await activateProfile(profile.id);
                else { setBaseUrl(await getStoredValue("adminBaseUrl") || ""); setStatus("anonymous"); }
            } catch (error) {
                if (!cancelled) { setConnectionError(error instanceof Error ? error.message : "Connection could not be restored."); setStatus("anonymous"); }
            }
        })();
        return () => { cancelled = true; activeRef.current?.transport.dispose(); };
    }, [activateProfile]);

    React.useEffect(() => {
        const subscription = AppState.addEventListener("change", (state) => {
            const visible = state === "active";
            setForeground(visible);
            if (!visible) {
                activeRef.current?.transport.stopStreams();
                void phoneDrafts.flushAll().catch((error) => setConnectionError(error.message));
            }
        });
        return () => subscription.remove();
    }, []);

    const saveView = React.useCallback(async (conversationId: string | null, draftId?: string) => {
        const current = activeRef.current;
        if (!current) return;
        if (current.conversationId === conversationId && !draftId) return;
        await phoneDrafts.flushAll();
        if (activeRef.current?.transport !== current.transport) throw abortError();
        const next = { ...current, conversationId, draftId: draftId || current.draftId };
        await writeMetadata(viewKey(current.authorityKey), JSON.stringify({ conversationId, draftId: next.draftId }));
        if (activeRef.current?.transport !== current.transport) throw abortError();
        publish(next);
    }, [publish]);
    const setActiveConversationId = React.useCallback((next: string | null) => saveView(next), [saveView]);
    const createNewDraft = React.useCallback(() => saveView(null, newDraftId()), [saveView]);

    const pairDevice = React.useCallback(async (input: DevicePairingInput) => {
        const pairing = parseDevicePairingUri(input.pairingUri);
        const payload = await consumeDevicePairing({ ...input, deviceName: input.deviceName || `v8-phone-${Platform.OS}` });
        const profiles = await readAdminConnectionProfiles();
        const { profile, profiles: next } = upsertAdminConnectionProfile(profiles, {
            adminBaseUrl: payload.adminBaseUrl || pairing.adminBaseUrl,
            instanceId: payload.instanceId || pairing.instanceId, serverId: payload.serverId || pairing.serverId,
            adminUrls: payload.adminUrls || pairing.adminUrls,
            lanUrls: payload.pairingManifest?.lanUrls || pairing.lanUrls,
            tailscaleUrls: payload.pairingManifest?.tailscaleUrls || pairing.tailscaleUrls,
            cloudflareUrls: payload.pairingManifest?.cloudflareUrls || pairing.cloudflareUrls,
            endpoints: payload.pairingManifest?.endpoints || pairing.endpoints,
            accessToken: payload.accessToken, refreshToken: payload.refreshToken, user: payload.user,
        });
        if (!profile) throw new Error("Pairing did not return a valid connection.");
        await writeAdminConnectionProfiles(next);
        await activateProfile(profile.id);
    }, [activateProfile]);

    const signOut = React.useCallback(async () => {
        const current = activeRef.current;
        await phoneDrafts.flushAll();
        if (current) {
            // Only this pairing is revoked; other profiles and all drafts survive.
            await current.transport.authorizedFetch("/api/client/auth/logout", { method: "POST",
                headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refreshToken: current.credentials.refreshToken }) });
            await forgetProfileCredentials(current.profile);
            const profiles = await readAdminConnectionProfiles();
            const own = profiles.find((profile) => profile.id === current.profile.id);
            if (own) { delete own.credentialRef; await writeAdminConnectionProfiles(profiles); }
        }
        await writeActiveAdminConnectionProfileId(null);
        await clearSessionStorage();
        activationSeq.current += 1;
        current?.transport.dispose();
        publish(null);
        setStatus("anonymous");
    }, [publish]);

    const refreshUser = React.useCallback(async () => {
        const current = activeRef.current;
        if (!current) return null;
        if (refreshUserInFlight.current?.key === current.authorityKey) return refreshUserInFlight.current.request;
        const request = (async () => {
            const response = await current.transport.authorizedFetch("/api/client/auth/me");
            if (!response.ok) throw new Error("This connection needs pairing again.");
            const payload = await parseJsonSafe<{ user: PhoneUser }>(response);
            if (activeRef.current?.transport !== current.transport) throw abortError();
            if (!payload?.user || payload.user.id !== current.profile.user?.id) throw new Error("The paired account changed. Pair this connection again.");
            if (JSON.stringify(payload.user) !== JSON.stringify(activeRef.current.profile.user)) {
                const profiles = await readAdminConnectionProfiles();
                const own = profiles.find((profile) => profile.id === current.profile.id);
                if (own) { own.user = payload.user; await writeAdminConnectionProfiles(profiles); }
                if (activeRef.current?.transport === current.transport) publish({ ...activeRef.current, profile: { ...activeRef.current.profile, user: payload.user } });
            }
            setConnectionError("");
            return payload.user;
        })();
        refreshUserInFlight.current = { key: current.authorityKey, request };
        try { return await request; } finally { if (refreshUserInFlight.current?.request === request) refreshUserInFlight.current = null; }
    }, [publish]);
    React.useEffect(() => {
        if (!active?.authorityKey || !foreground) return;
        let cancelled = false;
        void refreshUser().catch((error) => { if (!cancelled) setConnectionError(error.message); });
        return () => { cancelled = true; };
    }, [active?.authorityKey, foreground, refreshUser]);

    const updateCurrentUser = React.useCallback(async (user: PhoneUser | null) => {
        const current = activeRef.current;
        if (!current || !user || user.id !== current.profile.user?.id) return;
        const profiles = await readAdminConnectionProfiles();
        const own = profiles.find((profile) => profile.id === current.profile.id);
        if (!own) return;
        own.user = user;
        await writeAdminConnectionProfiles(profiles);
        if (activeRef.current?.transport === current.transport) publish({ ...activeRef.current, profile: own });
    }, [publish]);

    const user = active?.profile.user || null;
    const media = user?.appearance?.lightBackgroundMedia || user?.appearance?.lightBackgroundImage || "";
    const userBackgroundMediaType = user?.appearance?.lightBackgroundMediaType === "video" || media.toLowerCase().endsWith(".mp4") ? "video" : "image";
    React.useEffect(() => {
        let cancelled = false;
        setUserAvatarUri(""); setUserBackgroundUri("");
        if (!active) return;
        const avatar = resolveAdminAssetUrl(baseUrl, user?.image || "");
        const background = user?.appearance?.lightBackgroundEnabled ? resolveAdminAssetUrl(baseUrl, media) : "";
        if (avatar) void cacheProfileAvatar(avatar, active.authorityKey).then((uri) => { if (!cancelled) setUserAvatarUri(uri); }).catch(() => undefined);
        if (background) void cacheProfileBackground(background, userBackgroundMediaType, active.authorityKey).then((uri) => { if (!cancelled) setUserBackgroundUri(uri); }).catch(() => undefined);
        return () => { cancelled = true; };
    }, [active?.authorityKey, baseUrl, user?.image, user?.appearance?.lightBackgroundEnabled, media, userBackgroundMediaType]);

    React.useEffect(() => {
        if (!active || !foreground) return;
        let stopped = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const controller = new AbortController();
        const run = async () => {
            let failures = 0;
            while (!stopped) {
                const started = Date.now();
                try {
                    await active.transport.authorizedRealtimeStream("/api/client/realtime/session-activity/stream", () => {
                        if (timer || stopped) return;
                        timer = setTimeout(() => { timer = undefined; if (!stopped) setSessionActivityVersion((value) => value + 1); }, 180);
                    }, controller.signal);
                    if (Date.now() - started > 10_000) failures = 0;
                } catch { if (controller.signal.aborted) break; }
                if (stopped) break;
                await new Promise<void>((resolve) => {
                    const wait = setTimeout(done, Math.min(8_000, 500 * 2 ** Math.min(4, failures++)) + Math.random() * 250);
                    function done() { clearTimeout(wait); controller.signal.removeEventListener("abort", done); resolve(); }
                    controller.signal.addEventListener("abort", done, { once: true });
                });
            }
        };
        void run();
        return () => { stopped = true; controller.abort(); if (timer) clearTimeout(timer); };
    }, [active?.transport, foreground]);

    const setAdminBaseUrl = React.useCallback(async (next: string) => {
        // Manual input belongs to a new pairing; it never redirects a live token.
        const normalized = normalizeAdminBaseUrl(next);
        if (activeRef.current) throw new Error("Use saved connections to switch, or pair another device.");
        await writeMetadata("v8.phone.adminBaseUrl", normalized);
        setBaseUrl(normalized);
    }, []);
    const getEngineNowMs = React.useCallback(() => resolveEngineNowMs(engineClockOffsetMs), [engineClockOffsetMs]);
    const value = React.useMemo<SessionContextValue>(() => ({ status, user, userAvatarUri, userBackgroundUri, userBackgroundMediaType,
        adminBaseUrl: baseUrl, accessToken: active?.credentials.accessToken || "", authorityKey: active?.authorityKey || "",
        servingInstanceId: active?.profile.instanceId || active?.profile.serverId || "", activeProfileId: active?.profile.id || "",
        activeConversationId: active?.conversationId || null, newDraftId: active?.draftId || "unpaired", sessionActivityVersion,
        connectionError, setAdminBaseUrl, setActiveConversationId, createNewDraft, activateProfile, pairDevice, signOut, refreshUser, updateCurrentUser,
        authorizedFetch: active?.transport.authorizedFetch || unavailableFetch,
        authorizedRealtimeStream: active?.transport.authorizedRealtimeStream || unavailableStream,
        engineClockOffsetMs, getEngineNowMs,
    }), [status, active, user, userAvatarUri, userBackgroundUri, userBackgroundMediaType, baseUrl, sessionActivityVersion, connectionError,
        setAdminBaseUrl, setActiveConversationId, createNewDraft, activateProfile, pairDevice, signOut, refreshUser, updateCurrentUser, engineClockOffsetMs, getEngineNowMs]);
    return <SessionContext.Provider value={value}>
        {connectionError ? <Pressable accessibilityRole="button" onPress={() => { void refreshUser().catch((error) => setConnectionError(error.message)); }}>
            <Text style={{ color: "#B45309", paddingHorizontal: 16, paddingVertical: 8 }}>{connectionError}</Text>
        </Pressable> : null}
        <View key={active?.authorityKey || "unpaired"} style={{ flex: 1 }}>{children}</View>
    </SessionContext.Provider>;
}

export function useAppSession() {
    const context = React.useContext(SessionContext);
    if (!context) throw new Error("useAppSession must be used within AppSessionProvider");
    return context;
}
