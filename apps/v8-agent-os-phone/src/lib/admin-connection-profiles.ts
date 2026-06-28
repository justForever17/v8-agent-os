import { normalizeAdminBaseUrl } from "@/src/lib/admin-client";
import { getStoredValue, setStoredValue } from "@/src/lib/mobile-storage";
import type { ConnectionSummary } from "@/src/types/admin";

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
    adminApiBaseUrl?: string;
    bridgeMode?: string;
    transportKind?: string;
    transportProfileId?: string;
    reachable?: boolean;
    version?: string;
    accessToken?: string;
    refreshToken?: string;
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

function isLanHost(hostname: string) {
    return /^10\./.test(hostname)
        || /^192\.168\./.test(hostname)
        || /^172\.(1[6-9]|2\d|3[01])\./.test(hostname);
}

function classifyAdminUrl(value: string) {
    try {
        const url = new URL(value);
        const hostname = url.hostname || "";
        if (isTailscaleHost(hostname)) return "tailscale";
        if (isLanHost(hostname)) return "lan";
    } catch {
        // Fall through to manual.
    }
    return "manual";
}

export function orderAdminBaseUrlCandidates(input: {
    primary?: string | null;
    adminUrls?: string[] | null;
    lanUrls?: string[] | null;
    tailscaleUrls?: string[] | null;
}) {
    const all = [
        ...(input.tailscaleUrls || []),
        ...(input.lanUrls || []),
        ...(input.adminUrls || []),
        input.primary || "",
    ]
        .map((item) => normalizeAdminBaseUrl(item))
        .filter(Boolean);
    const tailscale: string[] = [];
    const lan: string[] = [];
    const manual: string[] = [];
    for (const url of all) {
        const kind = classifyAdminUrl(url);
        if (kind === "tailscale") {
            tailscale.push(url);
        } else if (kind === "lan") {
            lan.push(url);
        } else {
            manual.push(url);
        }
    }
    return [...tailscale, ...lan, ...manual]
        .filter((item, index, list) => list.indexOf(item) === index);
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
        adminApiBaseUrl: typeof record.adminApiBaseUrl === "string" ? record.adminApiBaseUrl : "",
        bridgeMode: typeof record.bridgeMode === "string" ? record.bridgeMode : "",
        transportKind: typeof record.transportKind === "string" ? record.transportKind : "",
        transportProfileId: typeof record.transportProfileId === "string" ? record.transportProfileId : "",
        reachable: typeof record.reachable === "boolean" ? record.reachable : undefined,
        version: typeof record.version === "string" ? record.version : "",
        accessToken: typeof record.accessToken === "string" ? record.accessToken : "",
        refreshToken: typeof record.refreshToken === "string" ? record.refreshToken : "",
        lastUsedAt: String(record.lastUsedAt || new Date(0).toISOString()),
    };
}

export async function readAdminConnectionProfiles() {
    try {
        const raw = await getStoredValue("adminConnectionProfiles");
        const parsed = raw ? JSON.parse(raw) : [];
        if (!Array.isArray(parsed)) {
            return [] as AdminConnectionProfile[];
        }
        return parsed
            .map((item) => sanitizeProfile(item))
            .filter((item): item is AdminConnectionProfile => Boolean(item))
            .sort((left, right) => right.lastUsedAt.localeCompare(left.lastUsedAt));
    } catch {
        return [] as AdminConnectionProfile[];
    }
}

export async function writeAdminConnectionProfiles(profiles: AdminConnectionProfile[]) {
    await setStoredValue("adminConnectionProfiles", JSON.stringify(profiles));
}

export async function readActiveAdminConnectionProfileId() {
    return getStoredValue("activeAdminConnectionProfileId");
}

export async function writeActiveAdminConnectionProfileId(profileId: string | null | undefined) {
    await setStoredValue("activeAdminConnectionProfileId", profileId ? String(profileId) : "");
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
        accessToken?: string | null;
        refreshToken?: string | null;
    },
) {
    const adminBaseUrl = normalizeAdminBaseUrl(input.adminBaseUrl);
    if (!adminBaseUrl) {
        return { profile: null, profiles };
    }
    const summaryConnection = input.summary?.connection || {};
    const existing = profiles.find((profile) =>
        (input.profileId && profile.id === input.profileId)
        || normalizeAdminBaseUrl(profile.adminBaseUrl) === adminBaseUrl,
    );
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
        }),
        lanUrls: sanitizeStringArray([...(input.lanUrls || []), ...(existing?.lanUrls || [])]),
        tailscaleUrls: sanitizeStringArray([...(input.tailscaleUrls || []), ...(existing?.tailscaleUrls || [])]),
        adminApiBaseUrl: summaryConnection.adminApiBaseUrl || summaryConnection.configuredAdminApiBaseUrl || existing?.adminApiBaseUrl || "",
        bridgeMode: summaryConnection.bridgeMode || existing?.bridgeMode || "",
        transportKind: summaryConnection.transportKind || existing?.transportKind || "",
        transportProfileId: summaryConnection.transportProfileId || existing?.transportProfileId || "",
        reachable: typeof summaryConnection.reachable === "boolean" ? summaryConnection.reachable : existing?.reachable,
        version,
        accessToken: input.accessToken || existing?.accessToken || "",
        refreshToken: input.refreshToken || existing?.refreshToken || "",
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
