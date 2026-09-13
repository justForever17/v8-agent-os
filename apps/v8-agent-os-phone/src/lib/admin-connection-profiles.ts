import { normalizeAdminBaseUrl } from "@/src/lib/admin-client";
import { getStoredValue, readMetadata, writeMetadata, readSecureItem, writeSecureItem, deleteSecureItem } from "@/src/lib/mobile-storage";
import type { ConnectionSummary, DeviceConnectionEndpoint, PhoneUser } from "@/src/types/admin";

export type AdminConnectionProfile = {
    id: string;
    label: string;
    serverId?: string;
    instanceId?: string;
    ownerDeviceId?: string;
    deviceId?: string;
    adminBaseUrl: string;
    adminUrls?: string[];
    lanUrls?: string[];
    tailscaleUrls?: string[];
    cloudflareUrls?: string[];
    endpoints?: DeviceConnectionEndpoint[];
    adminApiBaseUrl?: string;
    bridgeMode?: string;
    transportKind?: string;
    transportProfileId?: string;
    reachable?: boolean;
    version?: string;
    accessToken?: string;
    refreshToken?: string;
    credentialRef?: string;
    user?: PhoneUser;
    principalId?: string;
    lastUsedAt: string;
};

function createProfileId() {
    const cryptoApi = (globalThis as typeof globalThis & { crypto?: { randomUUID?: () => string } }).crypto;
    if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
        return cryptoApi.randomUUID();
    }
    return `admin-connection-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function deriveLabel(adminBaseUrl: string) {
    try {
        const url = new URL(adminBaseUrl);
        return url.host || adminBaseUrl;
    } catch {
        return adminBaseUrl.replace(/^https?:\/\//i, "") || adminBaseUrl;
    }
}

function sanitizeStringArray(value: unknown) {
    if (!Array.isArray(value)) {
        return [] as string[];
    }
    return value
        .map((item) => normalizeAdminBaseUrl(String(item || "")))
        .filter((item, index, all) => Boolean(item) && all.indexOf(item) === index);
}

function isTailscaleHost(hostname: string) {
    return /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(hostname) || /\.ts\.net$/i.test(hostname);
}

function normalizeHostname(hostname: string) {
    return String(hostname || "").trim().replace(/^\[/, "").replace(/\]$/, "").toLowerCase();
}

function isLanHost(hostname: string) {
    const normalized = normalizeHostname(hostname);
    return /^10\./.test(normalized)
        || /^192\.168\./.test(normalized)
        || /^172\.(1[6-9]|2\d|3[01])\./.test(normalized);
}

function isIpv6Host(hostname: string) {
    return normalizeHostname(hostname).includes(":");
}

function normalizeEndpointKind(value: unknown): DeviceConnectionEndpoint["kind"] {
    const normalized = String(value || "").trim().toLowerCase().replace(/-/g, "_");
    if (["lan", "lan_ipv6", "wireguard", "tailscale", "headscale", "cloudflare_tunnel", "custom_vpn", "manual_url"].includes(normalized)) {
        return normalized as DeviceConnectionEndpoint["kind"];
    }
    return "manual_url";
}

function classifyAdminUrl(value: string) {
    try {
        const url = new URL(value);
        const hostname = normalizeHostname(url.hostname || "");
        if (isTailscaleHost(hostname)) return "tailscale";
        if (isLanHost(hostname)) return "lan";
        if (isIpv6Host(hostname) && hostname !== "::1") return "lan_ipv6";
    } catch {
        // Fall through to manual.
    }
    return "manual_url";
}

function sanitizeConnectionEndpoint(value: unknown, fallbackKind?: DeviceConnectionEndpoint["kind"]): DeviceConnectionEndpoint | null {
    if (!value || typeof value !== "object") return null;
    const record = value as Record<string, unknown>;
    const baseUrl = normalizeAdminBaseUrl(String(record.baseUrl || record.adminBaseUrl || ""));
    if (!baseUrl) return null;
    const inferredKind = classifyAdminUrl(baseUrl) as DeviceConnectionEndpoint["kind"];
    const kind = normalizeEndpointKind(record.kind || fallbackKind || inferredKind);
    const priorityValue = Number(record.priority);
    return {
        id: String(record.id || `${kind}:${baseUrl}`),
        kind,
        baseUrl,
        scope: record.scope === "local" || record.scope === "remote"
            ? record.scope
            : kind === "lan" || kind === "lan_ipv6" ? "local" : "remote",
        priority: Number.isFinite(priorityValue) ? priorityValue : undefined,
        enabled: record.enabled !== false,
    };
}

function endpointRank(endpoint: DeviceConnectionEndpoint) {
    if (typeof endpoint.priority === "number" && Number.isFinite(endpoint.priority)) return endpoint.priority;
    if (endpoint.kind === "lan" || endpoint.kind === "lan_ipv6") return 10;
    if (endpoint.kind === "wireguard") return 20;
    if (endpoint.kind === "tailscale" || endpoint.kind === "headscale") return 30;
    if (endpoint.kind === "cloudflare_tunnel") return 40;
    if (endpoint.kind === "custom_vpn") return 50;
    return 60;
}

export function sanitizeConnectionEndpoints(value: unknown) {
    if (!Array.isArray(value)) return [] as DeviceConnectionEndpoint[];
    const seen = new Set<string>();
    return value
        .map((item) => sanitizeConnectionEndpoint(item))
        .filter((item): item is DeviceConnectionEndpoint => Boolean(item && item.enabled !== false && item.baseUrl))
        .filter((item) => {
            const key = normalizeAdminBaseUrl(item.baseUrl || "");
            if (!key || seen.has(key)) return false;
            seen.add(key);
            return true;
        });
}

export function orderAdminBaseUrlCandidates(input: {
    primary?: string | null;
    adminUrls?: string[] | null;
    lanUrls?: string[] | null;
    tailscaleUrls?: string[] | null;
    cloudflareUrls?: string[] | null;
    endpoints?: DeviceConnectionEndpoint[] | null;
    preferPrimary?: boolean;
}) {
    const endpointInputs: DeviceConnectionEndpoint[] = [
        ...(input.endpoints || []),
        ...(input.lanUrls || []).map((baseUrl) => ({ baseUrl, kind: classifyAdminUrl(baseUrl) === "lan_ipv6" ? "lan_ipv6" as const : "lan" as const, scope: "local" as const })),
        ...(input.tailscaleUrls || []).map((baseUrl) => ({ baseUrl, kind: "tailscale" as const, scope: "remote" as const })),
        ...(input.cloudflareUrls || []).map((baseUrl) => ({ baseUrl, kind: "cloudflare_tunnel" as const, scope: "remote" as const })),
        ...(input.adminUrls || []).map((baseUrl) => ({ baseUrl, kind: classifyAdminUrl(baseUrl) as DeviceConnectionEndpoint["kind"] })),
    ];
    const primary = normalizeAdminBaseUrl(input.primary || "");
    if (primary) {
        const matchingEndpoint = endpointInputs.find((item) => normalizeAdminBaseUrl(item.baseUrl || "") === primary);
        const primaryEndpoint: DeviceConnectionEndpoint = {
            id: `primary:${primary}`,
            baseUrl: primary,
            kind: matchingEndpoint?.kind || classifyAdminUrl(primary) as DeviceConnectionEndpoint["kind"],
            scope: matchingEndpoint?.scope,
            priority: input.preferPrimary === false ? undefined : -100,
        };
        if (input.preferPrimary === false) endpointInputs.push(primaryEndpoint);
        else endpointInputs.unshift(primaryEndpoint);
    }
    return sanitizeConnectionEndpoints(endpointInputs)
        .sort((left, right) => endpointRank(left) - endpointRank(right))
        .map((item) => normalizeAdminBaseUrl(item.baseUrl || ""));
}

function sanitizeProfile(value: unknown): AdminConnectionProfile | null {
    if (!value || typeof value !== "object") {
        return null;
    }
    const record = value as Record<string, unknown>;
    const adminBaseUrl = normalizeAdminBaseUrl(String(record.adminBaseUrl || ""));
    if (!adminBaseUrl) {
        return null;
    }
    return {
        id: String(record.id || createProfileId()),
        label: String(record.label || deriveLabel(adminBaseUrl)),
        serverId: typeof record.serverId === "string" ? record.serverId : "",
        instanceId: typeof record.instanceId === "string" ? record.instanceId : "",
        ownerDeviceId: typeof record.ownerDeviceId === "string" ? record.ownerDeviceId : "",
        deviceId: typeof record.deviceId === "string" ? record.deviceId : "",
        adminBaseUrl,
        adminUrls: sanitizeStringArray(record.adminUrls),
        lanUrls: sanitizeStringArray(record.lanUrls),
        tailscaleUrls: sanitizeStringArray(record.tailscaleUrls),
        cloudflareUrls: sanitizeStringArray(record.cloudflareUrls),
        endpoints: sanitizeConnectionEndpoints(record.endpoints),
        adminApiBaseUrl: typeof record.adminApiBaseUrl === "string" ? record.adminApiBaseUrl : "",
        bridgeMode: typeof record.bridgeMode === "string" ? record.bridgeMode : "",
        transportKind: typeof record.transportKind === "string" ? record.transportKind : "",
        transportProfileId: typeof record.transportProfileId === "string" ? record.transportProfileId : "",
        reachable: typeof record.reachable === "boolean" ? record.reachable : undefined,
        version: typeof record.version === "string" ? record.version : "",
        accessToken: typeof record.accessToken === "string" ? record.accessToken : "",
        refreshToken: typeof record.refreshToken === "string" ? record.refreshToken : "",
        credentialRef: typeof record.credentialRef === "string" ? record.credentialRef : undefined,
        user: record.user && typeof record.user === "object" ? record.user as PhoneUser : undefined,
        principalId: typeof record.principalId === "string" ? record.principalId : undefined,
        lastUsedAt: String(record.lastUsedAt || new Date(0).toISOString()),
    };
}

let migration: Promise<AdminConnectionProfile[]> | null = null;
async function readProfiles() {
    const current = await readMetadata(PROFILES_KEY);
    const raw = current ?? await getStoredValue("adminConnectionProfiles");
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) throw new Error("Connection directory is damaged; saved data has been preserved.");
    const profiles = parsed.map(sanitizeProfile).filter((item): item is AdminConnectionProfile => Boolean(item));
    if (current === null && profiles.length) {
        // Only the legacy active profile can prove ownership of global user / view.
        const activeId = await readActiveAdminConnectionProfileId();
        const oldUser = await getStoredValue("user");
        const active = profiles.find((profile) => profile.id === activeId);
        if (active && oldUser) {
            active.user = JSON.parse(oldUser) as PhoneUser;
            active.principalId = active.user.id;
        }
        await writeAdminConnectionProfiles(profiles);
        return readProfiles();
    }
    return profiles.sort((left, right) => right.lastUsedAt.localeCompare(left.lastUsedAt));
}

export async function readAdminConnectionProfiles() {
    if (migration) return migration;
    const operation = readProfiles();
    migration = operation;
    try { return await operation; } finally { if (migration === operation) migration = null; }
}

const PROFILES_KEY = "v8.phone.profiles.v2";
export type ProfileCredentials = { accessToken: string; refreshToken: string };
export async function readProfileCredentials(profile: AdminConnectionProfile): Promise<ProfileCredentials | null> {
    if (!profile.credentialRef) return null;
    const raw = await readSecureItem(profile.credentialRef);
    if (!raw) return null;
    const value = JSON.parse(raw) as ProfileCredentials;
    return value.accessToken && value.refreshToken ? value : null;
}

let directoryMutations: Promise<unknown> = Promise.resolve();
export function updateAdminConnectionProfiles(update: (current: AdminConnectionProfile[]) => AdminConnectionProfile[]) {
    const transaction = directoryMutations.catch(() => undefined).then(async () => {
        const current = await readAdminConnectionProfiles();
        const next = update(current);
        const activeId = await readActiveAdminConnectionProfileId();
        if (activeId && current.some((item) => item.id === activeId) && !next.some((item) => item.id === activeId)) {
            throw new Error("Switch away or sign out before removing the active connection.");
        }
        await writeAdminConnectionProfiles(next);
        return next;
    });
    directoryMutations = transaction;
    return transaction;
}

export function commitActiveAdminConnectionProfile(profileId: string, expectedCredentialRef: string | undefined, publish: () => void) {
    const transaction = directoryMutations.catch(() => undefined).then(async () => {
        const current = await readAdminConnectionProfiles();
        const profile = current.find((item) => item.id === profileId);
        if (!profile?.credentialRef || profile.credentialRef !== expectedCredentialRef) {
            throw new Error("Connection changed while switching. Retry this connection.");
        }
        await writeActiveAdminConnectionProfileId(profileId);
        // No await between pointer commit and in-memory activation. Directory
        // removal cannot interleave after validation and before publication.
        publish();
    });
    directoryMutations = transaction;
    return transaction;
}

/** Low-level migration writer. Production mutations must use updateAdminConnectionProfiles. */
async function writeAdminConnectionProfiles(profiles: AdminConnectionProfile[]) {
    const previousRaw = await readMetadata(PROFILES_KEY);
    const previous: AdminConnectionProfile[] = previousRaw ? JSON.parse(previousRaw) : [];
    const metadata: AdminConnectionProfile[] = [];
    const createdRefs: string[] = [];
    const pendingKey = "v8.phone.retiredCredentials.v2";
    const pending: string[] = JSON.parse(await readMetadata(pendingKey) || "[]");
    try {
        for (const profile of profiles) {
            const { accessToken, refreshToken, ...item } = profile;
            if (accessToken && refreshToken) {
                // New slot makes a failed directory write leave the previous credentials intact.
                const credentialRef = `v8.phone.credential.${createProfileId()}`;
                await writeSecureItem(credentialRef, JSON.stringify({ accessToken, refreshToken }));
                createdRefs.push(credentialRef);
                item.credentialRef = credentialRef;
            }
            metadata.push(item);
        }
        // Recovery ledger precedes publication. After an interrupted transaction,
        // only slots absent from the committed directory can be collected.
        await writeMetadata(pendingKey, JSON.stringify([...new Set([...pending, ...createdRefs,
            ...previous.map((item) => item.credentialRef).filter(Boolean)])]));
        await writeMetadata(PROFILES_KEY, JSON.stringify(metadata));
    } catch (error) {
        for (const ref of createdRefs) await deleteSecureItem(ref).catch(() => undefined);
        throw error;
    }
    // Keep the returned objects free of secrets and ready for subsequent metadata updates.
    profiles.forEach((profile, index) => {
        profile.credentialRef = metadata[index].credentialRef;
        delete profile.accessToken;
        delete profile.refreshToken;
    });
    const retained = new Set(metadata.map((profile) => profile.credentialRef).filter(Boolean));
    const retired = previous.map((profile) => profile.credentialRef).filter((ref): ref is string => Boolean(ref && !retained.has(ref)));
    const cleanup = [...new Set([...pending, ...retired, ...createdRefs])].filter((ref) => !retained.has(ref));
    await writeMetadata(pendingKey, JSON.stringify(cleanup));
    for (const ref of cleanup) await deleteSecureItem(ref);
    await writeMetadata(pendingKey, "[]");
}

export async function readActiveAdminConnectionProfileId() {
    return getStoredValue("activeAdminConnectionProfileId");
}

export async function writeActiveAdminConnectionProfileId(profileId: string | null | undefined) {
    await writeMetadata("v8.phone.activeAdminConnectionProfileId", profileId ? String(profileId) : "");
}

export function upsertAdminConnectionProfile(
    profiles: AdminConnectionProfile[],
    input: {
        adminBaseUrl: string;
        label?: string | null;
        profileId?: string | null;
        summary?: ConnectionSummary | null;
        serverId?: string | null;
        instanceId?: string | null;
        ownerDeviceId?: string | null;
        deviceId?: string | null;
        adminUrls?: string[] | null;
        lanUrls?: string[] | null;
        tailscaleUrls?: string[] | null;
        cloudflareUrls?: string[] | null;
        endpoints?: DeviceConnectionEndpoint[] | null;
        accessToken?: string | null;
        refreshToken?: string | null;
        user?: PhoneUser;
    },
) {
    const adminBaseUrl = normalizeAdminBaseUrl(input.adminBaseUrl);
    if (!adminBaseUrl) {
        return { profile: null, profiles };
    }
    const summaryConnection = input.summary?.connection || {};
    const existing = input.profileId ? profiles.find((profile) => profile.id === input.profileId) : undefined;
    const summaryRecord = (input.summary || {}) as Record<string, unknown>;
    const summaryConnectionRecord = summaryConnection as Record<string, unknown>;
    const version = typeof summaryRecord.version === "string"
        ? summaryRecord.version
        : typeof summaryConnectionRecord.version === "string"
            ? summaryConnectionRecord.version
            : existing?.version || "";
    const profile: AdminConnectionProfile = {
        id: existing?.id || input.profileId || createProfileId(),
        label: input.label || existing?.label || deriveLabel(adminBaseUrl),
        serverId: input.serverId || existing?.serverId || "",
        instanceId: input.instanceId || existing?.instanceId || "",
        ownerDeviceId: input.ownerDeviceId || existing?.ownerDeviceId || "",
        deviceId: input.deviceId || existing?.deviceId || "",
        adminBaseUrl,
        adminUrls: orderAdminBaseUrlCandidates({
            primary: adminBaseUrl,
            adminUrls: [
                ...(input.adminUrls || []),
                ...(existing?.adminUrls || []),
                ...((input.summary?.linkManifest?.profiles || []).map((item) => item.adminBaseUrl || "")),
                input.summary?.linkManifest?.admin?.baseUrl || "",
            ],
            endpoints: [
                ...(input.endpoints || []),
                ...(existing?.endpoints || []),
                ...((input.summary?.linkManifest?.endpoints || [])),
            ],
        }),
        lanUrls: sanitizeStringArray([...(input.lanUrls || []), ...(existing?.lanUrls || [])]),
        tailscaleUrls: sanitizeStringArray([...(input.tailscaleUrls || []), ...(existing?.tailscaleUrls || [])]),
        cloudflareUrls: sanitizeStringArray([...(input.cloudflareUrls || []), ...(existing?.cloudflareUrls || [])]),
        endpoints: sanitizeConnectionEndpoints([
            ...(input.endpoints || []),
            ...(existing?.endpoints || []),
            ...((input.summary?.linkManifest?.endpoints || [])),
        ]),
        adminApiBaseUrl: summaryConnection.adminApiBaseUrl || summaryConnection.configuredAdminApiBaseUrl || existing?.adminApiBaseUrl || "",
        bridgeMode: summaryConnection.bridgeMode || existing?.bridgeMode || "",
        transportKind: summaryConnection.transportKind || existing?.transportKind || "",
        transportProfileId: summaryConnection.transportProfileId || existing?.transportProfileId || "",
        reachable: typeof summaryConnection.reachable === "boolean" ? summaryConnection.reachable : existing?.reachable,
        version,
        accessToken: input.accessToken || existing?.accessToken || "",
        refreshToken: input.refreshToken || existing?.refreshToken || "",
        credentialRef: existing?.credentialRef,
        user: input.user || existing?.user,
        principalId: input.user?.id || existing?.principalId,
        lastUsedAt: new Date().toISOString(),
    };
    const nextProfiles = [
        profile,
        ...profiles.filter((item) => item.id !== profile.id),
    ].sort((left, right) => right.lastUsedAt.localeCompare(left.lastUsedAt));
    return { profile, profiles: nextProfiles };
}

export function removeAdminConnectionProfile(profiles: AdminConnectionProfile[], profileId: string) {
    return profiles.filter((profile) => profile.id !== profileId);
}
